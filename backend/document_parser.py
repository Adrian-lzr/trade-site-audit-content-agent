from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import json
import posixpath
from urllib.parse import urljoin, urlsplit, urlunsplit


PARSER_VERSION = "2.0.0"


def normalize_http_url(value: str, *, base_url: str | None = None) -> tuple[str | None, str | None]:
    """Resolve and normalize an HTTP URL without changing crawler identity."""
    value = (value or "").strip()
    if not value or any(ord(char) < 32 for char in value):
        return None, "URL is empty or contains control characters"
    try:
        resolved = urljoin(base_url, value) if base_url else value
        parsed = urlsplit(resolved)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None, "URL must use HTTP(S) and include a host"
        if parsed.username is not None or parsed.password is not None:
            return None, "credentials are not allowed in a canonical URL"
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        port = parsed.port
        if port == (80 if parsed.scheme.lower() == "http" else 443):
            port = None
        if ":" in host:
            host = f"[{host}]"
        netloc = host if port is None else f"{host}:{port}"
        path = parsed.path or "/"
        normalized_path = posixpath.normpath(path)
        if path.endswith("/") and not normalized_path.endswith("/"):
            normalized_path += "/"
        path = normalized_path if normalized_path.startswith("/") else f"/{normalized_path}"
        return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, "")), None
    except (UnicodeError, ValueError) as exc:
        return None, f"invalid HTTP URL: {exc}"


@dataclass(frozen=True)
class ParsedDocument:
    title: str | None
    title_values: list[str]
    canonical: list[str]
    normalized_canonicals: list[str | None]
    canonical_errors: list[str | None]
    canonical_url: str | None
    requested_url: str | None
    final_url: str | None
    robots: list[str]
    descriptions: list[str]
    h1: list[str]
    images: list[bool]
    links: list[str]
    jsonld: list[str]
    table_count: int
    faq_marker_count: int
    visible_text_length: int
    script_count: int
    requires_render: bool
    source_hash: str
    metadata_hash: str
    body_hash: str


