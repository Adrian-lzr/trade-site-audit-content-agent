from __future__ import annotations

import json
import logging
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app import app
from backend.observability import _row_fields, build_correlation_report, current_request_id, log_event, redact, request_id
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


def test_correlation_report_is_explicitly_blocked_without_runtime_rows(tmp_path):
    from backend.database import SessionLocal
    from backend.models import Workspace

    artifact = tmp_path / "input.log"
    artifact.write_text("fixture evidence\n", encoding="utf-8")
    with SessionLocal() as db:
        workspace = Workspace(name="correlation-test")
        db.add(workspace)
        db.commit()
        report = build_correlation_report(
            db,
            workspace_id=workspace.id,
            request_id="missing-runtime",
            command=["pytest", "backend/tests/test_observability.py"],
            artifact_paths=[artifact],
        )

    assert report["task_id"] == "T16"
    assert report["verification"] == "blocked_external"
    assert report["business_data_claim"] is False
    assert report["blockers"][0]["code"] == "no_runtime_rows"
    assert report["artifacts"][0]["sha256"]
    assert len(report["report_sha256"]) == 64


def test_correlation_report_redacts_payload_fields_and_binds_commit():
    from backend.database import SessionLocal
    from backend.models import AuditEvent, Workspace

    with SessionLocal() as db:
        workspace = Workspace(name="correlation-audit")
        db.add(workspace)
        db.flush()
        db.add(
            AuditEvent(
                workspace_id=workspace.id,
                actor="operator",
                action="task.started",
                target_type="content_task",
                target_id="7",
                run_id="req-t16",
                after_version_json='{"token":"must-not-appear"}',
            )
        )
        db.commit()
        report = build_correlation_report(db, workspace_id=workspace.id, request_id="req-t16")

    assert report["verification"] == "runtime_observed"
    assert report["code_commit"]
    assert report["correlation"]["audit_events"][0]["run_id"] == "req-t16"
    assert "must-not-appear" not in json.dumps(report)


def test_correlation_report_is_workspace_scoped_and_decimal_safe():
    with __import__("backend.database", fromlist=["SessionLocal"]).SessionLocal() as db:
        from backend.models import AuditEvent, Workspace

        first = Workspace(name="scope-a")
        second = Workspace(name="scope-b")
        db.add_all([first, second])
        db.flush()
        db.add_all(
            [
                AuditEvent(workspace_id=first.id, actor="a", action="one", target_type="task", target_id="1", run_id="same"),
                AuditEvent(workspace_id=second.id, actor="b", action="two", target_type="task", target_id="2", run_id="same"),
            ]
        )
        db.commit()
        report = build_correlation_report(db, workspace_id=first.id, request_id="same")

    assert [row["target_id"] for row in report["correlation"]["audit_events"]] == ["1"]
    assert _row_fields(SimpleNamespace(amount=Decimal("1.230000")), ("amount",))["amount"] == "1.230000"


def test_correlation_report_requires_a_selector():
    from backend.database import SessionLocal

    with SessionLocal() as db:
        report = build_correlation_report(db)

    assert report["verification"] == "blocked_external"
    assert {item["code"] for item in report["blockers"]} >= {"scope_unbounded", "no_runtime_rows"}


def test_correlation_report_does_not_call_partial_chain_runtime_verified():
    from backend.database import SessionLocal
    from backend.models import AuditEvent, Workspace

    with SessionLocal() as db:
        workspace = Workspace(name="correlation-partial")
        db.add(workspace)
        db.flush()
        db.add(AuditEvent(
            workspace_id=workspace.id,
            actor="operator",
            action="task.started",
            target_type="content_task",
            target_id="9",
            run_id=None,
        ))
        db.commit()
        report = build_correlation_report(db, workspace_id=workspace.id)

    assert report["verification"] == "blocked_external"
    assert any(item["code"] == "audit_request_link_missing" for item in report["blockers"])
