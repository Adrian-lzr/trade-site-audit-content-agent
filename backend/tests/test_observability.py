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


def test_correlation_report_fails_closed_for_unmatched_child_rows():
    """A bounded report must never scan child tables without a parent match."""
    from backend.database import SessionLocal
    from backend.models import (
        ChangeRevision,
        ChangeRequest,
        ContentGenerationTask,
        ContentGenerationItem,
        Page,
        PageSnapshot,
        ProcurementQuestion,
        ProcurementQuestionSet,
        ProcurementQuestionSetVersion,
        PublicationAttempt,
        Site,
        VisibilityRun,
        VisibilitySample,
        Workspace,
    )

    with SessionLocal() as db:
        workspace = Workspace(name="correlation-empty-parent")
        other = Workspace(name="correlation-other-workspace")
        db.add_all([workspace, other])
        db.flush()
        site = Site(workspace_id=other.id, name="other-site", base_url="https://other.invalid")
        db.add(site)
        db.flush()
        page = Page(site_id=site.id, canonical_url="https://other.invalid/page")
        snapshot = PageSnapshot(page=page, url=page.canonical_url, status_code=200, content_hash="a" * 64)
        question_set = ProcurementQuestionSet(workspace_id=other.id, site_id=site.id, name="other-set")
        db.add_all([page, snapshot, question_set])
        db.flush()
        version = ProcurementQuestionSetVersion(question_set_id=question_set.id, version=1, state="frozen")
        db.add(version)
        db.flush()
        question = ProcurementQuestion(
            question_set_version_id=version.id, position=1, question="Question?", product="Product",
            use_case="Use", buyer_role="Buyer", purchase_stage="Stage", target_market="Market", language="en",
        )
        change = ChangeRequest(workspace_id=other.id, site_id=site.id, title="Other change")
        db.add_all([question, change])
        db.flush()
        revision = ChangeRevision(change_request_id=change.id, revision=1, content_hash="b" * 64)
        task = ContentGenerationTask(
            workspace_id=other.id, site_id=site.id, question_set_version_id=version.id, status="queued",
        )
        db.add_all([revision, task])
        db.flush()
        item = ContentGenerationItem(
            task_id=task.id, question_id=question.id, page_id=page.id, change_request_id=change.id,
            snapshot_id=snapshot.id, snapshot_hash=snapshot.content_hash, request_summary="Other item",
            thread_id="other-thread",
        )
        publication = PublicationAttempt(
            change_request_id=change.id, revision_id=revision.id, idempotency_key="other-publication", status="queued",
        )
        run = VisibilityRun(
            workspace_id=other.id, site_id=site.id, question_set_id=question_set.id,
            question_set_version_id=version.id, provider="fixture", provider_kind="fixture", market="US",
            language="en", request_id="other-request", status="queued",
        )
        db.add_all([item, publication, run])
        db.flush()
        db.add(VisibilitySample(run_id=run.id, question_id=question.id, position=1, status="failed", request_id="other-request"))
        db.commit()
        reports = [
            build_correlation_report(db, workspace_id=workspace.id),
            build_correlation_report(db, task_id=999901),
            build_correlation_report(db, change_request_id=999902),
            build_correlation_report(db, visibility_run_id=999903),
        ]

    for index, report in enumerate(reports):
        correlation = report["correlation"]
        populated = {key: len(value) for key, value in correlation.items() if isinstance(value, list) and value}
        assert report["verification"] == "blocked_external", (index, report["scope"], populated)
        assert correlation["content_items"] == []
        assert correlation["revisions"] == []
        assert correlation["publication_attempts"] == []
        assert correlation["visibility_samples"] == []


def test_correlation_report_request_selector_does_not_scan_unrelated_task_chain():
    from backend.database import SessionLocal
    from backend.models import (
        ChangeRequest,
        ContentGenerationTask,
        ProcurementQuestionSet,
        ProcurementQuestionSetVersion,
        Site,
        Workspace,
    )

    with SessionLocal() as db:
        workspace = Workspace(name="correlation-request-only")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="request-only-site", base_url="https://fixture.invalid")
        db.add(site)
        db.flush()
        question_set = ProcurementQuestionSet(workspace_id=workspace.id, site_id=site.id, name="request-only-set")
        db.add(question_set)
        db.flush()
        version = ProcurementQuestionSetVersion(question_set_id=question_set.id, version=1, state="frozen")
        db.add(version)
        db.flush()
        change = ChangeRequest(workspace_id=workspace.id, site_id=site.id, title="unrelated change")
        db.add(change)
        db.flush()
        db.add(ContentGenerationTask(workspace_id=workspace.id, site_id=site.id, question_set_version_id=version.id))
        db.commit()
        report = build_correlation_report(db, request_id="request-without-task-link")

    correlation = report["correlation"]
    assert correlation["content_tasks"] == []
    assert correlation["revisions"] == []
    assert correlation["publication_attempts"] == []


