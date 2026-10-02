"""Stable transport contracts shared by API routes, workers, and clients.

The existing HTTP endpoints intentionally keep their legacy response shapes.
These models define the next application-service boundary without forcing a
flag-day route migration.  Contract models reject unknown top-level fields so
an accidental API drift is visible during contract tests.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


CONTRACT_VERSION = "2026-10-02"


class ContractModel(BaseModel):
    """Base model for stable service DTOs."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class ErrorCode(StrEnum):
    invalid_input = "invalid_input"
    unauthorized = "unauthorized"
    forbidden = "forbidden"
    not_found = "not_found"
    conflict = "conflict"
    stale_version = "stale_version"
    fact_not_current = "fact_not_current"
    fact_conflict = "fact_conflict"
    not_ready = "not_ready"
    external_unavailable = "external_unavailable"
    internal_error = "internal_error"


class RequestContext(ContractModel):
    """Request metadata passed across HTTP, service, and worker boundaries."""

    request_id: str | None = Field(default=None, min_length=1, max_length=120)
    workspace_id: int | None = Field(default=None, ge=1)
    actor_id: str | None = Field(default=None, min_length=1, max_length=255)
    task_id: str | None = Field(default=None, min_length=1, max_length=120)


class ApiError(ContractModel):
    """Machine-readable error body.

    ``expected_version`` and ``current_version`` are populated for optimistic
    locking failures.  ``details`` is intentionally an object so field-level
    validation and conflict evidence can evolve without changing the envelope.
    """

    contract_version: str = Field(default=CONTRACT_VERSION, min_length=1, max_length=40)
    code: ErrorCode | str
    message: str = Field(min_length=1, max_length=2000)
    request_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected_version: int | None = Field(default=None, ge=1)
    current_version: int | None = Field(default=None, ge=1)
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(ContractModel):
    """Compatibility envelope for FastAPI exception handlers."""

    error: ApiError


class PageRequest(ContractModel):
    """Bounded cursor pagination accepted by application services."""

    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, min_length=1, max_length=255)


class PageInfo(ContractModel):
    limit: int = Field(default=50, ge=1, le=200)
    next_cursor: str | None = Field(default=None, min_length=1, max_length=255)
    has_more: bool = False
    total: int | None = Field(default=None, ge=0)


T = TypeVar("T")


class Page(ContractModel, Generic[T]):
    """Stable list response; legacy routes may continue returning bare lists."""

    data: list[T] = Field(default_factory=list)
    page: PageInfo = Field(default_factory=PageInfo)
    request_id: str | None = Field(default=None, min_length=1, max_length=120)
    contract_version: str = Field(default=CONTRACT_VERSION, min_length=1, max_length=40)


class VersionGuard(ContractModel):
    """Optimistic-lock precondition shared by mutation commands."""

    expected_version: int = Field(ge=1)
    if_match: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("if_match")
    @classmethod
    def reject_blank_if_match(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("if_match must not be blank")
        return value

