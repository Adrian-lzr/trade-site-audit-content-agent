"""Typed facts and fact-resolution contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from .common import CONTRACT_VERSION, ContractModel, Page, PageInfo
from ..time_utils import require_aware_utc


class FactBindingContract(ContractModel):
    """Immutable fact identity used by content and publication operations."""

    fact_id: int = Field(ge=1)
    workspace_id: int = Field(ge=1)
    series_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    subject: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1)
    unit: str | None = Field(default=None, max_length=100)
    scope: dict[str, str] = Field(default_factory=dict)
    source_id: str = Field(min_length=1, max_length=255)
    source_locator: str = Field(min_length=1, max_length=2048)
    visibility: str = Field(default="public", min_length=1, max_length=30)
    status: str = Field(default="confirmed", min_length=1, max_length=30)
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @field_validator("scope")
    @classmethod
    def normalize_scope(cls, value: dict[str, str]) -> dict[str, str]:
        return {str(key).strip(): str(item).strip() for key, item in value.items() if str(key).strip()}


class FactConflictContract(ContractModel):
    workspace_id: int = Field(ge=1)
    series_id: str = Field(min_length=1, max_length=64)
    fact_ids: tuple[int, ...] = ()
    reason: str = Field(min_length=1, max_length=2000)


class FactResolutionContract(ContractModel):
    contract_version: str = Field(default=CONTRACT_VERSION, min_length=1, max_length=40)
    workspace_id: int = Field(ge=1)
    as_of: datetime
    facts: list[FactBindingContract] = Field(default_factory=list)
    conflicts: list[FactConflictContract] = Field(default_factory=list)

    @field_validator("as_of")
    @classmethod
    def output_must_be_aware(cls, value: datetime) -> datetime:
        normalized = require_aware_utc(value)
        assert normalized is not None
        return normalized

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)


class FactListContract(Page[FactBindingContract]):
    """Paginated fact list for the future route layer."""


class FactBindingRequest(ContractModel):
    fact_id: int = Field(ge=1)
    series_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)


class FactResolutionRequest(ContractModel):
    workspace_id: int = Field(ge=1)
    as_of: datetime | None = None
    series_ids: list[str] | None = Field(default=None, max_length=200)
    subject: str | None = Field(default=None, max_length=500)
    predicate: str | None = Field(default=None, max_length=200)
    visibility: str | None = Field(default="public", max_length=30)

    @field_validator("as_of")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        return require_aware_utc(value)

    @field_validator("series_ids")
    @classmethod
    def normalize_series_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [item.strip() for item in value if item.strip()]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("series_ids must be unique")
        return cleaned