def test_correlation_report_request_selector_follows_audited_content_chain():
    from backend.database import SessionLocal
    from backend.models import (
        AuditEvent,
        ChangeRequest,
        ChangeRevision,
        ContentGenerationItem,
        ContentGenerationTask,
        ModelCall,
        Page,
        PageSnapshot,
        ProcurementQuestion,
        ProcurementQuestionSet,
        ProcurementQuestionSetVersion,
        PublicationAttempt,
        Site,
        Workspace,
    )

    request_key = "request-content-chain"
    generation_id = "thread:content-chain:snapshot:draft:0"
    with SessionLocal() as db:
        workspace = Workspace(name="correlation-request-chain")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="chain-site", base_url="https://chain.invalid")
        db.add(site)
        db.flush()
        question_set = ProcurementQuestionSet(workspace_id=workspace.id, site_id=site.id, name="chain-set")
        db.add(question_set)
        db.flush()
        version = ProcurementQuestionSetVersion(question_set_id=question_set.id, version=1, state="frozen")
        db.add(version)
        db.flush()
        question = ProcurementQuestion(
            question_set_version_id=version.id,
            position=1,
            question="What is the confirmed pressure?",
            product="Valve",
            use_case="Water",
            buyer_role="Procurement",
            purchase_stage="Evaluation",
            target_market="US",
            language="en",
        )
        page = Page(site_id=site.id, canonical_url="https://chain.invalid/valve")
        db.add_all([question, page])
        db.flush()
        snapshot = PageSnapshot(
            page_id=page.id,
            url=page.canonical_url,
            status_code=200,
            content_hash="c" * 64,
            content="<html><title>Valve</title></html>",
        )
        change = ChangeRequest(workspace_id=workspace.id, site_id=site.id, title="Chain change")
        task = ContentGenerationTask(
            workspace_id=workspace.id,
            site_id=site.id,
            question_set_version_id=version.id,
            status="awaiting_review",
        )
        db.add_all([snapshot, change, task])
        db.flush()
        revision = ChangeRevision(
            change_request_id=change.id,
            revision=1,
            state="draft",
            content_hash="d" * 64,
            generation_id=generation_id,
        )
        db.add(revision)
        db.flush()
        item = ContentGenerationItem(
            task_id=task.id,
            question_id=question.id,
            page_id=page.id,
            change_request_id=change.id,
            snapshot_id=snapshot.id,
            snapshot_hash=snapshot.content_hash,
            request_summary="Draft pressure details",
            thread_id="chain-thread",
            status="awaiting_review",
        )
        call = ModelCall(
            workspace_id=workspace.id,
            call_key="chain-call",
            call_type="content_generation",
            generation_id=generation_id,
            input_hash="e" * 64,
            status="succeeded",
            cost_known=True,
            amount="0.010000",
        )
        publication = PublicationAttempt(
            change_request_id=change.id,
            revision_id=revision.id,
            idempotency_key="chain-publication",
            status="submitted",
            target="local-fixture",
        )
        db.add_all([item, call, publication])
        db.add_all(
            [
                AuditEvent(
                    workspace_id=workspace.id,
                    actor="operator",
                    action="content_generation_task.created",
                    target_type="content_generation_task",
                    target_id=str(task.id),
                    after_version_json=json.dumps({"site_id": site.id}),
                    run_id=request_key,
                ),
                AuditEvent(
                    workspace_id=workspace.id,
                    actor="operator",
                    action="change.created",
                    target_type="change_request",
                    target_id=str(change.id),
                    after_version_json=json.dumps({"current_revision_id": revision.id}),
                    run_id=request_key,
                ),
            ]
        )
        db.commit()
        report = build_correlation_report(db, request_id=request_key)

    correlation = report["correlation"]
    assert report["verification"] == "runtime_observed"
    assert [row["id"] for row in correlation["content_tasks"]] == [task.id]
    assert [row["id"] for row in correlation["revisions"]] == [revision.id]
    assert [row["id"] for row in correlation["model_calls"]] == [call.id]
    assert [row["id"] for row in correlation["publication_attempts"]] == [publication.id]
    assert report["coverage"]["task_to_revision"] is True
    assert report["coverage"]["revision_to_model_call"] is True
    assert report["coverage"]["revision_to_publication"] is True
