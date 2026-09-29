from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit


RULE_VERSION = "1.0.0"
RULES = (
    ("http_status_redirect", "page", "error", "Check the expected page response and validated redirect chain."),
    ("robots_access", "page", "error", "Review robots.txt and the intended crawl policy for this URL."),
    ("index_directives", "page", "error", "Align robots meta and X-Robots-Tag with the configured indexability policy."),
    ("sitemap_validity", "site_sample", "warning", "Publish a valid same-origin sitemap and include canonical public URLs."),
    ("canonical_target", "page", "warning", "Use one valid canonical URL on the configured preferred origin and verify its target."),
    ("title_present", "page", "warning", "Add a concise, page-specific HTML title."),
    ("duplicate_titles", "site_sample", "info", "Review repeated titles and make them distinguish the represented pages."),
    ("meta_description", "page", "info", "Consider adding a page-specific meta description for search snippets."),
    ("heading_structure", "page", "info", "Review the main heading structure; this heuristic is not a ranking penalty."),
    ("internal_links", "page", "warning", "Repair confirmed broken in-scope internal links; retry inconclusive network checks."),
    ("image_alt", "page", "info", "Review images without alt attributes and provide appropriate text or an empty decorative alt."),
    ("jsonld_consistency", "page", "warning", "Correct invalid or incomplete JSON-LD and manually verify it against visible page content."),
)


@dataclass(frozen=True)
class ParsedDocument:
    title: str | None
    canonical: list[str]
    robots: list[str]
    descriptions: list[str]
    h1: list[str]
    images: list[bool]
    links: list[str]
    jsonld: list[str]


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.canonical: list[str] = []
        self.robots: list[str] = []
        self.descriptions: list[str] = []
        self.images: list[bool] = []
        self.links: list[str] = []
        self.jsonld: list[str] = []
        self._in_title = False
        self._in_h1 = False
        self._in_jsonld = False
        self._jsonld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {key.lower(): value for key, value in attrs if key}
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_h1 = True
            self.h1_parts.append("")
        elif tag == "meta":
            name = (values.get("name") or "").strip().lower()
            if name in {"robots", "googlebot"} and values.get("content") is not None:
                self.robots.append(values["content"] or "")
            if name == "description" and values.get("content") is not None:
                self.descriptions.append(values["content"] or "")
        elif tag == "link" and "canonical" in (values.get("rel") or "").lower().split():
            self.canonical.append(values.get("href") or "")
        elif tag == "img":
            self.images.append("alt" in values)
        elif tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        elif tag == "script" and "json" in (values.get("type") or "").lower() and "ld+json" in (values.get("type") or "").lower():
            self._in_jsonld = True
            self._jsonld_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
        elif tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jsonld_parts))
            self._in_jsonld = False
            self._jsonld_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_h1 and self.h1_parts:
            self.h1_parts[-1] += data
        if self._in_jsonld:
            self._jsonld_parts.append(data)

    def result(self) -> ParsedDocument:
        title = " ".join("".join(self.title_parts).split()) or None
        headings = [" ".join(value.split()) for value in self.h1_parts]
        return ParsedDocument(title, self.canonical, self.robots, self.descriptions, headings, self.images, self.links, self.jsonld)


def parse_document(content: str) -> ParsedDocument:
    parser = _DocumentParser()
    parser.feed(content or "")
    parser.close()
    return parser.result()


def _result(rule_index: int, status: str, message: str, evidence: dict[str, Any], *, severity: str | None = None) -> dict[str, Any]:
    rule_id, scope, default_severity, remediation = RULES[rule_index]
    return {
        "rule_id": rule_id,
        "version": RULE_VERSION,
        "scope": scope,
        "status": status,
        "severity": severity or default_severity,
        "message": message,
        "evidence": evidence,
        "remediation_hint": remediation,
    }


