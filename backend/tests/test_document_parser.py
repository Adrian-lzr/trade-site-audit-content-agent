from __future__ import annotations

from hashlib import sha256

import pytest

from backend.audit import map_rule_failures_to_findings, summarize_rule_results
from backend.audit_rules import RULE_VERSION, evaluate_snapshot
from backend.document_parser import PARSER_VERSION, normalize_http_url, parse_document


@pytest.mark.parametrize(
    ("name", "html", "expected_title"),
    [
        ("ordinary head title", "<html><head><title>Industrial valve</title></head><body><h1>Valve</h1></body></html>", "Industrial valve"),
        ("entity decoding", "<head><title>Flow &amp; Pressure</title></head><body></body>", "Flow & Pressure"),
        ("inline title nodes", "<head><title>Heavy <b>duty</b> valve</title></head><body></body>", "Heavy duty valve"),
        ("svg title after document title", "<head><title>Correct title</title></head><body><svg><title>Icon label</title></svg></body>", "Correct title"),
        ("svg title only", "<body><svg><title>Icon label</title></svg></body>", None),
        ("body title only", "<html><head></head><body><title>Not document metadata</title></body></html>", None),
        ("body meta excluded", "<head><title>Product</title></head><body><meta name='description' content='not head'><meta name='robots' content='noindex'></body>", "Product"),
        ("multiple head titles choose first", "<head><title>Primary</title><title>Secondary</title></head><body></body>", "Primary"),
        ("malformed title closed by body", "<html><head><title>Primary<body><h1>Body content</h1></body></html>", "Primary"),
        ("headless snippet", "<title>Implied head</title><main>Text</main>", "Implied head"),
        ("product specification table", "<head><title>Valve specifications</title></head><body><table><tr><th>Pressure</th><td>16 bar</td></tr></table></body>", "Valve specifications"),
        ("FAQ content", "<head><title>Valve FAQ</title></head><body><h2 class='faq-question'>What is the pressure?</h2><p>16 bar.</p></body>", "Valve FAQ"),
        ("JavaScript shell", "<html><head><title>Shell</title><script src='/app.js'></script></head><body><div id='root'>Loading</div></body></html>", "Shell"),
        ("JSON-LD is not a render script", "<html><head><script type='application/ld+json'>{}</script></head><body></body></html>", None),
        ("malformed partial document", "<head><title>Partial title<meta name='description' content='summary'><body><h1>Body heading", "Partial title"),
        ("canonical self close", "<head><title>Self closing</title><link rel='canonical' href='/catalog/item' /></head>", "Self closing"),
        ("encoded title whitespace", "<head><title>  Industrial&nbsp; Valve  </title></head>", "Industrial Valve"),
        ("two canonical declarations", "<head><title>Canonical choices</title><link rel='canonical' href='/one'><link rel='canonical' href='/two'></head>", "Canonical choices"),
    ],
)
def test_parser_handles_representative_static_html(name: str, html: str, expected_title: str | None):
    parsed = parse_document(html, requested_url="https://shop.example/request", final_url="https://shop.example/final")

    assert parsed.title == expected_title, name
    assert parsed.source_hash == sha256(html.encode("utf-8")).hexdigest()
    assert len(parsed.metadata_hash) == 64
    assert len(parsed.body_hash) == 64
    assert parsed.requested_url == "https://shop.example/request"
    assert parsed.final_url == "https://shop.example/final"
    assert PARSER_VERSION == "2.0.0"


def test_parser_limits_head_metadata_and_preserves_table_faq_shell_and_render_signal():
    html = (
        "<html><head><title>Catalog</title><meta name='description' content='Head summary'>"
        "<link rel='canonical' href='/products/valve'></head><body>"
        "<svg><title>Badge</title></svg><meta name='description' content='Body summary'>"
        "<table><tr><td>16 bar</td></tr></table><h2 class='faq-question'>Question</h2>"
        "<script src='/app.js'></script></body></html>"
    )
    parsed = parse_document(html, requested_url="https://shop.example/start", final_url="https://shop.example/catalog")

    assert parsed.title_values == ["Catalog"]
    assert parsed.descriptions == ["Head summary"]
    assert parsed.canonical == ["/products/valve"]
    assert parsed.normalized_canonicals == ["https://shop.example/products/valve"]
    assert parsed.canonical_url == "https://shop.example/products/valve"
    assert parsed.table_count == 1
    assert parsed.faq_marker_count == 1
    assert not parsed.requires_render
    assert parsed.source_hash == sha256(html.encode("utf-8")).hexdigest()

    shell = parse_document("<div id='root'>Loading</div><script src='/app.js'></script>")
    assert shell.requires_render


def test_metadata_and_body_hashes_are_independent():
    first = parse_document("<head><title>First</title></head><body><p>Stable body</p></body>")
    second = parse_document("<head><title>Second</title></head><body><p>Stable body</p></body>")
    changed_body = parse_document("<head><title>First</title></head><body><p>Changed body</p></body>")

    assert first.source_hash != second.source_hash
    assert first.metadata_hash != second.metadata_hash
    assert first.body_hash == second.body_hash
    assert first.body_hash != changed_body.body_hash


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("/catalog/../item", "https://example.test/item"),
        ("https://EXAMPLE.test:443/item#section", "https://example.test/item"),
        ("https://example.test/catalog/../item", "https://example.test/item"),
        ("//cdn.example.test/item", "https://cdn.example.test/item"),
        ("javascript:alert(1)", None),
        ("https://user:secret@example.test/item", None),
        ("", None),
    ],
)
def test_canonical_normalization_is_explicit_and_keeps_invalid_values(declared: str, expected: str | None):
    normalized, error = normalize_http_url(declared, base_url="https://example.test/catalog/current")
    assert normalized == expected
    assert (error is None) == (expected is not None)


