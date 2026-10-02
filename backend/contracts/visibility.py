"""Versioned visibility metric and citation contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .common import CONTRACT_VERSION, ContractModel


class MetricValueContract(ContractModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: float | None = Field(default=None, ge=0, le=1)


class CitationEvidenceContract(ContractModel):
    url: str
    provider_raw_source: str | None = None
    kind: str
    normalized_domain: str | None = None
    validation_status: str
    validated_site_citation: bool
    crawled_at: str | None = None
    answered_at: str | None = None
    sample_index: int | None = Field(default=None, ge=0)
    question_id: str | int | None = None


class VisibilityMetricsContract(ContractModel):
    contract_version: str = Field(default=CONTRACT_VERSION, min_length=1, max_length=40)
    metric_version: str = Field(min_length=1, max_length=80)
    site_host: str | None = None
    site_host_valid: bool
    subdomains_allowed: bool
    sample_counts: dict[str, Any]
    sample_success: MetricValueContract
    brand_mention: MetricValueContract
    domain_mention: MetricValueContract
    validated_site_citation: MetricValueContract
    answered_question: dict[str, Any]
    question_counts: dict[str, Any]
    citation_details: dict[str, Any]
    cost: dict[str, Any]
    evidence_strata: dict[str, Any]
    details: dict[str, Any] = Field(default_factory=dict)

