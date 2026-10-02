"""Stable content-generation and evidence-binding contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from .common import CONTRACT_VERSION, ContractModel


class ContentClaimContract(ContractModel):
    claim_id: str = Field(min_length=1, max_length=120)
    field_path: str = Field(min_length=1, max_length=500)
    fact_id: int = Field(ge=1)
    fact_version: int = Field(ge=1)
    subject: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1)
    unit: str | None = Field(default=None, max_length=100)
    scope: dict[str, str] = Field(default_factory=dict)
    source_locator: str = Field(min_length=1, max_length=2048)


class ContentBlockContract(ContractModel):
    kind: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=12000)
    claim_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("claim_ids")
    @classmethod
    def unique_claim_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("claim_ids must be unique")
        return value


class ContentDraftContract(ContractModel):
    contract_version: str = Field(default=CONTRACT_VERSION, min_length=1, max_length=40)
    schema_version: int = Field(default=2, ge=1)
    field_diff: dict[str, Any] = Field(min_length=1)
    body_blocks: list[ContentBlockContract] = Field(default_factory=list, max_length=100)
    claims: list[ContentClaimContract] = Field(default_factory=list, max_length=200)
    answered_question_ids: list[int] = Field(default_factory=list, max_length=100)
    missing_information: list[str] = Field(default_factory=list, max_length=100)


class ContentValidationContract(ContractModel):
    contract_version: str = Field(default=CONTRACT_VERSION, min_length=1, max_length=40)
    schema_version: int = Field(default=2, ge=1)
    status: Literal["valid", "needs_review", "rejected"]
    valid: bool
    issues: list[str] = Field(default_factory=list, max_length=100)
    checked_fact_ids: list[int] = Field(default_factory=list, max_length=200)


class ContentGenerationCommand(ContractModel):
    generation_id: str = Field(min_length=1, max_length=200)
    workspace_id: int = Field(ge=1)
    site_id: int = Field(ge=1)
    change_request_id: int = Field(ge=1)
    request_summary: str = Field(min_length=1, max_length=800)
    required_fact_ids: list[int] = Field(min_length=1, max_length=100)

    @field_validator("required_fact_ids")
    @classmethod
    def unique_fact_ids(cls, value: list[int]) -> list[int]:
        if any(item < 1 for item in value):
            raise ValueError("required_fact_ids must contain positive integers")
        if len(set(value)) != len(value):
            raise ValueError("required_fact_ids must be unique")
        return value

