from __future__ import annotations

import importlib
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.database import SessionLocal
from backend.models import ProcurementQuestion, Site, VisibilityRun, VisibilitySample, Workspace, utcnow
from backend.visibility_provider import StructuredHTTPVisibilityProvider, redact_sensitive_text
from backend.visibility_worker import VisibilityWorker, _reserve_sample_budget


def _records() -> tuple[int, int, int, int]:
    """Keep this module independent from the fixture helper in another test."""

    with SessionLocal() as db:
        workspace = Workspace(name="Runtime governance workspace", external_id="runtime-governance-workspace")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="Runtime site", base_url="https://runtime.example")
        db.add(site)
        db.flush()
        from backend.models import ProcurementQuestionSet, ProcurementQuestionSetVersion

        question_set = ProcurementQuestionSet(workspace_id=workspace.id, site_id=site.id, name="Runtime questions", current_version=1)
        version = ProcurementQuestionSetVersion(version=1, edit_version=1, state="frozen")
        question_set.versions = [version]
        version.questions = [
            ProcurementQuestion(
                position=1,
                question="Which runtime valve is suitable?",
                product="industrial valve",
                use_case="water treatment",
                buyer_role="procurement manager",
                purchase_stage="shortlist",
                target_market="US",
                language="en",
            )
        ]
        db.add(question_set)
        db.commit()
        return workspace.id, site.id, question_set.id, version.id


def _running_run(workspace_id: int, site_id: int, question_set_id: int, version_id: int, token: str) -> int:
    with SessionLocal() as db:
        run = VisibilityRun(
            workspace_id=workspace_id,
            site_id=site_id,
            question_set_id=question_set_id,
            question_set_version_id=version_id,
            provider="fixture",
            provider_kind="model_api",
            is_synthetic=True,
            market="US",
            language="en",
            status="running",
            budget_usd="1.000000",
            total_cost_usd="0.000000",
            reserved_cost_usd="0.000000",
            lease_token=token,
            request_id=f"run-{token}",
        )
        db.add(run)
        db.commit()
        return run.id


def test_workspace_daily_budget_reservation_is_serialized_and_counts_reservations():
    workspace_id, site_id, question_set_id, version_id = _records()
    first_id = _running_run(workspace_id, site_id, question_set_id, version_id, "daily-one")
    second_id = _running_run(workspace_id, site_id, question_set_id, version_id, "daily-two")

    with SessionLocal() as first, SessionLocal() as second:
        assert _reserve_sample_budget(first, first_id, 1, Decimal("0.000006"), Decimal("1"), "daily-one", Decimal("0.000010"))
        first.commit()
        assert not _reserve_sample_budget(second, second_id, 1, Decimal("0.000006"), Decimal("1"), "daily-two", Decimal("0.000010"))
        second.rollback()

    with SessionLocal() as db:
        first_run = db.get(VisibilityRun, first_id)
        second_run = db.get(VisibilityRun, second_id)
        assert first_run.reserved_cost_usd == "0.000006"
        assert second_run.reserved_cost_usd == "0.000000"


def test_raw_evidence_retention_removes_only_raw_body():
    workspace_id, site_id, question_set_id, version_id = _records()
    with SessionLocal() as db:
        question_id = db.scalar(select(ProcurementQuestion.id).where(ProcurementQuestion.question_set_version_id == version_id))
        run = VisibilityRun(
            workspace_id=workspace_id,
            site_id=site_id,
            question_set_id=question_set_id,
            question_set_version_id=version_id,
            provider="fixture",
            provider_kind="model_api",
            is_synthetic=True,
            market="US",
            language="en",
            status="succeeded",
            request_id="run-retention",
        )
        db.add(run)
        db.flush()
        sample = VisibilitySample(
            run_id=run.id,
            question_id=question_id,
            position=1,
            status="succeeded",
            raw_response='{"answer":"old"}',
            answer_text="Parsed answer",
            citations_json='["https://runtime.example/"]',
            request_id="sample-retention",
            model="fixture-visibility-v1",
            cost_usd="0.000001",
            created_at=utcnow() - timedelta(days=31),
        )
        db.add(sample)
        db.commit()

    assert VisibilityWorker(raw_retention_days=30).purge_expired_raw_evidence(now=utcnow()) == 1
    with SessionLocal() as db:
        sample = db.scalar(select(VisibilitySample).where(VisibilitySample.request_id == "sample-retention"))
        assert sample.raw_response is None
        assert sample.answer_text == "Parsed answer"
        assert sample.citations_json == '["https://runtime.example/"]'
        assert sample.request_id == "sample-retention"
        assert sample.model == "fixture-visibility-v1"


def test_structured_provider_redacts_credentials_and_propagates_request_id():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "request_id": "provider-42",
                "answer": "Use Bearer super-secret-token for access",
                "citations": ["https://runtime.example/?api_key=sk-secret-value"],
                "api_key": "sk-secret-value",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = StructuredHTTPVisibilityProvider(
        url="https://provider.example/sample",
        api_key="local-provider-secret",
        client=client,
    )
    response = provider.sample(
        "Which valve is suitable?",
        market="US",
        language="en",
        target_domain="runtime.example",
        request_id="sample-request-42",
    )
    assert captured["request_id"] == "sample-request-42"
    assert response.provider_request_id == "provider-42"
    assert "super-secret-token" not in (response.raw_response or "")
    assert "sk-secret-value" not in (response.raw_response or "")
    assert "super-secret-token" not in (response.answer_text or "")
    assert "api_key" in (response.raw_response or "")
    client.close()


def test_run_budget_boundary_is_rejected_before_provider_execution(monkeypatch):
    workspace_id, site_id, _, version_id = _records()
    app_module = importlib.import_module("backend.app")
    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, visibility_max_run_budget_usd=Decimal("0.000010")),
    )
    payload = {
        "question_set_version_id": version_id,
        "provider": "fixture",
        "market": "US",
        "language": "en",
        "budget_usd": "0.000011",
        "idempotency_key": "runtime-budget-boundary",
    }
    with TestClient(app_module.app) as client:
        response = client.post(f"/api/workspaces/{workspace_id}/sites/{site_id}/visibility-runs", json=payload)
    assert response.status_code == 422
    assert "per-run limit" in response.text


def test_redaction_bounds_evidence_without_exposing_token():
    redacted = redact_sensitive_text("Bearer local-secret-token", max_bytes=12)
    assert "local-secret-token" not in (redacted or "")
    assert 0 < len((redacted or "").encode("utf-8")) <= 12
