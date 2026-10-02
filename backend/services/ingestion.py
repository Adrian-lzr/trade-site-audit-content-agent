"""Source-backed document ingestion and small, explainable retrieval helpers.

The service produces proposed fact payloads only.  It deliberately has no
database session so callers must choose the workspace, review, and persistence
transaction explicitly.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


class IngestionError(ValueError):
    """The supplied source cannot be parsed under the bounded policy."""


@dataclass(frozen=True, slots=True)
class EvidenceBlock:
    source_id: str
    source_format: str
    source_hash: str
    locator: str
    text: str


@dataclass(frozen=True, slots=True)
class IngestionResult:
    source_id: str
    source_format: str
    source_hash: str
    blocks: tuple[EvidenceBlock, ...]
    proposed_facts: tuple[dict[str, Any], ...]


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _tokens(text: str) -> set[str]:
    """Normalize field-style names so natural-language queries can match them."""
    tokens: set[str] = set()
    for token in re.findall(r"[\w-]{2,}", text.casefold()):
        tokens.update(part for part in re.split(r"[-_]", token) if len(part) >= 2)
    return tokens


def ingest_csv(content: str | bytes, *, source_id: str, max_rows: int = 5000) -> IngestionResult:
    """Parse a CSV source while retaining row locators and the raw hash."""
    if not source_id.strip():
        raise IngestionError("source_id is required")
    raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestionError("CSV must be UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise IngestionError("CSV must contain a header row")
    fields = {field.strip().casefold() for field in reader.fieldnames if field}
    required = {"subject", "predicate", "value"}
    if not required.issubset(fields):
        raise IngestionError("CSV requires subject, predicate, and value columns")
    source_hash = _hash_bytes(raw)
    blocks: list[EvidenceBlock] = []
    facts: list[dict[str, Any]] = []
    for row_number, row in enumerate(reader, start=2):
        if row_number - 1 > max_rows:
            raise IngestionError(f"CSV exceeds the {max_rows}-row limit")
        normalized = {str(key).strip().casefold(): _clean(value) for key, value in row.items() if key}
        if not any(normalized.values()):
            continue
        subject, predicate, value = (normalized.get(key, "") for key in ("subject", "predicate", "value"))
        if not subject or not predicate or not value:
            raise IngestionError(f"CSV row {row_number} has an empty fact field")
        locator = f"csv:{source_id}#row={row_number}"
        block_text = " | ".join(f"{key}={value}" for key, value in normalized.items() if value)
        blocks.append(EvidenceBlock(source_id, "csv", source_hash, locator, block_text))
        facts.append(
            {
                "subject": subject,
                "predicate": predicate,
                "value": value,
                "unit": normalized.get("unit") or None,
                "source_id": source_id,
                "source_locator": locator,
                "status": "proposed",
                "source_hash": source_hash,
            }
        )
    return IngestionResult(source_id, "csv", source_hash, tuple(blocks), tuple(facts))


def extract_text_pdf(content: bytes, *, max_pages: int = 100) -> tuple[str, ...]:
    """Extract text from a text-layer PDF through the optional pypdf package."""
    if not content.startswith(b"%PDF"):
        raise IngestionError("source is not a PDF")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise IngestionError("text-layer PDF ingestion requires the optional pypdf dependency") from exc
    try:
        pages = PdfReader(io.BytesIO(content)).pages
        if len(pages) > max_pages:
            raise IngestionError(f"PDF exceeds the {max_pages}-page limit")
        texts = tuple(_clean(page.extract_text() or "") for page in pages)
    except IngestionError:
        raise
    except Exception as exc:  # parser-specific errors are not safe to expose
        raise IngestionError("PDF text extraction failed") from exc
    if not any(texts):
        raise IngestionError("PDF has no extractable text layer")
    return texts


def ingest_text_pdf(content: bytes, *, source_id: str, max_pages: int = 100) -> IngestionResult:
    pages = extract_text_pdf(content, max_pages=max_pages)
    source_hash = _hash_bytes(content)
    blocks = tuple(
        EvidenceBlock(source_id, "pdf_text", source_hash, f"pdf:{source_id}#page={number}", text)
        for number, text in enumerate(pages, start=1)
        if text
    )
    return IngestionResult(source_id, "pdf_text", source_hash, blocks, tuple())


def retrieve_evidence(question: str, blocks: Iterable[EvidenceBlock], *, limit: int = 5) -> list[dict[str, Any]]:
    """Rank blocks by query token overlap and return stable source evidence."""
    tokens = _tokens(question)
    ranked: list[tuple[int, str, EvidenceBlock]] = []
    for block in blocks:
        block_tokens = _tokens(block.text)
        score = len(tokens & block_tokens)
        if score:
            ranked.append((score, block.locator, block))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [
        {
            "score": score,
            "source_id": block.source_id,
            "source_format": block.source_format,
            "source_hash": block.source_hash,
            "locator": block.locator,
            "snippet": block.text[:2000],
        }
        for score, _, block in ranked[: max(1, min(limit, 20))]
    ]
