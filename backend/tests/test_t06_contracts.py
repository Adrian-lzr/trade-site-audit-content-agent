from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.content_workflow import ConfirmedFact
from backend.contracts import (
    ApiError,
    ContentDraftContract,
    ErrorCode,
    FactResolutionRequest,
    Page,
    PageRequest,
    PublicationCommand,
    VisibilityMetricsContract,
)
from backend.database import SessionLocal
from backend.models import Fact, FactStatus, FactVisibility, Workspace
from backend.services.application import ApplicationServiceError, ApplicationServices


def test_contract_models_freeze_top_level_shape_and_pagination_bounds():
    page = Page[dict[str, int]](data=[{"id": 1}])
    assert page.page.limit == 50
    assert page.contract_version == "2026-10-02"
    assert PageRequest(limit=200).limit == 200
    with pytest.raises(ValidationError):
        PageRequest(limit=201)
    with pytest.raises(ValidationError):
        ApiError(code=ErrorCode.invalid_input, message="bad", unknown_field=True)
    with pytest.raises(ValidationError):
        FactResolutionRequest(workspace_id=1, as_of=datetime(2026, 1, 1))


def test_fact_service_returns_authoritative_resolution_dto():
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        workspace = Workspace(name="T06 contract workspace")
        db.add(workspace)
        db.flush()
        db.add(
            Fact(
                workspace_id=workspace.id,
                series_id="pressure-series",
                subject="Valve-A",
                predicate="pressure",
                value="20",
                unit="bar",
                source_id="fixture",
                source_locator="fixture://valve-a/pressure",
                visibility=FactVisibility.public.value,
                status=FactStatus.confirmed.value,
                version=1,
                valid_from=now,
            )
        )
        db.commit()
        result = ApplicationServices(db, request_id="t06-fact").facts().resolve(workspace.id, as_of=now)

    assert result.workspace_id == workspace.id
    assert result.as_of.tzinfo is not None
    assert len(result.facts) == 1
    assert result.facts[0].series_id == "pressure-series"
    assert result.facts[0].version == 1
    assert result.conflicts == []


def _confirmed_fact(value: str = "20") -> ConfirmedFact:
    now = datetime.now(timezone.utc)
    return ConfirmedFact(
        id=11,
        workspace_id=4,
        series_id="moq-series",
        version=1,
        subject="Valve-A",
        predicate="minimum_order_quantity",
        value=value,
        unit="pieces",
        source_id="fixture",
        source_locator="fixture://valve-a/moq",
        visibility=FactVisibility.public.value,
        status=FactStatus.confirmed.value,
        valid_from=now,
        valid_until=None,
    )


def test_content_service_uses_existing_numeric_fact_guard():
    service = ApplicationServices(request_id="t06-content").content()
    valid = service.validate(
        ContentDraftContract(field_diff={"body": "Minimum order: 20 pieces."}),
        [_confirmed_fact()],
    )
    invalid = service.validate(
        ContentDraftContract(field_diff={"body": "Minimum order: 21 pieces."}),
        [_confirmed_fact()],
    )
    assert valid.valid is True
    assert valid.status == "valid"
    assert invalid.valid is False
    assert invalid.status == "rejected"
    assert invalid.issues


def test_visibility_service_preserves_metric_version_and_citation_semantics():
    metrics = ApplicationServices(request_id="t06-visibility").visibility().calculate(
        [
            {
                "question_id": "q-1",
                "status": "succeeded",
                "answer_text": "Valve-A is listed at example.com",
                "citations": [{"url": "https://example.com/catalog", "kind": "native"}],
                "answered_question": True,
                "cost_usd": "0.010000",
            }
        ],
        site_url="https://example.com",
        brand_terms=["Valve-A"],
        planned_samples=1,
    )
    assert isinstance(metrics, VisibilityMetricsContract)
    assert metrics.metric_version == "visibility-metrics-v2"
    assert metrics.sample_success.numerator == 1
    assert metrics.validated_site_citation.numerator == 1
    assert metrics.cost["unknown_actual_cost_sample_count"] == 0


def test_publication_service_emits_stable_stale_version_error():
    service = ApplicationServices(request_id="t06-publication").publication()
    with pytest.raises(ApplicationServiceError) as raised:
        service.check_expected_version(current_version=2, expected_version=1)
    error = raised.value.as_contract()
    assert error.code == ErrorCode.stale_version
    assert error.request_id == "t06-publication"
    assert error.expected_version == 1
    assert error.current_version == 2

    command = PublicationCommand(
        workspace_id=1,
        change_request_id=2,
        revision_id=3,
        expected_version=4,
        target="fixture",
        idempotency_key="change:2:revision:3:target:fixture",
    )
    assert command.version_guard.expected_version == 4

