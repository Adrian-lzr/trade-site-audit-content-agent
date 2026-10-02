from datetime import timedelta

from backend.content_workflow import ConfirmedFact, validate_structured_draft
from backend.services.claims import validate_claim_bindings
from backend.time_utils import utcnow


def _fact(*, predicate="minimum_order_quantity", value="20", unit="pieces", subject="VX-21"):
    return ConfirmedFact(
        id=1,
        workspace_id=1,
        series_id="series-1",
        version=2,
        subject=subject,
        predicate=predicate,
        value=value,
        unit=unit,
        source_id="catalog",
        source_locator="csv:catalog#row=2",
        visibility="public",
        status="confirmed",
        valid_from=utcnow() - timedelta(days=1),
        valid_until=None,
    )


def test_high_risk_certification_without_evidence_is_rejected():
    issues = validate_structured_draft({"body": "VX-21 is FDA approved."}, [_fact()])
    assert any("unsupported certification" in issue for issue in issues)


def test_quantity_cannot_be_relabelled_as_pressure_or_percentage():
    facts = [_fact()]
    assert validate_structured_draft({"body": "Minimum order: 20 pieces."}, facts) == []
    issues = validate_structured_draft({"body": "Working pressure: 20 bar (20%)."}, facts)
    assert any("incompatible unit category" in issue for issue in issues)


def test_explicit_binding_must_match_authoritative_version_and_source():
    fact = _fact()
    binding = {
        "claim_id": "c1",
        "fact_id": 1,
        "fact_version": 1,
        "series_id": "series-1",
        "subject": "VX-21",
        "predicate": "minimum_order_quantity",
        "value": "20",
        "unit": "pieces",
        "source_locator": "csv:catalog#row=2",
    }
    issues = validate_claim_bindings([binding], [fact])
    assert any("version" in issue for issue in issues)
