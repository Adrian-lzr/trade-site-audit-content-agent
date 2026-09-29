from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceOut(WorkspaceCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


class SiteCreate(BaseModel):
    workspace_id: str | int
    name: str = Field(min_length=1, max_length=200)
    origin: HttpUrl | None = None
    base_url: HttpUrl | None = None
    allowed_paths: list[str] = Field(default_factory=lambda: ["/"])
    is_synthetic: bool = False
    expected_accessible: bool = True
    expected_indexable: bool = True
    preferred_origin: HttpUrl | None = None
    audit_page_limit: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def require_origin(self):
        if self.origin is None and self.base_url is None:
            raise ValueError("origin is required")
        return self

    @property
    def site_origin(self) -> HttpUrl:
        return self.origin or self.base_url


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    workspace_id: int
    name: str
    base_url: str
    allowed_paths: list[str]
    is_synthetic: bool = False
    created_at: datetime


class ProcurementQuestionCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    product: str = Field(min_length=1, max_length=200)
    use_case: str = Field(min_length=1, max_length=300)
    buyer_role: str = Field(min_length=1, max_length=120)
    purchase_stage: str = Field(min_length=1, max_length=120)
    target_market: str = Field(min_length=1, max_length=120)
    language: str = Field(min_length=1, max_length=35)
    page_ids: list[int] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_question_fields(self):
        for field_name in ("question", "product", "use_case", "buyer_role", "purchase_stage", "target_market", "language"):
            value = getattr(self, field_name)
            if not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        if any(page_id < 1 for page_id in self.page_ids):
            raise ValueError("page_ids must contain positive integers")
        if len(set(self.page_ids)) != len(self.page_ids):
            raise ValueError("page_ids must be unique")
        return self


class ProcurementQuestionSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    questions: list[ProcurementQuestionCreate] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_name(self):
        if not self.name.strip():
            raise ValueError("name must not be blank")
        return self


class ProcurementQuestionSetVersionCreate(BaseModel):
    expected_version: int = Field(ge=1)


class ProcurementQuestionSetVersionUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    questions: list[ProcurementQuestionCreate] = Field(max_length=20)


class ProcurementQuestionSetSummaryOut(BaseModel):
    id: int
    workspace_id: int
    site_id: int
    name: str
    current_version: int
    current_state: str
    created_at: datetime


class ProcurementQuestionPageMappingOut(BaseModel):
    page_id: int
    position: int
    canonical_url: str


class ProcurementQuestionOut(BaseModel):
    id: int
    position: int
    question: str
    product: str
    use_case: str
    buyer_role: str
    purchase_stage: str
    target_market: str
    language: str
    page_mappings: list[ProcurementQuestionPageMappingOut]


class ProcurementQuestionSetVersionOut(BaseModel):
    id: int
    question_set_id: int
    version: int
    edit_version: int
    state: str
    created_at: datetime
    frozen_at: datetime | None
    questions: list[ProcurementQuestionOut]


class ProcurementQuestionSetOut(ProcurementQuestionSetSummaryOut):
    versions: list[ProcurementQuestionSetVersionOut]


class ContentGenerationItemCreate(BaseModel):
    question_id: int = Field(ge=1)
    page_id: int = Field(ge=1)
    expected_snapshot_id: int = Field(ge=1)
    expected_snapshot_hash: str = Field(min_length=64, max_length=64, pattern="^[a-f0-9]{64}$")
    request_summary: str = Field(min_length=1, max_length=800)
    required_fact_ids: list[int] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_content_request(self):
        if not self.request_summary.strip():
            raise ValueError("request_summary must not be blank")
        if any(fact_id <= 0 for fact_id in self.required_fact_ids):
            raise ValueError("required_fact_ids must contain positive integer IDs")
        if len(set(self.required_fact_ids)) != len(self.required_fact_ids):
            raise ValueError("required_fact_ids must not contain duplicates")
        return self


class ContentGenerationTaskCreate(BaseModel):
    items: list[ContentGenerationItemCreate] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_unique_mappings(self):
        mappings = [(item.question_id, item.page_id) for item in self.items]
        if len(set(mappings)) != len(mappings):
            raise ValueError("question and page mappings must be unique")
        return self


class JobOut(BaseModel):
    id: str
    site_id: str
    status: str
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: int = 0
    pages_total: int = 0
    pages_completed: int = 0
    findings_count: int = 0
    completed_at: datetime | None = None


class FindingOut(BaseModel):
    id: int
    code: str
    severity: str
    message: str
    evidence: dict[str, Any]


class RuleResultOut(BaseModel):
    id: int
    rule_id: str
    version: str
    scope: str
    status: str
    severity: str
    message: str
    evidence: dict[str, Any]
    remediation_hint: str


class SnapshotOut(BaseModel):
    id: int
    page_id: int
    job_id: int | None
    url: str
    status_code: int
    title: str | None
    content_hash: str
    content_type: str | None
    fetched_at: datetime
    is_synthetic: bool = False
    findings: list[FindingOut]
    rule_set_version: str
    rule_results: list[RuleResultOut]


class FactCreate(BaseModel):
    """Payload used to import one proposed fact version."""

    workspace_id: str | int | None = None
    subject: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1)
    unit: str | None = Field(default=None, max_length=100)
    source_id: str = Field(min_length=1, max_length=255)
    source_locator: str = Field(min_length=1, max_length=2048)
    visibility: Literal["public", "internal_only"] = "internal_only"
    series_id: str | None = Field(default=None, min_length=1, max_length=64)
    parent_id: int | None = Field(default=None, ge=1)
    version: int | None = Field(default=None, ge=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def valid_window(self):
        if self.valid_until is not None and self.valid_from is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        return self


class FactReview(BaseModel):
    reviewer: str = Field(min_length=1, max_length=200)
    expected_version: int | None = Field(default=None, ge=1)


class FactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    workspace_id: int
    series_id: str
    parent_id: int | None
    subject: str
    predicate: str
    value: str | None
    unit: str | None
    source_id: str
    source_locator: str
    visibility: Literal["public", "internal_only"]
    status: str
    version: int
    valid_from: datetime
    valid_until: datetime | None
    reviewer: str | None
    reviewed_at: datetime | None
    created_at: datetime


class ChangeRevisionCreate(BaseModel):
    base_snapshot_id: int | None = Field(default=None, ge=1)
    base_content_hash: str | None = Field(default=None, min_length=1, max_length=64)
    field_diff: dict[str, Any] = Field(default_factory=dict)
    fact_versions: list[dict[str, Any]] = Field(default_factory=list)
    content_hash: str | None = Field(default=None, min_length=1, max_length=64)
    expected_version: int | None = Field(default=None, ge=1)


class ChangeRequestCreate(ChangeRevisionCreate):
    workspace_id: str | int | None = None
    site_id: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, max_length=500)


