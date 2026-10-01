from __future__ import annotations

from decimal import Decimal
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import app
from backend.database import SessionLocal
from backend.models import (
    ProcurementQuestion,
    ProcurementQuestionSet,
    ProcurementQuestionSetVersion,
    Site,
    VisibilityRun,
    VisibilitySample,
    Workspace,
    utcnow,
)
from backend.visibility_provider import VisibilityResponse
from backend.visibility_worker import VisibilityWorker, _reserve_sample_budget, _settle_sample_cost


def _records() -> tuple[int, int, int, int]:
    with SessionLocal() as db:
        workspace = Workspace(name="Visibility workspace", external_id="visibility-workspace")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="Acme valves", base_url="https://acme.example")
        db.add(site)
        db.flush()
        query_set = ProcurementQuestionSet(workspace_id=workspace.id, site_id=site.id, name="Visibility questions", current_version=1)
        version = ProcurementQuestionSetVersion(version=1, edit_version=1, state="frozen")
        query_set.versions = [version]
        version.questions = [
            ProcurementQuestion(
                position=index,
                question=question,
                product="industrial valves",
                use_case="water treatment",
                buyer_role="procurement manager",
                purchase_stage="shortlist",
                target_market="US",
                language="en",
            )
            for index, question in enumerate(("What is Acme valve lead time?", "Which valve supplier supports water treatment?"), start=1)
        ]
        db.add(query_set)
        db.commit()
        return workspace.id, site.id, query_set.id, version.id


def _create_run(client: TestClient, workspace_id: int, site_id: int, version_id: int, **extra):
    payload = {
        "question_set_version_id": version_id,
        "provider": "fixture",
        "market": "US",
        "language": "en",
        "brand_terms": ["Acme"],
        "idempotency_key": "visibility-test-key",
        **extra,
    }
    return client.post(f"/api/workspaces/{workspace_id}/sites/{site_id}/visibility-runs", json=payload)


def test_fixture_visibility_run_persists_raw_answer_citations_and_metrics():
    workspace_id, site_id, _, version_id = _records()
    with TestClient(app) as client:
        created = _create_run(client, workspace_id, site_id, version_id)
        assert created.status_code == 202, created.text
        queued = created.json()
        assert queued["is_synthetic"] is True
        assert queued["status"] == "queued"
        assert queued["planned_samples"] == 2

        executed = client.post(f"/api/workspaces/{workspace_id}/sites/{site_id}/visibility-runs/{queued['id']}/execute")
        assert executed.status_code == 200, executed.text
        body = executed.json()
        assert body["status"] == "succeeded"
        assert body["successful_samples"] == 2
        assert body["metrics"]["sampling_success_rate"] == 1.0
        assert body["metrics"]["site_citation_rate"] == 1.0
        assert body["metrics"]["brand"]["query_count"] == 1
        assert body["metrics"]["brand"]["mention_rate"] == 1.0
        assert body["metrics"]["non_brand"]["mention_rate"] == 1.0
        assert body["reserved_cost_usd"] == "0.000000"
        assert all(sample["status"] == "succeeded" for sample in body["samples"])
        assert all(sample["raw_response"] for sample in body["samples"])
        assert all("acme.example" in sample["citations"][0] for sample in body["samples"])

        duplicate = _create_run(client, workspace_id, site_id, version_id)
        assert duplicate.status_code == 202
        assert duplicate.json()["id"] == queued["id"]


def test_structured_provider_without_credentials_is_unavailable_and_not_success():
    workspace_id, site_id, _, version_id = _records()
    with TestClient(app) as client:
        created = _create_run(
            client,
            workspace_id,
            site_id,
            version_id,
            provider="structured_http",
            idempotency_key="unavailable-key",
        )
        assert created.status_code == 202
        run_id = created.json()["id"]
        executed = client.post(f"/api/workspaces/{workspace_id}/sites/{site_id}/visibility-runs/{run_id}/execute")
        assert executed.status_code == 200, executed.text
        body = executed.json()
        assert body["status"] == "failed"
        assert body["is_synthetic"] is False
        assert body["capability"]["available"] is False
        assert all(sample["status"] == "unavailable" for sample in body["samples"])
        assert body["metrics"]["sampling_success_rate"] == 0.0


