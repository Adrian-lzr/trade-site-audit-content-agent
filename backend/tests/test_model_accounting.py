from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.database import SessionLocal
from backend.models import BudgetReservation, ModelCall, Workspace
from backend.services.accounting import AccountingError, BudgetExceeded, finish_model_call, reserve_budget, start_model_call, usage_summary


def _workspace() -> int:
    with SessionLocal() as db:
        workspace = Workspace(name="accounting")
        db.add(workspace)
        db.commit()
        return workspace.id


def test_model_call_is_idempotent_and_known_cost_settles_reservation():
    workspace_id = _workspace()
    with SessionLocal() as db:
        call = start_model_call(
            db,
            workspace_id=workspace_id,
            call_key="generation-1",
            call_type="content_generation",
            input_value={"question": "pressure"},
            estimated_cost="0.040000",
            budget_limit="0.100000",
        )
        db.commit()
        call_id = call.id
    with SessionLocal() as db:
        replay = start_model_call(
            db,
            workspace_id=workspace_id,
            call_key="generation-1",
            call_type="content_generation",
            input_value={"question": "pressure"},
            estimated_cost="0.040000",
            budget_limit="0.100000",
        )
        assert replay.id == call_id
        finish_model_call(db, call_id, status="succeeded", output_value={"title": "Valve"}, actual_cost="0.031250")
        db.commit()
        summary = usage_summary(db, workspace_id=workspace_id)
        reservation = db.scalar(select(BudgetReservation).where(BudgetReservation.model_call_id == call_id))
        assert summary["known_cost"] == Decimal("0.031250")
        assert summary["unknown_calls"] == 0
        assert reservation is not None and reservation.status == "settled"


def test_strict_budget_rejects_unknown_price_and_overcommit():
    workspace_id = _workspace()
    with SessionLocal() as db:
        with pytest.raises(BudgetExceeded, match="known cost"):
            start_model_call(
                db,
                workspace_id=workspace_id,
                call_key="unknown-price",
                call_type="visibility",
                input_value="question",
                estimated_cost=None,
                budget_limit="1",
            )
        start_model_call(
            db,
            workspace_id=workspace_id,
            call_key="one",
            call_type="visibility",
            input_value="one",
            estimated_cost="0.800000",
            budget_limit="1.000000",
        )
        db.commit()
    with SessionLocal() as db:
        with pytest.raises(BudgetExceeded):
            start_model_call(
                db,
                workspace_id=workspace_id,
                call_key="two",
                call_type="visibility",
                input_value="two",
                estimated_cost="0.300000",
                budget_limit="1.000000",
            )


def test_unknown_provider_result_is_explicit_and_keeps_budget_reserved():
    workspace_id = _workspace()
    with SessionLocal() as db:
        call = start_model_call(
            db,
            workspace_id=workspace_id,
            call_key="uncertain",
            call_type="content_generation",
            input_value="input",
            estimated_cost="0.200000",
            budget_limit="1.000000",
        )
        db.commit()
        call_id = call.id
    with SessionLocal() as db:
        finish_model_call(db, call_id, status="failed", error_code="timeout", actual_cost=None)
        db.commit()
        saved = db.get(ModelCall, call_id)
        summary = usage_summary(db, workspace_id=workspace_id)
        assert saved is not None and saved.status == "unknown_result" and not saved.cost_known
        assert summary["unknown_calls"] == 1
        assert summary["active_reserved"] == Decimal("0.200000")


def test_unknown_provider_result_can_be_reconciled_from_a_later_billing_ledger():
    """An uncertain network window stays reserved until an actual charge arrives."""

    workspace_id = _workspace()
    with SessionLocal() as db:
        call = start_model_call(
            db,
            workspace_id=workspace_id,
            call_key="reconcile-later",
            call_type="visibility",
            input_value={"question": "pressure"},
            estimated_cost="0.200000",
            budget_limit="1.000000",
        )
        db.commit()
        call_id = call.id

    with SessionLocal() as db:
        finish_model_call(db, call_id, status="failed", error_code="timeout", actual_cost=None)
        db.commit()

    with SessionLocal() as db:
        reconciled = finish_model_call(
            db,
            call_id,
            status="failed",
            provider_request_id="provider-ledger-42",
            actual_cost="0.125000",
            cost_source="provider_invoice",
            price_version="invoice-2026-10",
        )
        db.commit()
        summary = usage_summary(db, workspace_id=workspace_id)
        reservation = db.scalar(select(BudgetReservation).where(BudgetReservation.model_call_id == call_id))
        assert reconciled.status == "failed"
        assert reconciled.cost_known is True
        assert reconciled.amount == Decimal("0.125000")
        assert summary["unknown_calls"] == 0
        assert summary["known_cost"] == Decimal("0.125000")
        assert summary["active_reserved"] == Decimal("0.000000")
        assert reservation is not None and reservation.status == "settled"


def test_budget_reservation_requires_an_existing_workspace():
    with SessionLocal() as db:
        with pytest.raises(AccountingError, match="workspace not found"):
            reserve_budget(
                db,
                workspace_id=999999,
                reservation_key="missing-workspace",
                amount="0.010000",
                budget_limit="1.000000",
            )
