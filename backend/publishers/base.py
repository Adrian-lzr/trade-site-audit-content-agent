"""Contracts shared by controlled publication adapters.

The publisher boundary intentionally carries the values that make a write
reviewable: the exact source revision, the source file hash, the approved
revision hash, and the fact set used to produce the content.  Adapters are
expected to implement a compare-and-swap write and to expose a separate
inspection result after the write.

No class in this module performs network or CMS operations.  A concrete
adapter owns its target allowlist and decides how a target is materialized.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


class PublisherError(RuntimeError):
    """Base class for controlled publisher failures."""


class ManifestError(PublisherError):
    """The server-side target manifest is invalid or incomplete."""


class UnauthorizedTargetError(PublisherError):
    """A requested site, page, repository, file, or field is not allowlisted."""


class InvalidPublicationError(PublisherError):
    """A proposed field value cannot be safely represented by the adapter."""


class PublicationConflictError(PublisherError):
    """A compare-and-swap precondition no longer matches the target."""


class ReviewRequiredError(PublisherError):
    """The requested apply does not match the prepared, approved artifact."""


class IdempotencyConflictError(PublisherError):
    """An idempotency key has already been used for different content."""


class RollbackConflictError(PublisherError):
    """The target changed before a guarded rollback could be applied."""


def utc_now() -> datetime:
    """Return an aware UTC timestamp for result metadata."""

    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> str:
    """Serialize provenance deterministically without accepting opaque bytes."""

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)
    except (TypeError, ValueError) as exc:
        raise InvalidPublicationError("publication provenance must be JSON serializable") from exc


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, set | frozenset):
        return sorted(value)
    raise TypeError(f"unsupported provenance value: {type(value).__name__}")


def content_hash(value: str | bytes) -> str:
    """Hash the exact UTF-8 file/field bytes used by the adapter."""

    payload = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class PublicationRequest:
    """Input to :meth:`PublisherAdapter.prepare`.

    ``current_facts`` deliberately has no domain-specific type.  The facts
    are copied into provenance and hashed as supplied by the trusted service;
    callers cannot use the request to select a different repository or file.
    ``None`` means that the caller failed to bind a fact set and is rejected by
    concrete adapters.  An empty list is a valid, explicit "no facts" binding
    for a low-risk editorial change.
    """

    site: str
    page: str
    fields: Mapping[str, Any]
    expected_base_content_hash: str
    revision_hash: str
    current_facts: Any = None
    expected_base_commit: str | None = None
    expected_field_hashes: Mapping[str, str] = field(default_factory=dict)
    idempotency_key: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PublicationApproval:
    """The minimum approval binding accepted by ``apply``."""

    revision_hash: str
    fields: Mapping[str, Any]
    current_facts: Any = None
    approver: str | None = None
    approval_id: str | None = None


@dataclass(frozen=True)
class PreparedPublication:
    """A reviewable artifact produced without changing the target file."""

    site: str
    page: str
    repository: str
    file: str
    fields: Mapping[str, Any]
    field_hashes_before: Mapping[str, str]
    field_hashes_after: Mapping[str, str]
    expected_base_commit: str | None
    actual_base_commit: str | None
    expected_base_content_hash: str
    actual_base_content_hash: str
    revision_hash: str
    current_facts: Any
    current_fact_set_hash: str
    before_content: str
    after_content: str
    diff: str
    artifact_hash: str
    provenance: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    prepared_at: datetime = field(default_factory=utc_now)

    @property
    def target(self) -> str:
        """Stable target label used by workers and audit records."""

        return f"{self.site}:{self.page}"

    @property
    def status(self) -> str:
        """Lifecycle label used by review and worker integrations."""

        return "prepared"

    @property
    def review_artifact(self) -> Mapping[str, Any]:
        """JSON-ready review record, including provenance and the real diff."""

        return {
            "status": self.status,
            "site": self.site,
            "page": self.page,
            "repository": self.repository,
            "file": self.file,
            "revision_hash": self.revision_hash,
            "expected_base_commit": self.expected_base_commit,
            "actual_base_commit": self.actual_base_commit,
            "expected_base_content_hash": self.expected_base_content_hash,
            "actual_base_content_hash": self.actual_base_content_hash,
            "current_fact_set_hash": self.current_fact_set_hash,
            "fields": dict(self.fields),
            "field_hashes_before": dict(self.field_hashes_before),
            "field_hashes_after": dict(self.field_hashes_after),
            "artifact_hash": self.artifact_hash,
            "diff": self.diff,
            "provenance": dict(self.provenance),
            "idempotency_key": self.idempotency_key,
            "prepared_at": self.prepared_at.isoformat(),
        }

    def to_review_json(self) -> str:
        """Serialize the review artifact for an audit store."""

        return canonical_json(self.review_artifact)

    @property
    def content_hash(self) -> str:
        return content_hash(self.after_content)

    def approval(self, *, approver: str | None = None, approval_id: str | None = None) -> PublicationApproval:
        """Create an approval for this exact prepared content."""

        return PublicationApproval(
            revision_hash=self.revision_hash,
            fields=dict(self.fields),
            current_facts=self.current_facts,
            approver=approver,
            approval_id=approval_id,
        )


@dataclass(frozen=True)
class ApplyResult:
    """Result of a real file write or an idempotent historical replay."""

    site: str
    page: str
    repository: str
    file: str
    status: str
    revision_hash: str
    applied_content_hash: str
    base_content_hash: str
    base_commit: str | None
    current_fact_set_hash: str
    idempotency_key: str
    changed: bool
    replayed: bool
    provenance: Mapping[str, Any] = field(default_factory=dict)
    applied_at: datetime = field(default_factory=utc_now)

    @property
    def historical_replay(self) -> bool:
        """Whether this result came from an earlier successful request."""

        return self.replayed


@dataclass(frozen=True)
class InspectionResult:
    """Fresh read of a target page after prepare/apply/deployment."""

    site: str
    page: str
    repository: str
    file: str
    status: str
    matches_prepared: bool
    content_hash: str
    expected_content_hash: str | None
    base_content_hash: str | None
    actual_base_commit: str | None
    field_hashes: Mapping[str, str]
    mismatches: tuple[str, ...] = ()
    inspected_at: datetime = field(default_factory=utc_now)

    @property
    def verified(self) -> bool:
        return self.matches_prepared and self.status == "verified"


@dataclass(frozen=True)
class RollbackResult:
    """Result of restoring the exact pre-apply content."""

    site: str
    page: str
    repository: str
    file: str
    status: str
    revision_hash: str
    restored_content_hash: str
    expected_current_content_hash: str
    idempotency_key: str
    changed: bool
    replayed: bool
    provenance: Mapping[str, Any] = field(default_factory=dict)
    rolled_back_at: datetime = field(default_factory=utc_now)


class PublisherAdapter(ABC):
    """Adapter contract for prepare/apply/inspect/rollback publication."""

    @abstractmethod
    def prepare(self, request: PublicationRequest | None = None, **kwargs: Any) -> PreparedPublication:
        """Validate and render a reviewable artifact without writing a target."""

    @abstractmethod
    def apply(
        self,
        prepared: PreparedPublication,
        approval: PublicationApproval | Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> ApplyResult:
        """Apply an approved artifact with compare-and-swap checks."""

    @abstractmethod
    def inspect(
        self,
        prepared: PreparedPublication | ApplyResult,
        **kwargs: Any,
    ) -> InspectionResult:
        """Read the target and compare it with an expected artifact."""

    @abstractmethod
    def rollback(
        self,
        applied: ApplyResult | PreparedPublication,
        **kwargs: Any,
    ) -> RollbackResult:
        """Restore the exact pre-apply content with a guarded write."""


# Short aliases make the contract convenient for callers that use the terms
# in the plan while preserving the more explicit names above.
PrepareRequest = PublicationRequest
Approval = PublicationApproval
PreparedArtifact = PreparedPublication


__all__ = [
    "ApplyResult",
    "Approval",
    "IdempotencyConflictError",
    "InspectionResult",
    "InvalidPublicationError",
    "ManifestError",
    "PrepareRequest",
    "PreparedArtifact",
    "PreparedPublication",
    "PublicationApproval",
    "PublicationConflictError",
    "PublicationRequest",
    "PublisherAdapter",
    "PublisherError",
    "ReviewRequiredError",
    "RollbackConflictError",
    "RollbackResult",
    "UnauthorizedTargetError",
    "canonical_json",
    "content_hash",
    "utc_now",
]
