"""Durable model-call accounting and short budget reservations.

The helpers intentionally keep network calls outside the database transaction.  A
caller reserves and commits before invoking a provider, then records the result
in a second short transaction.  An absent price is represented as
``unknown_result`` and never silently converted to zero.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import func, select

from ..models import BudgetReservation, BudgetReservationStatus, ModelCall, ModelCallStatus, Workspace, utcnow


class AccountingError(ValueError):
    """Invalid accounting input or an unsafe budget operation."""


class BudgetExceeded(AccountingError):
    """The requested reservation would exceed the configured workspace budget."""


def money(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AccountingError("amount must be a decimal") from exc
    if not result.is_finite() or result < 0:
        raise AccountingError("amount must be a finite non-negative decimal")
    return result.quantize(Decimal("0.000001"))


def payload_hash(payload: Any) -> str:
    if isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _active_reserved(db, workspace_id: int, currency: str) -> Decimal:
    value = db.scalar(
        select(func.coalesce(func.sum(BudgetReservation.reserved_amount), 0)).where(
            BudgetReservation.workspace_id == workspace_id,
            BudgetReservation.currency == currency,
            BudgetReservation.status.in_((BudgetReservationStatus.reserved.value, BudgetReservationStatus.unknown_result.value)),
        )
    )
    return money(value)


def reserve_budget(
    db,
    *,
    workspace_id: int,
    reservation_key: str,
    amount: Decimal | int | float | str,
    budget_limit: Decimal | int | float | str,
    currency: str = "USD",
    model_call_id: int | None = None,
    expires_at: datetime | None = None,
) -> BudgetReservation:
    """Create or return an idempotent reservation before a provider call."""
    if not reservation_key.strip():
        raise AccountingError("reservation_key is required")
    currency = currency.strip().upper()
    if not currency or len(currency) > 12:
        raise AccountingError("currency is invalid")
    requested = money(amount)
    limit = money(budget_limit)
    # Serialize reservations for a workspace on PostgreSQL.  A read-then-insert
    # check without a lock lets two concurrent callers observe the same active
    # total and over-commit the configured budget.  SQLite ignores
    # ``FOR UPDATE`` but still serializes its writes; the explicit workspace
    # existence check keeps the failure deterministic on both databases.
    workspace = db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
    if workspace is None:
        raise AccountingError("workspace not found")
    existing = db.scalar(
        select(BudgetReservation).where(
            BudgetReservation.workspace_id == workspace_id,
            BudgetReservation.reservation_key == reservation_key,
        )
    )
    if existing is not None:
        if money(existing.reserved_amount) != requested or existing.currency != currency:
            raise AccountingError("reservation key was reused with different budget inputs")
        return existing
    if _active_reserved(db, workspace_id, currency) + requested > limit:
        raise BudgetExceeded("budget reservation exceeds the configured limit")
    reservation = BudgetReservation(
        workspace_id=workspace_id,
        reservation_key=reservation_key,
        model_call_id=model_call_id,
        reserved_amount=requested,
        currency=currency,
        status=BudgetReservationStatus.reserved.value,
        expires_at=expires_at,
    )
    db.add(reservation)
    db.flush()
    return reservation


def start_model_call(
    db,
    *,
    workspace_id: int,
    call_key: str,
    call_type: str,
    input_value: Any,
    estimated_cost: Decimal | int | float | str | None,
    budget_limit: Decimal | int | float | str,
    currency: str = "USD",
    generation_id: str | None = None,
    sample_id: str | None = None,
    attempt: int = 1,
    price_version: str | None = None,
    cost_source: str | None = None,
    strict_budget: bool = True,
) -> ModelCall:
    """Persist a call and its reservation; commit before doing network work."""
    if not call_key.strip() or not call_type.strip():
        raise AccountingError("call_key and call_type are required")
    if attempt < 1:
        raise AccountingError("attempt must be positive")
    existing = db.scalar(select(ModelCall).where(ModelCall.workspace_id == workspace_id, ModelCall.call_key == call_key))
    if existing is not None:
        if existing.input_hash != payload_hash(input_value):
            raise AccountingError("call key was reused with different input")
        return existing
    if estimated_cost is None and strict_budget:
        raise BudgetExceeded("strict budget mode requires a known cost estimate")
    estimate = money(estimated_cost)
    call = ModelCall(
        workspace_id=workspace_id,
        call_key=call_key,
        call_type=call_type,
        generation_id=generation_id,
        sample_id=sample_id,
        input_hash=payload_hash(input_value),
        attempt=attempt,
        currency=currency.strip().upper(),
        price_version=price_version,
        cost_source=cost_source,
        cost_known=estimated_cost is not None,
        amount=estimate if estimated_cost is not None else None,
        status=ModelCallStatus.reserved.value,
    )
    db.add(call)
    db.flush()
    reserve_budget(
        db,
        workspace_id=workspace_id,
        reservation_key=call_key,
        amount=estimate,
        budget_limit=budget_limit,
        currency=call.currency,
        model_call_id=call.id,
    )
    return call


def finish_model_call(
    db,
    call_id: int,
    *,
    status: str,
    output_value: Any = None,
    provider_request_id: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    actual_cost: Decimal | int | float | str | None = None,
    cost_known: bool | None = None,
    price_version: str | None = None,
    cost_source: str | None = None,
    latency_ms: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    completed_at: datetime | None = None,
) -> ModelCall:
    """Settle a call.  ``actual_cost=None`` creates an explicit unknown result."""
    call = db.get(ModelCall, call_id)
    if call is None:
        raise AccountingError("model call not found")
    if call.status in (ModelCallStatus.succeeded.value, ModelCallStatus.failed.value):
        return call
    # An unknown provider result remains reserved until a billing ledger or
    # provider reconciliation supplies an actual amount.  Reconciliation is
    # intentionally idempotent and uses this same settlement path.
    if call.status == ModelCallStatus.unknown_result.value and actual_cost is None and cost_known is not True:
        return call
    if status not in {item.value for item in ModelCallStatus if item is not ModelCallStatus.reserved}:
        raise AccountingError("invalid model call terminal status")
    known = actual_cost is not None if cost_known is None else bool(cost_known)
    call.status = status if known else ModelCallStatus.unknown_result.value
    call.cost_known = known
    call.amount = money(actual_cost) if known and actual_cost is not None else None
    call.output_hash = payload_hash(output_value) if output_value is not None else None
    call.provider_request_id = provider_request_id
    call.input_tokens = input_tokens
    call.output_tokens = output_tokens
    if price_version is not None:
        call.price_version = price_version
    if cost_source is not None:
        call.cost_source = cost_source
    call.latency_ms = latency_ms
    call.error_code = error_code
    call.error_message = error_message[:2000] if error_message else None
    call.completed_at = completed_at or utcnow()
    reservation = db.scalar(
        select(BudgetReservation).where(
            BudgetReservation.workspace_id == call.workspace_id,
            BudgetReservation.reservation_key == call.call_key,
        )
    )
    if reservation is not None:
        reservation.status = BudgetReservationStatus.settled.value if known else BudgetReservationStatus.unknown_result.value
        reservation.settled_amount = call.amount
        reservation.settled_at = call.completed_at
    db.flush()
    return call


def usage_summary(db, *, workspace_id: int, currency: str = "USD") -> Mapping[str, Decimal | int]:
    rows = db.scalars(select(ModelCall).where(ModelCall.workspace_id == workspace_id, ModelCall.currency == currency)).all()
    known = [money(row.amount) for row in rows if row.cost_known and row.amount is not None]
    return {
        "calls": len(rows),
        "known_cost": sum(known, Decimal("0")).quantize(Decimal("0.000001")),
        "unknown_calls": sum(not row.cost_known for row in rows),
        "active_reserved": _active_reserved(db, workspace_id, currency),
    }