class _HTMLDocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_values: list[str] = []
        self.canonical: list[str] = []
        self.robots: list[str] = []
        self.descriptions: list[str] = []
        self.h1: list[str] = []
        self.images: list[bool] = []
        self.links: list[str] = []
        self.jsonld: list[str] = []
        self.table_count = 0
        self.faq_marker_count = 0
        self.script_count = 0
        self._in_head = True
        self._body_seen = False
        self._in_title = False
        self._title_parts: list[str] = []
        self._in_h1 = False
        self._h1_parts: list[str] = []
        self._in_svg = 0
        self._in_jsonld = False
        self._jsonld_parts: list[str] = []
        self._executable_script_count = 0
        self._visible_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {key.lower(): value for key, value in attrs if key}
        if tag == "body":
            if self._in_title:
                self._finish_title()
            self._body_seen = True
            self._in_head = False
        elif tag == "head" and not self._body_seen:
            self._in_head = True
        elif tag == "svg":
            self._in_svg += 1
        elif tag == "title" and self._in_head and self._in_svg == 0:
            if self._in_title:
                self._finish_title()
            self._in_title = True
            self._title_parts = []
        elif tag == "h1":
            self._in_h1 = True
            self._h1_parts.append("")
        elif tag == "meta" and self._in_head:
            name = (values.get("name") or "").strip().lower()
            if name in {"robots", "googlebot"} and values.get("content") is not None:
                self.robots.append(values["content"] or "")
            if name == "description" and values.get("content") is not None:
                self.descriptions.append(values["content"] or "")
        elif tag == "link" and self._in_head and "canonical" in (values.get("rel") or "").lower().split():
            self.canonical.append(values.get("href") or "")
        elif tag == "img":
            self.images.append("alt" in values)
        elif tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        elif tag == "table":
            self.table_count += 1
        elif tag == "script":
            self.script_count += 1
            script_type = (values.get("type") or "").lower()
            if "ld+json" in script_type:
                self._in_jsonld = True
                self._jsonld_parts = []
            else:
                self._executable_script_count += 1
        if tag in {"h2", "h3", "summary", "dt"}:
            label = (values.get("class") or "") + " " + (values.get("id") or "")
            if "faq" in label.lower():
                self.faq_marker_count += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "body":
            self._in_head = False
        elif tag == "head":
            if self._in_title:
                self._finish_title()
            self._in_head = False
        elif tag == "svg" and self._in_svg:
            self._in_svg -= 1
        elif tag == "title" and self._in_title:
            self._finish_title()
        elif tag == "h1":
            self._in_h1 = False
        elif tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jsonld_parts))
            self._in_jsonld = False
            self._jsonld_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title and self._in_svg == 0:
            self._title_parts.append(data)
        if self._in_h1 and self._h1_parts:
            self._h1_parts[-1] += data
        if self._in_jsonld:
            self._jsonld_parts.append(data)
        if not self._in_title and not self._in_jsonld and self._body_seen and self._in_svg == 0:
            self._visible_parts.append(data)

    def _finish_title(self) -> None:
        self.title_values.append(" ".join("".join(self._title_parts).split()))
        self._in_title = False
        self._title_parts = []

    def result(self, content: str, requested_url: str | None, final_url: str | None) -> ParsedDocument:
        if self._in_title:
            self._finish_title()
        if self._in_jsonld:
            self.jsonld.append("".join(self._jsonld_parts))
        normalized: list[str | None] = []
        errors: list[str | None] = []
        base = final_url or requested_url
        for value in self.canonical:
            canonical, error = normalize_http_url(value, base_url=base)
            normalized.append(canonical)
            errors.append(error)
        # HTML permits one title; use the first non-empty head title deterministically.
        title = next((value for value in self.title_values if value), None)
        body_text = " ".join(" ".join(self._visible_parts).split())
        visible_text_length = len(body_text)
        has_static_content_structure = bool(
            self.table_count
            or self.faq_marker_count
            or self._h1_parts
            or self.links
        )
        requires_render = bool(
            self._executable_script_count
            and visible_text_length <= 40
            and not self.images
            and not has_static_content_structure
        )
        canonical_url = normalized[0] if len(normalized) == 1 and normalized[0] else None
        metadata = {
            "title_values": self.title_values,
            "canonical": self.canonical,
            "normalized_canonicals": normalized,
            "robots": self.robots,
            "descriptions": self.descriptions,
            "h1": self._h1_parts,
            "images": self.images,
            "links": self.links,
            "jsonld": self.jsonld,
        }
        return ParsedDocument(
            title=title,
            title_values=list(self.title_values),
            canonical=list(self.canonical),
            normalized_canonicals=normalized,
            canonical_errors=errors,
            canonical_url=canonical_url,
            requested_url=requested_url,
            final_url=final_url,
            robots=list(self.robots),
            descriptions=list(self.descriptions),
            h1=[" ".join(value.split()) for value in self._h1_parts],
            images=list(self.images),
            links=list(self.links),
            jsonld=list(self.jsonld),
            table_count=self.table_count,
            faq_marker_count=self.faq_marker_count,
            visible_text_length=visible_text_length,
            script_count=self.script_count,
            requires_render=requires_render,
            source_hash=sha256(content.encode("utf-8")).hexdigest(),
            metadata_hash=sha256(json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
            body_hash=sha256(body_text.encode("utf-8")).hexdigest(),
        )


def parse_document(content: str, *, requested_url: str | None = None, final_url: str | None = None) -> ParsedDocument:
    """Extract bounded document metadata while leaving the captured HTML untouched."""
    source = content or ""
    parser = _HTMLDocumentParser()
    parser.feed(source)
    parser.close()
    return parser.result(source, requested_url, final_url)
