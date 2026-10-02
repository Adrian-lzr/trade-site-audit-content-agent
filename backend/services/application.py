"""Application-service boundary for content, facts, visibility, and publish.

This module is deliberately independent of FastAPI.  Routes and workers can
construct these services with the same database session and request context,
while existing legacy route functions remain compatible until migrated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.orm import Session

from ..content_workflow import ConfirmedFact, validate_structured_draft
from ..contracts.common import ApiError, ErrorCode
from ..contracts.content import ContentDraftContract, ContentValidationContract
from ..contracts.facts import (
    FactBindingContract,
    FactConflictContract,
    FactResolutionContract,
)
from ..contracts.visibility import MetricValueContract, VisibilityMetricsContract
from ..services.facts import (
    FactResolutionError,
    assert_bindings_current,
    resolve_current_facts,
)
from ..services.visibility_metrics import calculate_visibility_metrics
from ..time_utils import as_utc, require_aware_utc


class ApplicationServiceError(RuntimeError):
    """Stable error raised at the application boundary."""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        *,
        request_id: str | None = None,
        expected_version: int | None = None,
        current_version: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id
        self.expected_version = expected_version
        self.current_version = current_version
        self.details = dict(details or {})

    def as_contract(self) -> ApiError:
        return ApiError(
            code=self.code,
            message=self.message,
            request_id=self.request_id,
            expected_version=self.expected_version,
            current_version=self.current_version,
            details=self.details,
        )


def _fact_contract(fact: Any) -> FactBindingContract:
    return FactBindingContract(
        fact_id=int(fact.id),
        workspace_id=int(fact.workspace_id),
        series_id=str(fact.series_id),
        version=int(fact.version),
        subject=str(fact.subject),
        predicate=str(fact.predicate),
        value=str(fact.value),
        unit=fact.unit,
        source_id=str(fact.source_id),
        source_locator=str(fact.source_locator),
        visibility=str(fact.visibility),
        status=str(fact.status),
        valid_from=as_utc(fact.valid_from),
        valid_until=as_utc(fact.valid_until),
    )


class FactApplicationService:
    """Use the authoritative fact resolver for API and worker callers."""

    def __init__(self, db: Session, *, request_id: str | None = None) -> None:
        self.db = db
        self.request_id = request_id

    def resolve(
        self,
        workspace_id: int,
        *,
        as_of: datetime | None = None,
        series_ids: Iterable[str] | None = None,
        subject: str | None = None,
        predicate: str | None = None,
        visibility: str | None = "public",
    ) -> FactResolutionContract:
        if workspace_id < 1:
            raise ApplicationServiceError(
                ErrorCode.invalid_input,
                "workspace_id must be positive",
                request_id=self.request_id,
            )
        if as_of is not None:
            try:
                as_of = require_aware_utc(as_of)
            except ValueError as exc:
                raise ApplicationServiceError(
                    ErrorCode.invalid_input,
                    str(exc),
                    request_id=self.request_id,
                ) from exc
        point = as_utc(as_of) or datetime.now(timezone.utc)
        rows, conflicts = resolve_current_facts(
            self.db,
            workspace_id,
            as_of=point,
            series_ids=series_ids,
            subject=subject,
            predicate=predicate,
            visibility=visibility,
        )
        return FactResolutionContract(
            workspace_id=workspace_id,
            as_of=point,
            facts=[_fact_contract(row) for row in rows],
            conflicts=[
                FactConflictContract(
                    workspace_id=item.workspace_id,
                    series_id=item.series_id,
                    fact_ids=item.fact_ids,
                    reason=item.reason,
                )
                for item in conflicts
            ],
        )

    def require_bindings(
        self,
        workspace_id: int,
        bindings: Sequence[Mapping[str, object]],
        *,
        as_of: datetime | None = None,
        require_public: bool = True,
    ) -> list[FactBindingContract]:
        if as_of is not None:
            try:
                as_of = require_aware_utc(as_of)
            except ValueError as exc:
                raise ApplicationServiceError(
                    ErrorCode.invalid_input,
                    str(exc),
                    request_id=self.request_id,
                ) from exc
        try:
            rows = assert_bindings_current(
                self.db,
                workspace_id,
                bindings,
                as_of=as_of,
                require_public=require_public,
            )
        except FactResolutionError as exc:
            message = str(exc)
            code = ErrorCode.fact_conflict if "conflict" in message or "overlap" in message else ErrorCode.fact_not_current
            raise ApplicationServiceError(code, message, request_id=self.request_id) from exc
        return [_fact_contract(row) for row in rows]


class ContentApplicationService:
    """Validate structured drafts against facts without depending on HTTP."""

    def __init__(self, *, request_id: str | None = None) -> None:
        self.request_id = request_id

    def validate(
        self,
        draft: ContentDraftContract | Mapping[str, Any],
        facts: Sequence[ConfirmedFact],
    ) -> ContentValidationContract:
        if not isinstance(draft, ContentDraftContract):
            try:
                draft = ContentDraftContract.model_validate(draft)
            except Exception as exc:  # Pydantic's detailed errors are a stable caller concern.
                raise ApplicationServiceError(
                    ErrorCode.invalid_input,
                    "content draft does not satisfy the contract",
                    request_id=self.request_id,
                    details={"validation_error": str(exc)},
                ) from exc
        issues = validate_structured_draft(draft.field_diff, facts)
        return ContentValidationContract(
            schema_version=draft.schema_version,
            status="valid" if not issues else "rejected",
            valid=not issues,
            issues=issues,
            checked_fact_ids=[fact.id for fact in facts],
        )


def _metric(value: Mapping[str, Any]) -> MetricValueContract:
    return MetricValueContract.model_validate(value)


class VisibilityApplicationService:
    """Versioned, pure visibility metric calculation for routes and workers."""

    def __init__(self, *, request_id: str | None = None) -> None:
        self.request_id = request_id

    def calculate(
        self,
        samples: Iterable[Any],
        *,
        site_url: str,
        brand_terms: Iterable[str] = (),
        planned_samples: int | None = None,
        expected_questions: Iterable[Any] | None = None,
        allowed_site_hosts: Iterable[str] | None = None,
        allow_subdomains: bool = True,
        is_synthetic: bool = False,
        provider_kind: str | None = None,
    ) -> VisibilityMetricsContract:
        try:
            raw = calculate_visibility_metrics(
                samples,
                site_url=site_url,
                brand_terms=brand_terms,
                planned_samples=planned_samples,
                expected_questions=expected_questions,
                allowed_site_hosts=allowed_site_hosts,
                allow_subdomains=allow_subdomains,
                is_synthetic=is_synthetic,
                provider_kind=provider_kind,
            )
        except (TypeError, ValueError) as exc:
            raise ApplicationServiceError(
                ErrorCode.invalid_input,
                "visibility samples do not satisfy the metric contract",
                request_id=self.request_id,
                details={"error": str(exc)},
            ) from exc
        known = {
            "metric_version",
            "site_host",
            "site_host_valid",
            "subdomains_allowed",
            "sample_counts",
            "sample_success",
            "brand_mention",
            "domain_mention",
            "validated_site_citation",
            "answered_question",
            "question_counts",
            "citation_details",
            "cost",
            "evidence_strata",
        }
        return VisibilityMetricsContract(
            metric_version=str(raw["metric_version"]),
            site_host=raw.get("site_host"),
            site_host_valid=bool(raw.get("site_host_valid")),
            subdomains_allowed=bool(raw.get("subdomains_allowed")),
            sample_counts=dict(raw.get("sample_counts") or {}),
            sample_success=_metric(raw["sample_success"]),
            brand_mention=_metric(raw["brand_mention"]),
            domain_mention=_metric(raw["domain_mention"]),
            validated_site_citation=_metric(raw["validated_site_citation"]),
            answered_question=dict(raw.get("answered_question") or {}),
            question_counts=dict(raw.get("question_counts") or {}),
            citation_details=dict(raw.get("citation_details") or {}),
            cost=dict(raw.get("cost") or {}),
            evidence_strata=dict(raw.get("evidence_strata") or {}),
            details={key: value for key, value in raw.items() if key not in known},
        )


class PublicationApplicationService:
    """Shared optimistic-lock guard used by publish routes and workers."""

    def __init__(self, *, request_id: str | None = None) -> None:
        self.request_id = request_id

    def check_expected_version(self, current_version: int, expected_version: int | None) -> int:
        if current_version < 1 or expected_version is None or expected_version < 1:
            raise ApplicationServiceError(
                ErrorCode.invalid_input,
                "expected_version is required and must be positive",
                request_id=self.request_id,
                current_version=current_version if current_version > 0 else None,
            )
        if current_version != expected_version:
            raise ApplicationServiceError(
                ErrorCode.stale_version,
                "resource version is stale; refresh before retrying",
                request_id=self.request_id,
                expected_version=expected_version,
                current_version=current_version,
            )
        return current_version


@dataclass(slots=True)
class ApplicationServices:
    """Composition root shared by an API request or one worker attempt."""

    db: Session | None = None
    request_id: str | None = None

    def facts(self) -> FactApplicationService:
        if self.db is None:
            raise ApplicationServiceError(ErrorCode.internal_error, "a database session is required", request_id=self.request_id)
        return FactApplicationService(self.db, request_id=self.request_id)

    def content(self) -> ContentApplicationService:
        return ContentApplicationService(request_id=self.request_id)

    def visibility(self) -> VisibilityApplicationService:
        return VisibilityApplicationService(request_id=self.request_id)

    def publication(self) -> PublicationApplicationService:
        return PublicationApplicationService(request_id=self.request_id)


__all__ = [
    "ApplicationServiceError",
    "ApplicationServices",
    "ContentApplicationService",
    "FactApplicationService",
    "PublicationApplicationService",
    "VisibilityApplicationService",
]