def _origin(url: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        port = parsed.port
        if port == (80 if parsed.scheme == "http" else 443):
            port = None
        return parsed.scheme.lower(), parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower(), port
    except (ValueError, UnicodeError):
        return None


def _is_html(snapshot: dict[str, Any]) -> bool:
    return (snapshot.get("content_type") or "").split(";", 1)[0].strip().lower() in {"text/html", "application/xhtml+xml"}


def _document_status(snapshot: dict[str, Any]) -> tuple[str, str]:
    code = int(snapshot.get("status_code") or 0)
    if code == 0:
        return "unknown", "No HTTP document was captured because the request was blocked or failed."
    if code == 429 or code >= 500:
        return "unknown", f"HTTP {code} is inconclusive and is not treated as a page-content failure."
    if code < 200 or code >= 400:
        return "unknown", f"HTTP {code} did not provide a document for content checks."
    if not _is_html(snapshot):
        return "not_applicable", "The captured response is not HTML."
    return "available", ""


def evaluate_snapshot(snapshot: dict[str, Any], audit_input: dict[str, Any]) -> list[dict[str, Any]]:
    """Evaluate a frozen snapshot and frozen bounded probe context deterministically."""
    document = parse_document(str(snapshot.get("content") or ""))
    policy = audit_input.get("policy") or {}
    crawl = audit_input.get("crawl") or {}
    robots = audit_input.get("robots") or {}
    sitemap = audit_input.get("sitemap") or {}
    links = audit_input.get("link_checks") or []
    sample_pages = audit_input.get("sample_pages") or []
    page_status, page_status_message = _document_status(snapshot)
    url = str(snapshot.get("url") or "")
    code = int(snapshot.get("status_code") or 0)
    out: list[dict[str, Any]] = []

    # 1. Response status and redirect anomalies.
    if not policy.get("expected_accessible", True):
        out.append(_result(0, "not_applicable", "This URL is configured as intentionally inaccessible.", {"expected_accessible": False}))
    elif code == 0 or code == 429 or code >= 500:
        out.append(_result(0, "unknown", "The HTTP result is unavailable or transient; no page-business failure is inferred.", {"status_code": code, "error": crawl.get("error"), "blocked_by_robots": crawl.get("blocked_by_robots"), "redirect_chain": crawl.get("redirect_chain", [])}))
    elif code >= 400:
        out.append(_result(0, "fail", f"Expected an accessible page but received HTTP {code}.", {"url": url, "status_code": code, "redirect_chain": crawl.get("redirect_chain", [])}))
    elif crawl.get("error"):
        out.append(_result(0, "unknown", "The redirect/request chain could not be verified completely.", {"status_code": code, "error": crawl["error"], "redirect_chain": crawl.get("redirect_chain", [])}))
    else:
        out.append(_result(0, "pass", "The page returned a successful HTTP response through a validated redirect chain.", {"url": url, "status_code": code, "redirect_chain": crawl.get("redirect_chain", [])}))

    # 2. Robots reachability policy.
    rcode = robots.get("status_code")
    if not policy.get("expected_accessible", True):
        out.append(_result(1, "not_applicable", "The URL is configured as intentionally inaccessible to crawlers.", {"expected_accessible": False}))
    elif robots.get("error") or rcode is None or rcode == 429 or (rcode is not None and rcode >= 500):
        out.append(_result(1, "unknown", "robots.txt could not be checked reliably.", {"url": robots.get("url"), "status_code": rcode, "error": robots.get("error")}))
    elif crawl.get("blocked_by_robots"):
        out.append(_result(1, "fail", "robots.txt disallows this expected public page.", {"url": url, "robots_url": robots.get("url"), "directive": crawl.get("robots_directive")}))
    else:
        out.append(_result(1, "pass", "robots.txt does not block this expected public page.", {"url": url, "robots_url": robots.get("url"), "status_code": rcode, "robots_missing": rcode == 404}))

    # 3. Index directive expectations and conflicts.
    if page_status != "available":
        out.append(_result(2, "not_applicable" if page_status == "not_applicable" else "unknown", page_status_message, {"status_code": code}))
    else:
        directives = [part.strip().lower() for value in document.robots for part in re.split(r"[,;]", value) if part.strip()]
        headers = {str(key).lower(): str(value) for key, value in (audit_input.get("headers") or {}).items()}
        x_robots = headers.get("x-robots-tag", "")
        directives.extend(part.strip().lower() for part in re.split(r"[,;]", x_robots) if part.strip())
        has_noindex = any("noindex" in value.split(":", 1)[-1].split() for value in directives)
        has_index = any("index" in value.split(":", 1)[-1].split() for value in directives)
        expected_indexable = bool(policy.get("expected_indexable", True))
        if has_noindex and has_index:
            state, message = "fail", "Conflicting index and noindex directives were found."
        elif expected_indexable and has_noindex:
            state, message = "fail", "The page is configured to be indexable but sends noindex."
        elif not expected_indexable and not has_noindex:
            state, message = "fail", "The page is configured as non-indexable but no noindex directive was found."
        else:
            state, message = "pass", "Index directives agree with the configured expectation."
        out.append(_result(2, state, message, {"expected_indexable": expected_indexable, "meta_directives": document.robots, "x_robots_tag": x_robots}))

    # 4. Sitemap XML and bounded sampled-URL validity.
    scode = sitemap.get("status_code")
    if sitemap.get("error") or scode is None or scode == 429 or (scode is not None and scode >= 500):
        out.append(_result(3, "unknown", "The sitemap could not be fetched reliably.", {"url": sitemap.get("url"), "status_code": scode, "error": sitemap.get("error")}))
    elif scode == 404:
        out.append(_result(3, "not_applicable", "No sitemap was found at the configured or advertised sitemap URL.", {"url": sitemap.get("url"), "status_code": scode}))
    elif scode is None or scode < 200 or scode >= 400:
        out.append(_result(3, "unknown", "The sitemap response is inconclusive.", {"url": sitemap.get("url"), "status_code": scode}))
    else:
        text = str(sitemap.get("content") or "")
        try:
            if "<!doctype" in text.lower() or "<!entity" in text.lower():
                raise ValueError("DTD and entity declarations are not accepted")
            root = ET.fromstring(text)
            root_name = root.tag.rsplit("}", 1)[-1].lower()
            if root_name not in {"urlset", "sitemapindex"}:
                raise ValueError("root element must be urlset or sitemapindex")
            locs = [el.text.strip() for el in root.iter() if el.tag.rsplit("}", 1)[-1].lower() == "loc" and el.text and el.text.strip()]
            if not locs or len(locs) > 200:
                raise ValueError("sitemap must contain between 1 and 200 sampled loc entries")
            site_origin = _origin(str(policy.get("site_origin") or ""))
            invalid = [loc for loc in locs if _origin(urljoin(str(sitemap.get("url") or url), loc)) != site_origin]
            sampled = {str(entry.get("url")): entry for entry in sample_pages}
            missing_sample = [loc for loc in locs if urljoin(str(sitemap.get("url") or url), loc) in sampled and int(sampled[urljoin(str(sitemap.get("url") or url), loc)].get("status_code") or 0) >= 400]
            if invalid:
                out.append(_result(3, "fail", "The sitemap contains URLs outside the configured site origin or with invalid URL syntax.", {"url": sitemap.get("url"), "invalid_urls": invalid[:20], "entry_count": len(locs)}))
            elif missing_sample:
                out.append(_result(3, "fail", "Sampled sitemap URLs include pages that returned HTTP errors.", {"invalid_sampled_urls": missing_sample[:20], "entry_count": len(locs)}))
            else:
                out.append(_result(3, "pass", "The sitemap XML is valid and its sampled URLs are on the configured origin.", {"url": sitemap.get("url"), "entry_count": len(locs), "sampled_page_count": sum(1 for loc in locs if urljoin(str(sitemap.get("url") or url), loc) in sampled)}))
        except (ET.ParseError, ValueError) as exc:
            out.append(_result(3, "fail", "The sitemap is not valid supported XML.", {"url": sitemap.get("url"), "error": str(exc)}))

    # 5. Canonical syntax, preferred origin, and observed target status.
    if page_status != "available":
        out.append(_result(4, "not_applicable" if page_status == "not_applicable" else "unknown", page_status_message, {"status_code": code}))
    elif not document.canonical:
        out.append(_result(4, "not_applicable", "The page does not declare a canonical URL; no canonical target can be assessed.", {"url": url}))
    elif len(document.canonical) != 1 or not document.canonical[0].strip():
        out.append(_result(4, "fail", "The page must declare at most one non-empty canonical URL.", {"canonical_values": document.canonical}))
    else:
        canonical = urljoin(url, document.canonical[0].strip())
        target_origin = _origin(canonical)
        preferred_origin = _origin(str(policy.get("preferred_origin") or policy.get("site_origin") or ""))
        observed = {str(entry.get("url")): entry for entry in sample_pages}
        target = observed.get(canonical)
        if target_origin is None:
            out.append(_result(4, "fail", "The canonical URL is not a valid HTTP(S) URL.", {"canonical": canonical}))
        elif target_origin != preferred_origin:
            out.append(_result(4, "fail", "The canonical URL is not on the configured preferred site origin.", {"canonical": canonical, "preferred_origin": policy.get("preferred_origin") or policy.get("site_origin")}))
        elif target and (int(target.get("status_code") or 0) == 429 or int(target.get("status_code") or 0) >= 500):
            out.append(_result(4, "unknown", "The sampled canonical target has a transient HTTP failure.", {"canonical": canonical, "status_code": target.get("status_code")}))
        elif target and int(target.get("status_code") or 0) >= 400:
            out.append(_result(4, "fail", "The sampled canonical target returned an HTTP error.", {"canonical": canonical, "status_code": target.get("status_code")}))
        elif not target and canonical != url:
            out.append(_result(4, "unknown", "The canonical target is in scope but was not in the bounded captured sample.", {"canonical": canonical, "target_checked": False}))
        else:
            out.append(_result(4, "pass", "The canonical URL is valid, on the preferred origin, and its target is successful or self-referential.", {"canonical": canonical, "target_status": target.get("status_code") if target else code}))

    # 6-9, 11-12. Document-level checks.
    if page_status != "available":
        state = "not_applicable" if page_status == "not_applicable" else "unknown"
        for index in (5, 7, 8, 10, 11):
            out.append(_result(index, state, page_status_message, {"status_code": code}))
    else:
        title = snapshot.get("title") or document.title
        out.append(_result(5, "fail" if not title else "pass", "The HTML title is missing." if not title else "The HTML title is present.", {"element": "title", "value": title}))

        usable_sample = [entry for entry in sample_pages if entry.get("title") and 200 <= int(entry.get("status_code") or 0) < 300]
        if len(usable_sample) < 2:
            out.append(_result(6, "not_applicable", "Fewer than two successful titled pages were captured in this frozen sample.", {"sampled_titled_pages": len(usable_sample), "minimum_required": 2}))
        else:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for entry in usable_sample:
                key = " ".join(str(entry["title"]).casefold().split())
                grouped.setdefault(key, []).append(entry)
            duplicates = [[{"url": item.get("url"), "title": item.get("title"), "content_hash": item.get("content_hash")} for item in group] for group in grouped.values() if len(group) > 1]
            out.append(_result(6, "needs_review" if duplicates else "pass", "Repeated titles need human review; duplication alone is not treated as a search penalty." if duplicates else "No duplicate titles were found in the frozen sample.", {"duplicate_groups": duplicates, "sampled_titled_pages": len(usable_sample)}))

        description = next((value.strip() for value in document.descriptions if value.strip()), None)
        out.append(_result(7, "needs_review" if not description else "pass", "The page has no non-empty meta description; this is an improvement suggestion." if not description else "A non-empty meta description is present.", {"element": "meta[name=description]", "value": description}))

        if not document.h1 or len(document.h1) != 1:
            out.append(_result(8, "needs_review", "The page has no H1 or has multiple H1 elements; review its heading hierarchy manually.", {"h1_count": len(document.h1), "headings": document.h1[:10]}))
        else:
            out.append(_result(8, "pass", "Exactly one H1 was found; this structural check is not a ranking guarantee.", {"h1_count": 1, "heading": document.h1[0]}))

        in_scope_links = [entry for entry in links if entry.get("in_scope", True)]
        if not in_scope_links:
            out.append(_result(9, "not_applicable", "No in-scope internal links were available to check.", {"checked_links": 0, "out_of_scope_links": len(links)}))
        else:
            broken = [entry for entry in in_scope_links if int(entry.get("status_code") or 0) in {400, 401, 403, 404, 410}]
            inconclusive = [entry for entry in in_scope_links if entry.get("error") or int(entry.get("status_code") or 0) == 0 or int(entry.get("status_code") or 0) == 429 or int(entry.get("status_code") or 0) >= 500]
            if broken:
                state, message = "fail", "One or more sampled in-scope internal links returned a definitive client error."
            elif inconclusive:
                state, message = "unknown", "At least one sampled internal-link request was inconclusive; it is not counted as a broken page."
            else:
                state, message = "pass", "All sampled in-scope internal links returned non-error responses."
            out.append(_result(9, state, message, {"checked_links": len(in_scope_links), "broken_links": broken[:20], "inconclusive_links": inconclusive[:20], "results": in_scope_links[:20]}))

        missing_alt = sum(1 for has_alt in document.images if not has_alt)
        if not document.images:
            out.append(_result(10, "not_applicable", "No images were present in the captured HTML.", {"image_count": 0}))
        elif missing_alt:
            out.append(_result(10, "needs_review", "Some images have no alt attribute; review whether they need descriptive or decorative alternatives.", {"image_count": len(document.images), "missing_alt_count": missing_alt}))
        else:
            out.append(_result(10, "pass", "Every captured image has an alt attribute.", {"image_count": len(document.images), "missing_alt_count": 0}))

        out.append(_jsonld_result(document, snapshot, title))

    present = {item["rule_id"] for item in out}
    if "duplicate_titles" not in present:
        usable_sample = [entry for entry in sample_pages if entry.get("title") and 200 <= int(entry.get("status_code") or 0) < 300]
        if len(usable_sample) < 2:
            out.append(_result(6, "not_applicable", "Fewer than two successful titled pages were captured in this frozen sample.", {"sampled_titled_pages": len(usable_sample), "minimum_required": 2}))
        else:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for entry in usable_sample:
                grouped.setdefault(" ".join(str(entry["title"]).casefold().split()), []).append(entry)
            duplicates = [[{"url": item.get("url"), "title": item.get("title"), "content_hash": item.get("content_hash")} for item in group] for group in grouped.values() if len(group) > 1]
            out.append(_result(6, "needs_review" if duplicates else "pass", "Repeated titles need human review; duplication alone is not treated as a search penalty." if duplicates else "No duplicate titles were found in the frozen sample.", {"duplicate_groups": duplicates, "sampled_titled_pages": len(usable_sample)}))
    if "internal_links" not in {item["rule_id"] for item in out}:
        in_scope_links = [entry for entry in links if entry.get("in_scope", True)]
        if not in_scope_links:
            out.append(_result(9, "not_applicable" if page_status == "not_applicable" else "unknown", "No in-scope internal links could be checked for this snapshot.", {"checked_links": 0, "out_of_scope_links": len(links)}))
        else:
            broken = [entry for entry in in_scope_links if int(entry.get("status_code") or 0) in {400, 401, 403, 404, 410}]
            inconclusive = [entry for entry in in_scope_links if entry.get("error") or int(entry.get("status_code") or 0) == 0 or int(entry.get("status_code") or 0) == 429 or int(entry.get("status_code") or 0) >= 500]
            state = "fail" if broken else "unknown" if inconclusive else "pass"
            message = "One or more sampled in-scope internal links returned a definitive client error." if broken else "At least one sampled internal-link request was inconclusive." if inconclusive else "All sampled in-scope internal links returned non-error responses."
            out.append(_result(9, state, message, {"checked_links": len(in_scope_links), "broken_links": broken[:20], "inconclusive_links": inconclusive[:20], "results": in_scope_links[:20]}))
    return out


def _jsonld_result(document: ParsedDocument, snapshot: dict[str, Any], visible_title: str | None) -> dict[str, Any]:
    if not document.jsonld:
        return _result(11, "not_applicable", "No JSON-LD was present; absence alone is not an indexability failure.", {"script_count": 0})
    parsed: list[Any] = []
    errors: list[str] = []
    for index, source in enumerate(document.jsonld):
        try:
            parsed.append(json.loads(source))
        except (json.JSONDecodeError, TypeError) as exc:
            errors.append(f"script[{index}]: {exc}")
    if errors:
        return _result(11, "fail", "At least one JSON-LD script is invalid JSON.", {"script_count": len(document.jsonld), "errors": errors[:10]})

    objects: list[dict[str, Any]] = []
    def visit(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            graph = value.get("@graph")
            if graph is not None:
                visit(graph)
            if "@type" in value:
                objects.append(value)
    for item in parsed:
        visit(item)

    required = {
        "product": ("name",),
        "organization": ("name",),
        "localbusiness": ("name",),
        "article": ("headline",),
        "newsarticle": ("headline",),
        "blogposting": ("headline",),
        "faqpage": ("mainEntity",),
        "webpage": ("name",),
        "website": ("name",),
        "breadcrumblist": ("itemListElement",),
    }
    missing: list[dict[str, Any]] = []
    unsupported: list[str] = []
    names: list[str] = []
    for obj in objects:
        types = obj.get("@type")
        types = types if isinstance(types, list) else [types]
        supported_type = False
        for type_value in types:
            type_name = str(type_value).rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower()
            fields = required.get(type_name)
            if fields is None:
                unsupported.append(str(type_value))
                continue
            supported_type = True
            absent = [field for field in fields if obj.get(field) in (None, "", [], {})]
            if absent:
                missing.append({"type": str(type_value), "missing_fields": absent})
        if supported_type:
            for field in ("name", "headline"):
                if isinstance(obj.get(field), str) and obj[field].strip():
                    names.append(obj[field].strip())

    if not objects:
        return _result(11, "needs_review", "JSON-LD is valid JSON but contains no recognized schema objects for this check.", {"script_count": len(parsed), "recognized_objects": 0})
    if missing:
        return _result(11, "fail", "A supported JSON-LD type is missing fields required by this rule profile.", {"objects_checked": len(objects), "missing_required_fields": missing})
    if unsupported:
        return _result(11, "needs_review", "Some JSON-LD types are outside the deterministic required-field profile.", {"objects_checked": len(objects), "unsupported_types": sorted(set(unsupported)), "supported_required_field_profile": sorted(required)})
    visible = [value for value in [visible_title, *(item for item in document.h1 if item)] if value]
    if not visible:
        return _result(11, "unknown", "JSON-LD fields could not be compared because no visible title or H1 was captured.", {"structured_names": names, "visible_labels": []})
    mismatches = [name for name in names if not any(name.casefold() in label.casefold() or label.casefold() in name.casefold() for label in visible)]
    if mismatches:
        return _result(11, "needs_review", "Structured names differ from visible page labels and require human comparison.", {"structured_names": names, "visible_labels": visible, "mismatches": mismatches})
    return _result(11, "pass", "JSON-LD syntax, supported required fields, and sampled visible labels are consistent.", {"objects_checked": len(objects), "structured_names": names, "visible_labels": visible, "required_field_profile": sorted(required)})