def _audit(content: str, *, requested_url: str = "https://shop.example/request", final_url: str = "https://shop.example/final", samples=None):
    snapshot = {
        "requested_url": requested_url,
        "final_url": final_url,
        "url": final_url,
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
        "content": content,
        "title": "stale title is deliberately ignored",
    }
    audit_input = {
        "requested_url": requested_url,
        "policy": {
            "site_origin": "https://shop.example",
            "preferred_origin": "https://shop.example",
            "expected_accessible": True,
            "expected_indexable": True,
        },
        "robots": {"url": "https://shop.example/robots.txt", "status_code": 404},
        "sitemap": {"url": "https://shop.example/sitemap.xml", "status_code": 404},
        "sample_pages": samples or [],
        "link_checks": [],
    }
    return evaluate_snapshot(snapshot, audit_input)


def test_relative_canonical_resolves_from_final_url_and_evidence_keeps_all_identities():
    content = "<head><title>Valve</title><link rel='canonical' href='../products/valve#details'></head><body><h1>Valve</h1></body>"
    results = _audit(
        content,
        samples=[
            {
                "url": "https://shop.example/products/valve",
                "requested_url": "https://shop.example/products/valve",
                "final_url": "https://shop.example/products/valve",
                "status_code": 200,
                "title": "Valve product",
            }
        ],
    )
    canonical = next(item for item in results if item["rule_id"] == "canonical_target")

    assert canonical["status"] == "pass"
    assert canonical["evidence"]["requested_url"] == "https://shop.example/request"
    assert canonical["evidence"]["final_url"] == "https://shop.example/final"
    assert canonical["evidence"]["declared_canonical_values"] == ["../products/valve#details"]
    assert canonical["evidence"]["normalized_canonical_url"] == "https://shop.example/products/valve"
    assert canonical["evidence"]["parser_version"] == PARSER_VERSION
    assert len(canonical["evidence"]["parser_metadata_hash"]) == 64
    assert len(canonical["evidence"]["parser_body_hash"]) == 64
    assert {item["version"] for item in results} == {RULE_VERSION}


@pytest.mark.parametrize(
    ("html", "expected_status"),
    [
        ("<head><title>Product</title><link rel='canonical' href='https://other.example/item'></head><body><h1>Product</h1></body>", "fail"),
        ("<head><title>Product</title><link rel='canonical' href='/a'><link rel='canonical' href='/b'></head><body><h1>Product</h1></body>", "fail"),
        ("<head><title>Product</title></head><body><link rel='canonical' href='https://other.example/item'><h1>Product</h1></body>", "not_applicable"),
        ("<head><title>Product</title><link rel='canonical' href='javascript:alert(1)'></head><body><h1>Product</h1></body>", "fail"),
    ],
)
def test_canonical_rule_uses_only_head_metadata_and_rejects_ambiguous_or_invalid_values(html: str, expected_status: str):
    results = _audit(html)
    result = next(item for item in results if item["rule_id"] == "canonical_target")
    assert result["status"] == expected_status


def test_javascript_shell_uses_unknown_for_unrendered_body_assessments():
    results = _audit("<head><script src='/app.js'></script></head><body><div id='root'>Loading</div></body>")
    by_rule = {item["rule_id"]: item for item in results}

    assert by_rule["title_present"]["status"] == "unknown"
    assert by_rule["meta_description"]["status"] == "unknown"
    assert by_rule["heading_structure"]["status"] == "unknown"
    assert by_rule["image_alt"]["status"] == "unknown"
    assert by_rule["jsonld_consistency"]["status"] == "unknown"
    assert all(item["evidence"]["requires_render"] for item in results)


def test_replaying_identical_frozen_snapshot_is_stable():
    html = "<html><head><title>Same</title><link rel='canonical' href='/final'></head><body><h1>Same</h1><table><tr><td>16 bar</td></tr></table></body></html>"
    first = _audit(html)
    second = _audit(html)
    assert first == second


def test_fail_findings_and_summary_share_one_status_mapping():
    results = _audit(
        "<head><link rel='canonical' href='/one'><link rel='canonical' href='/two'></head><body><h1>No title</h1></body>",
        samples=[
            {"url": "https://shop.example/a", "status_code": 200, "title": "Repeated"},
            {"url": "https://shop.example/b", "status_code": 200, "title": "Repeated"},
        ],
    )
    findings = map_rule_failures_to_findings(results)
    summary = summarize_rule_results(results)

    assert summary["problem_count"] == len(findings)
    assert summary["problem_count"] == 2
    assert summary["review_count"] >= 1
    assert all(item["evidence"]["rule_status"] == "fail" for item in findings)
    assert not any(item["evidence"]["rule_id"] == "duplicate_titles" for item in findings)
    assert {item["code"] for item in findings} == {"TITLE_MISSING", "RULE_CANONICAL_TARGET"}