def test_visibility_run_rejects_cross_workspace_site_and_worker_budget():
    workspace_id, site_id, _, version_id = _records()
    with SessionLocal() as db:
        other_workspace = Workspace(name="Other visibility workspace", external_id="other-visibility-workspace")
        db.add(other_workspace)
        db.commit()
        other_workspace_id = other_workspace.id
    with TestClient(app) as client:
        response = _create_run(client, other_workspace_id, site_id, version_id, idempotency_key="cross-key")
        assert response.status_code == 404

    with SessionLocal() as db:
        query_set = db.scalar(
            select(ProcurementQuestionSet).where(
                ProcurementQuestionSet.workspace_id == workspace_id,
                ProcurementQuestionSet.site_id == site_id,
            )
        )
        run = VisibilityRun(
            workspace_id=workspace_id,
            site_id=site_id,
            question_set_id=query_set.id,
            question_set_version_id=version_id,
            provider="costly-fixture",
            provider_kind="model_api",
            is_synthetic=True,
            market="US",
            language="en",
            budget_usd="0.000001",
            planned_samples=1,
            total_cost_usd="0",
            brand_terms_json="[]",
        )
        question = db.scalar(select(ProcurementQuestion).where(ProcurementQuestion.question_set_version_id == version_id))
        run.samples = [VisibilitySample(question_id=question.id, position=question.position, status="failed", brand_query=False)]
        db.add(run)
        db.commit()
        run_id = run.id

    class CostlyProvider:
        name = "costly-fixture"
        kind = "model_api"
        model = "costly"
        is_synthetic = True

        def capabilities(self):
            return {"available": True, "synthetic": True}

        def sample(self, question, *, market, language, target_domain):
            return VisibilityResponse(answer_text="answer", raw_response="{}", cost_usd=Decimal("0.000010"))

    assert VisibilityWorker(provider_factory=lambda _: CostlyProvider()).run_once(run_id=run_id)
    with SessionLocal() as db:
        run = db.get(VisibilityRun, run_id)
        sample = db.scalar(select(VisibilitySample).where(VisibilitySample.run_id == run_id))
        assert run.status == "failed"
        assert run.total_cost_usd == "0.000010"
        assert sample.error_code == "budget_exceeded"


def test_visibility_budget_reservation_is_conditional_and_settlement_is_fixed_precision():
    workspace_id, site_id, query_set_id, version_id = _records()
    with SessionLocal() as db:
        run = VisibilityRun(
            workspace_id=workspace_id,
            site_id=site_id,
            question_set_id=query_set_id,
            question_set_version_id=version_id,
            provider="fixture",
            provider_kind="model_api",
            is_synthetic=True,
            market="US",
            language="en",
            status="running",
            budget_usd="0.000010",
            total_cost_usd="0.000000",
            reserved_cost_usd="0.000000",
            lease_token="budget-token",
        )
        db.add(run)
        db.commit()
        run_id = run.id

    with SessionLocal() as first, SessionLocal() as second:
        assert _reserve_sample_budget(first, run_id, 1, Decimal("0.000006"), Decimal("0.000010"), "budget-token")
        first.commit()
        assert not _reserve_sample_budget(second, run_id, 2, Decimal("0.000006"), Decimal("0.000010"), "budget-token")
        second.rollback()

    with SessionLocal() as db:
        _settle_sample_cost(db, run_id, 1, Decimal("0.000005"), Decimal("0.000006"), "budget-token")
        db.commit()
        run = db.get(VisibilityRun, run_id)
        assert run.total_cost_usd == "0.000005"
        assert run.reserved_cost_usd == "0.000000"


def test_expired_visibility_lease_releases_inflight_budget_reservation():
    workspace_id, site_id, query_set_id, version_id = _records()
    with SessionLocal() as db:
        run = VisibilityRun(
            workspace_id=workspace_id,
            site_id=site_id,
            question_set_id=query_set_id,
            question_set_version_id=version_id,
            provider="fixture",
            provider_kind="model_api",
            is_synthetic=True,
            market="US",
            language="en",
            status="running",
            budget_usd="0.010000",
            total_cost_usd="0.000000",
            reserved_cost_usd="0.006000",
            lease_token="expired-token",
            lease_expires_at=utcnow() - timedelta(seconds=1),
        )
        db.add(run)
        db.commit()
        run_id = run.id

    assert VisibilityWorker().recover_interrupted() == 1
    with SessionLocal() as db:
        run = db.get(VisibilityRun, run_id)
        assert run.status == "queued"
        assert run.lease_token is None
        assert run.reserved_cost_usd == "0.000000"
