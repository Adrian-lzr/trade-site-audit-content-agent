from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from backend.app import app
from backend.observability import current_request_id, log_event, redact, request_id
import backend.observability as observability


def test_request_context_preserves_safe_request_id_and_logs_completion(monkeypatch):
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        observability,
        "log_event",
        lambda logger, event, **fields: events.append((event, {"request_id": current_request_id(), **fields})),
    )
    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Request-ID": "browser-check-42"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "browser-check-42"
    completion = next(fields for event, fields in events if event == "http_request_complete")
    assert completion["request_id"] == "browser-check-42"
    assert completion["path"] == "/health"
    assert completion["status_code"] == 200


def test_structured_event_logger_redacts_secrets():
    logger = logging.getLogger("test.observability")
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        log_event(logger, "test_event", secret="Bearer hidden")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    event = json.loads(records[0].getMessage())
    assert event["request_id"] is None
    assert event["secret"] == "[redacted]"


def test_invalid_request_id_is_replaced_and_sensitive_fields_are_redacted():
    assert request_id("bad id with spaces") != "bad id with spaces"
    payload = redact(
        {
            "authorization": "Bearer top-secret",
            "nested": {"api_key": "sk-secret", "message": "Bearer another-secret"},
        }
    )
    assert payload["authorization"] == "[redacted]"
    assert payload["nested"]["api_key"] == "[redacted]"
    assert "another-secret" not in payload["nested"]["message"]
