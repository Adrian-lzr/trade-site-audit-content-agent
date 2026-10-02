from __future__ import annotations

import hashlib

import pytest

from backend.services.ingestion import IngestionError, ingest_csv, ingest_text_pdf, retrieve_evidence


def test_csv_ingestion_keeps_proposed_facts_and_row_hash_locators():
    raw = "subject,predicate,value,unit\nVX-21,pressure_rating,16,bar\nVX-21,minimum_order_quantity,20,pieces\n"
    result = ingest_csv(raw, source_id="catalog.csv")
    assert result.source_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert len(result.proposed_facts) == 2
    assert all(fact["status"] == "proposed" for fact in result.proposed_facts)
    assert result.proposed_facts[0]["source_locator"] == "csv:catalog.csv#row=2"
    assert result.blocks[1].source_hash == result.source_hash


def test_csv_ingestion_rejects_missing_fields_and_bad_encoding():
    with pytest.raises(IngestionError, match="requires"):
        ingest_csv("subject,value\nVX-21,16\n", source_id="bad.csv")
    with pytest.raises(IngestionError, match="UTF-8"):
        ingest_csv(b"subject,predicate,value\n\xff", source_id="bad.csv")


def test_pdf_ingestion_requires_text_layer_and_retrieval_is_explainable():
    with pytest.raises(IngestionError, match="not a PDF"):
        ingest_text_pdf(b"not pdf", source_id="spec.pdf")
    csv_result = ingest_csv("subject,predicate,value\nVX-21,pressure_rating,16 bar\n", source_id="spec.csv")
    evidence = retrieve_evidence("pressure rating", csv_result.blocks)
    assert evidence and evidence[0]["locator"] == "csv:spec.csv#row=2"
    assert evidence[0]["source_hash"] == csv_result.source_hash
