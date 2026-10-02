from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from backend.publishers import (
    IdempotencyConflictError,
    InvalidPublicationError,
    ManifestError,
    PrepareRequest,
    PublicationConflictError,
    ReviewRequiredError,
    StaticHtmlAdapter,
    UnauthorizedTargetError,
)


HTML = """<!doctype html>
<html><head>
  <title>Base page</title>
  <meta name="description" content="Base description" data-owner="fixture">
</head><body>
  <svg aria-hidden="true"><title>Icon title must stay</title></svg>
  <main data-publisher-field="body"><p>Original <strong>body</strong>.</p></main>
  <section data-publisher-field="faq"><div class="faq-item"><h3>Old question</h3><p>Old answer</p></div></section>
  <footer data-keep="yes">Unrelated content stays byte-for-byte.</footer>
</body></html>
"""


def _repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "site"
    repo.mkdir()
    page = repo / "index.html"
    page.write_text(HTML, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=repo, check=True)
    subprocess.run(["git", "add", "index.html"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-q", "-m", "fixture"],
        cwd=repo,
        check=True,
    )
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()
    return repo, page, commit


def _adapter(repo: Path) -> StaticHtmlAdapter:
    return StaticHtmlAdapter(
        {
            "sites": {
                "fixture": {
                    "repository": str(repo),
                    "pages": {
                        "home": {
                            "file": "index.html",
                            "fields": {
                                "title": {"kind": "title"},
                                "meta_description": {"kind": "meta", "name": "description"},
                                "body": {"kind": "marker", "marker": "body", "tag": "main"},
                                "faq": {"kind": "marker", "marker": "faq", "tag": "section"},
                            },
                        }
                    },
                }
            }
        }
    )


def _request(page: Path, commit: str, **overrides):
    values = {
        "site": "fixture",
        "page": "home",
        "fields": {
            "title": "Updated page title",
            "meta_description": "Updated description",
            "body": '<p>Updated <a href="/quote">buyer details</a>.</p>',
            "faq": [{"question": "What is the MOQ?", "answer": "The answer is <strong>20 pieces</strong>."}],
        },
        "expected_base_content_hash": hashlib.sha256(page.read_bytes()).hexdigest(),
        "expected_base_commit": commit,
        "revision_hash": hashlib.sha256(b"revision-1").hexdigest(),
        "current_facts": [{"fact_id": 7, "version": 2, "predicate": "minimum_order_quantity"}],
        "idempotency_key": "fixture-home-revision-1",
        "provenance": {"is_synthetic": True, "source": "fixture"},
    }
    values.update(overrides)
    return PrepareRequest(**values)


def test_static_adapter_changes_only_allowlisted_dom_fields_and_rolls_back(tmp_path: Path):
    repo, page, commit = _repo(tmp_path)
    adapter = _adapter(repo)
    before = page.read_text(encoding="utf-8")
    prepared = adapter.prepare(_request(page, commit))

    assert page.read_text(encoding="utf-8") == before
    assert "-  <title>Base page</title>" in prepared.diff
    assert "+  <title>Updated page title</title>" in prepared.diff
    assert prepared.provenance["source"] == "fixture"
    assert prepared.current_fact_set_hash

    applied = adapter.apply(prepared, prepared.approval(approver="reviewer", approval_id="approval-1"))
    changed = page.read_text(encoding="utf-8")
    assert applied.status == "applied"
    assert "<title>Updated page title</title>" in changed
    assert 'content="Updated description"' in changed
    assert "Icon title must stay" in changed
    assert "Unrelated content stays byte-for-byte." in changed
    assert "buyer details" in changed
    assert "What is the MOQ?" in changed
    assert adapter.inspect(prepared).verified

    replay = adapter.apply(prepared, prepared.approval(approver="another-reviewer"))
    assert replay.replayed is True
    assert replay.changed is False
    assert page.read_text(encoding="utf-8") == changed

    rolled_back = adapter.rollback(applied, reason="fixture verification")
    assert rolled_back.status == "rolled_back"
    assert page.read_text(encoding="utf-8") == before
    assert adapter.inspect(prepared).status == "mismatch"


def test_static_adapter_rejects_unsafe_content_unknown_fields_and_approval_drift(tmp_path: Path):
    repo, page, commit = _repo(tmp_path)
    adapter = _adapter(repo)

    with pytest.raises(InvalidPublicationError, match="tag is not allowed"):
        adapter.prepare(_request(page, commit, fields={"body": "<script>alert(1)</script>"}, idempotency_key="unsafe-script"))
    with pytest.raises(InvalidPublicationError, match="safe HTTP"):
        adapter.prepare(_request(page, commit, fields={"body": '<p><a href="javascript:alert(1)">x</a></p>'}, idempotency_key="unsafe-link"))
    with pytest.raises(UnauthorizedTargetError, match="not allowlisted"):
        adapter.prepare(_request(page, commit, fields={"canonical": "https://example.test/"}, idempotency_key="unknown-field"))

    prepared = adapter.prepare(_request(page, commit, idempotency_key="approval-drift"))
    with pytest.raises(ReviewRequiredError, match="approved fields differ"):
        adapter.apply(
            prepared,
            {
                "revision_hash": prepared.revision_hash,
                "fields": {"title": "Changed after approval"},
                "current_facts": prepared.current_facts,
            },
        )


def test_static_adapter_rejects_stale_hashes_and_stale_idempotency_content(tmp_path: Path):
    repo, page, commit = _repo(tmp_path)
    adapter = _adapter(repo)
    request = _request(page, commit)
    page.write_text(HTML.replace("Base page", "Manual edit"), encoding="utf-8")
    with pytest.raises(PublicationConflictError, match="base content hash changed"):
        adapter.prepare(request)

    page.write_text(HTML, encoding="utf-8")
    prepared = adapter.prepare(request)
    with pytest.raises(IdempotencyConflictError, match="different prepared artifact"):
        adapter.prepare(
            _request(
                page,
                commit,
                fields={"title": "Different content"},
                idempotency_key=request.idempotency_key,
            )
        )


def test_static_adapter_rejects_manifest_path_traversal(tmp_path: Path):
    repo, _, _ = _repo(tmp_path)
    with pytest.raises(ManifestError):
        StaticHtmlAdapter(
            {
                "sites": {
                    "fixture": {
                        "repository": str(repo),
                        "pages": {"home": {"file": "../outside.html", "fields": {"title": "title"}}},
                    }
                }
            }
        )