class ChangeApprovalCreate(BaseModel):
    reviewer: str = Field(min_length=1, max_length=200)
    decision: str = Field(default="approved", pattern="^(approved|rejected)$")
    revision_id: int = Field(ge=1)
    revision_hash: str = Field(min_length=1, max_length=64)
    comment: str | None = None
    expected_version: int | None = Field(default=None, ge=1)


class ChangeAction(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)


class ChangeRevisionOut(BaseModel):
    id: int
    change_request_id: int
    revision: int
    state: str
    base_snapshot_id: int | None
    base_content_hash: str | None
    field_diff: dict[str, Any]
    fact_versions: list[dict[str, Any]]
    content_hash: str
    created_at: datetime


class ChangeApprovalOut(BaseModel):
    id: int
    change_request_id: int
    revision_id: int
    revision_hash: str
    reviewer: str
    decision: str
    comment: str | None
    created_at: datetime


class ChangeRequestOut(BaseModel):
    id: int
    workspace_id: int
    site_id: int
    state: str
    version: int
    current_revision_id: int | None
    title: str | None
    created_at: datetime
    updated_at: datetime
    revision: ChangeRevisionOut | None = None
    approvals: list[ChangeApprovalOut] = Field(default_factory=list)


class ContentGenerationItemOut(BaseModel):
    id: int
    task_id: int
    question_id: int
    question: str
    page_id: int
    canonical_url: str
    change_request_id: int
    snapshot_id: int
    snapshot_hash: str
    request_summary: str
    required_fact_ids: list[int]
    status: str
    thread_id: str
    created_at: datetime
    change_request: ChangeRequestOut


class ContentGenerationTaskSummaryOut(BaseModel):
    id: int
    workspace_id: int
    site_id: int
    question_set_version_id: int
    status: str
    generation_source: Literal["fixture", "model_api"]
    attempts: int
    last_error: str | None
    created_at: datetime


class ContentGenerationTaskOut(ContentGenerationTaskSummaryOut):
    items: list[ContentGenerationItemOut]


class PublicationAttemptOut(BaseModel):
    id: int
    change_request_id: int
    revision_id: int
    status: str
    error: str | None
    created_at: datetime
