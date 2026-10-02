"""Authoritative resolution of versioned workspace facts.

Facts are append-only rows.  A series is the aggregate identified by
``(workspace_id, series_id)``; this module is the only place that decides
which confirmed row is effective at a point in time.  Callers must treat an
overlap as a conflict instead of choosing a row by query ordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Fact, FactStatus, FactVisibility
from ..time_utils import as_utc


class FactResolutionError(RuntimeError):
    """A fact series cannot be resolved without guessing business intent."""


@dataclass(frozen=True, slots=True)
class FactConflict:
    workspace_id: int
    series_id: str
    fact_ids: tuple[int, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class FactResolution:
    """Result for one series. ``fact`` is None when it is absent or conflicted."""

    workspace_id: int
    series_id: str
    fact: Fact | None
    conflict: FactConflict | None = None

    @property
    def is_current(self) -> bool:
        return self.fact is not None and self.conflict is None


def _now(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return as_utc(value)


def _scope_key(fact: Fact) -> tuple[str, str, str | None]:
    # These fields are the existing applicability scope.  A later schema can
    # add an explicit scope JSON field without changing resolver semantics.
    return (fact.subject.strip(), fact.predicate.strip(), fact.unit)


def _effective_until(row: Fact, rows: Sequence[Fact]) -> datetime | None:
    """Return the implicit replacement boundary for an open-ended revision.

    An open-ended v1 is superseded by a later append-only version. Explicit
    windows are never rewritten and remain eligible for overlap diagnostics.
    """
    if row.valid_until is not None:
        return as_utc(row.valid_until)
    later = [as_utc(other.valid_from) for other in rows if as_utc(other.valid_from) > as_utc(row.valid_from)]
    return min(later) if later else None


def _resolve_rows(
    workspace_id: int,
    series_id: str,
    rows: Sequence[Fact],
    as_of: datetime,
) -> FactResolution:
    scope_groups: dict[tuple[str, str, str | None], list[Fact]] = {}
    for row in rows:
        scope_groups.setdefault(_scope_key(row), []).append(row)
    if len(scope_groups) > 1:
        ids = tuple(sorted(row.id for row in rows))
        return FactResolution(
            workspace_id,
            series_id,
            None,
            FactConflict(workspace_id, series_id, ids, "series contains multiple applicability scopes"),
        )

    eligible: list[Fact] = []
    for row in rows:
        if row.status != FactStatus.confirmed.value:
            continue
        start = as_utc(row.valid_from)
        end = _effective_until(row, rows)
        if start <= as_of and (end is None or as_of < end):
            eligible.append(row)
    if not eligible:
        return FactResolution(workspace_id, series_id, None)
    if len(eligible) > 1:
        ids = tuple(sorted(row.id for row in eligible))
        return FactResolution(
            workspace_id,
            series_id,
            None,
            FactConflict(workspace_id, series_id, ids, "confirmed validity windows overlap"),
        )
    return FactResolution(workspace_id, series_id, eligible[0])


def resolve_current_facts(
    db: Session,
    workspace_id: int,
    *,
    as_of: datetime | None = None,
    series_ids: Iterable[str] | None = None,
    subject: str | None = None,
    predicate: str | None = None,
    visibility: str | None = FactVisibility.public.value,
    include_conflicts: bool = False,
) -> tuple[list[Fact], list[FactConflict]]:
    """Resolve effective facts and return explicit overlap diagnostics.

    Rejected/expired/proposed rows remain queryable history but can never be
    returned as current.  ``include_conflicts`` leaves conflicted series out
    of the facts list while exposing their diagnostics to migration tooling.
    """
    point = _now(as_of)
    query = select(Fact).where(Fact.workspace_id == workspace_id)
    if series_ids is not None:
        values = sorted({str(value) for value in series_ids})
        if not values:
            return [], []
        query = query.where(Fact.series_id.in_(values))
    if subject is not None:
        query = query.where(Fact.subject == subject)
    if predicate is not None:
        query = query.where(Fact.predicate == predicate)
    if visibility is not None:
        query = query.where(Fact.visibility == visibility)
    rows = db.scalars(query.order_by(Fact.series_id, Fact.version)).all()
    grouped: dict[str, list[Fact]] = {}
    for row in rows:
        grouped.setdefault(row.series_id, []).append(row)
    resolved: list[Fact] = []
    conflicts: list[FactConflict] = []
    for key, candidates in grouped.items():
        result = _resolve_rows(workspace_id, key, candidates, point)
        if result.conflict is not None:
            conflicts.append(result.conflict)
        elif result.fact is not None:
            resolved.append(result.fact)
    if conflicts and not include_conflicts:
        # Conflicts are deliberately excluded, while still returned to make
        # the caller's refusal observable and actionable.
        pass
    return resolved, conflicts


def resolve_current_fact(
    db: Session,
    workspace_id: int,
    series_id: str,
    *,
    as_of: datetime | None = None,
    visibility: str | None = FactVisibility.public.value,
) -> Fact | None:
    facts, conflicts = resolve_current_facts(
        db, workspace_id, as_of=as_of, series_ids=[series_id], visibility=visibility
    )
    if conflicts:
        raise FactResolutionError(conflicts[0].reason)
    return facts[0] if facts else None


def assert_binding_current(
    db: Session,
    workspace_id: int,
    binding: Mapping[str, object],
    *,
    as_of: datetime | None = None,
    require_public: bool = True,
) -> Fact:
    """Validate an immutable revision binding against the authoritative resolver."""
    try:
        fact_id = int(binding["fact_id"])
        series_id = str(binding["series_id"])
        version = int(binding["version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FactResolutionError("fact binding requires fact_id, series_id, and version") from exc
    fact = db.get(Fact, fact_id)
    if fact is None or fact.workspace_id != workspace_id or fact.series_id != series_id or fact.version != version:
        raise FactResolutionError("bound fact is outside the workspace or no longer has the bound version")
    if require_public and fact.visibility != FactVisibility.public.value:
        raise FactResolutionError("bound fact is not public")
    current = resolve_current_fact(
        db,
        workspace_id,
        series_id,
        as_of=as_of,
        visibility=FactVisibility.public.value if require_public else None,
    )
    if current is None or current.id != fact.id:
        raise FactResolutionError("bound fact version is stale or not currently effective")
    return fact


def assert_bindings_current(
    db: Session,
    workspace_id: int,
    bindings: Sequence[Mapping[str, object]],
    *,
    as_of: datetime | None = None,
    require_public: bool = True,
) -> list[Fact]:
    return [
        assert_binding_current(db, workspace_id, binding, as_of=as_of, require_public=require_public)
        for binding in bindings
    ]
