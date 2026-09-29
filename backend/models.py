from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class FactStatus(StrEnum):
    proposed = "proposed"
    confirmed = "confirmed"
    rejected = "rejected"
    expired = "expired"


class FactVisibility(StrEnum):
    public = "public"
    internal_only = "internal_only"


class ChangeState(StrEnum):
    draft = "draft"
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"
    publishing = "publishing"
    published = "published"
    failed = "failed"
    rolled_back = "rolled_back"


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    sites: Mapped[list["Site"]] = relationship(back_populates="workspace", cascade="all, delete-orphan")
    facts: Mapped[list["Fact"]] = relationship(back_populates="workspace", cascade="all, delete-orphan")


class Site(Base):
    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_site_workspace_name"),
        UniqueConstraint("workspace_id", "id", name="uq_site_workspace_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    allowed_paths: Mapped[str] = mapped_column(Text, default='["/"]', nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(default=False, nullable=False)
    audit_policy_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    audit_page_limit: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    workspace: Mapped[Workspace] = relationship(back_populates="sites")
    pages: Mapped[list["Page"]] = relationship(back_populates="site", cascade="all, delete-orphan")
    jobs: Mapped[list["Job"]] = relationship(back_populates="site", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("site_id", "canonical_url", name="uq_page_site_url"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    site: Mapped[Site] = relationship(back_populates="pages")
    snapshots: Mapped[list["PageSnapshot"]] = relationship(back_populates="page", cascade="all, delete-orphan")


class ProcurementQuestionSet(Base):
    """Workspace- and site-scoped identity for immutable question-set versions."""

    __tablename__ = "procurement_question_sets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "site_id", "name", name="uq_question_set_site_name"),
        UniqueConstraint("id", "site_id", name="uq_question_set_id_site"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    site_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ += (
        ForeignKeyConstraint(
            ["workspace_id", "site_id"],
            ["sites.workspace_id", "sites.id"],
            name="fk_question_set_site_workspace",
            ondelete="CASCADE",
        ),
    )

    workspace: Mapped[Workspace] = relationship()
    site: Mapped[Site] = relationship(viewonly=True)
    versions: Mapped[list["ProcurementQuestionSetVersion"]] = relationship(
        back_populates="question_set", cascade="all, delete-orphan", order_by="ProcurementQuestionSetVersion.version"
    )


class ProcurementQuestionSetVersion(Base):
    """Draft or frozen numbered version of one procurement question set."""

    __tablename__ = "procurement_question_set_versions"
    __table_args__ = (
        UniqueConstraint("question_set_id", "version", name="uq_question_set_version_number"),
        CheckConstraint("version >= 1", name="ck_question_set_version_positive"),
        CheckConstraint("edit_version >= 1", name="ck_question_set_edit_version_positive"),
        CheckConstraint("state IN ('draft', 'frozen')", name="ck_question_set_version_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_set_id: Mapped[int] = mapped_column(ForeignKey("procurement_question_sets.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    edit_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    question_set: Mapped[ProcurementQuestionSet] = relationship(back_populates="versions")
    questions: Mapped[list["ProcurementQuestion"]] = relationship(
        back_populates="version_record", cascade="all, delete-orphan", order_by="ProcurementQuestion.position"
    )


class ProcurementQuestion(Base):
    """One buyer question and its structured purchasing context."""

    __tablename__ = "procurement_questions"
    __table_args__ = (
        UniqueConstraint("question_set_version_id", "position", name="uq_question_version_position"),
        CheckConstraint("position BETWEEN 1 AND 20", name="ck_question_position_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_set_version_id: Mapped[int] = mapped_column(ForeignKey("procurement_question_set_versions.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    product: Mapped[str] = mapped_column(String(200), nullable=False)
    use_case: Mapped[str] = mapped_column(String(300), nullable=False)
    buyer_role: Mapped[str] = mapped_column(String(120), nullable=False)
    purchase_stage: Mapped[str] = mapped_column(String(120), nullable=False)
    target_market: Mapped[str] = mapped_column(String(120), nullable=False)
    language: Mapped[str] = mapped_column(String(35), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    version_record: Mapped[ProcurementQuestionSetVersion] = relationship(back_populates="questions")
    page_mappings: Mapped[list["ProcurementQuestionPageMapping"]] = relationship(
        back_populates="question", cascade="all, delete-orphan", order_by="ProcurementQuestionPageMapping.position"
    )


class ProcurementQuestionPageMapping(Base):
    """Explicit mapping from one question to a page on its question-set site."""

    __tablename__ = "procurement_question_page_mappings"
    __table_args__ = (
        UniqueConstraint("question_id", "page_id", name="uq_question_page_mapping"),
        UniqueConstraint("question_id", "position", name="uq_question_page_position"),
        CheckConstraint("position >= 1", name="ck_question_page_position_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("procurement_questions.id", ondelete="CASCADE"), nullable=False)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    question: Mapped[ProcurementQuestion] = relationship(back_populates="page_mappings")
    page: Mapped[Page] = relationship()


class PageSnapshot(Base):
    __tablename__ = "page_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    headers_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(default=False, nullable=False)
    audit_rule_version: Mapped[str] = mapped_column(String(40), default="1.0.0", nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    page: Mapped[Page] = relationship(back_populates="snapshots")
    findings: Mapped[list["AuditFinding"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")
    rule_results: Mapped[list["AuditRuleResult"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.queued.value, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    audit_input_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    site: Mapped[Site] = relationship(back_populates="jobs")
    snapshots: Mapped[list[PageSnapshot]] = relationship(cascade="all, delete-orphan")


class AuditFinding(Base):
    __tablename__ = "audit_findings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("page_snapshots.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    snapshot: Mapped[PageSnapshot] = relationship(back_populates="findings")


class AuditRuleResult(Base):
    __tablename__ = "audit_rule_results"
    __table_args__ = (UniqueConstraint("snapshot_id", "rule_id", "version", name="uq_snapshot_rule_version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("page_snapshots.id", ondelete="CASCADE"), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    remediation_hint: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[PageSnapshot] = relationship(back_populates="rule_results")


class Fact(Base):
    """An append-only version of a business fact owned by one workspace."""

    __tablename__ = "facts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "series_id", "version", name="uq_fact_series_version"),
        CheckConstraint("visibility IN ('public', 'internal_only')", name="ck_fact_visibility"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    series_id: Mapped[str] = mapped_column(String(64), default=lambda: uuid4().hex, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("facts.id", ondelete="SET NULL"), nullable=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    predicate: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_locator: Mapped[str] = mapped_column(String(2048), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), default=FactVisibility.internal_only.value, server_default=FactVisibility.internal_only.value, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=FactStatus.proposed.value, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="facts")
    parent: Mapped["Fact | None"] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list["Fact"]] = relationship(back_populates="parent")


class ChangeRequest(Base):
    """Workspace and site scoped, versioned request for a published change."""

    __tablename__ = "change_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), nullable=False)
    state: Mapped[str] = mapped_column(String(30), default=ChangeState.draft.value, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    current_revision_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    workspace: Mapped[Workspace] = relationship()
    site: Mapped[Site] = relationship()
    revisions: Mapped[list["ChangeRevision"]] = relationship(back_populates="request", cascade="all, delete-orphan", foreign_keys="ChangeRevision.change_request_id")
    approvals: Mapped[list["ChangeApproval"]] = relationship(back_populates="request", cascade="all, delete-orphan")
    publication_attempts: Mapped[list["PublicationAttempt"]] = relationship(back_populates="request", cascade="all, delete-orphan")


class ChangeRevision(Base):
    """Immutable proposed contents for one change request revision."""

    __tablename__ = "change_revisions"
    __table_args__ = (
        UniqueConstraint("change_request_id", "revision", name="uq_change_revision_number"),
        UniqueConstraint("generation_id", name="uq_change_revision_generation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_request_id: Mapped[int] = mapped_column(ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(30), default=ChangeState.draft.value, nullable=False)
    base_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("page_snapshots.id", ondelete="SET NULL"), nullable=True)
    base_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    field_diff_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    fact_versions_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    request: Mapped[ChangeRequest] = relationship(back_populates="revisions", foreign_keys=[change_request_id])
    base_snapshot: Mapped[PageSnapshot | None] = relationship()
    approvals: Mapped[list["ChangeApproval"]] = relationship(back_populates="revision", cascade="all, delete-orphan")


class ContentGenerationTask(Base):
    """A queued batch of content drafts for one frozen procurement question set."""

    __tablename__ = "content_generation_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "site_id"],
            ["sites.workspace_id", "sites.id"],
            name="fk_content_task_site_workspace",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'needs_information', 'awaiting_review')",
            name="ck_content_task_status",
        ),
        CheckConstraint("generation_source IN ('fixture', 'model_api')", name="ck_content_task_generation_source"),
        CheckConstraint("attempts >= 0", name="ck_content_task_attempts_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    site_id: Mapped[int] = mapped_column(Integer, nullable=False)
    question_set_version_id: Mapped[int] = mapped_column(
        ForeignKey("procurement_question_set_versions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    generation_source: Mapped[str] = mapped_column(String(20), default="fixture", nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    items: Mapped[list["ContentGenerationItem"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="ContentGenerationItem.id"
    )


class ContentGenerationItem(Base):
    """One page-scoped content draft and its immutable source references."""

    __tablename__ = "content_generation_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'needs_information', 'awaiting_review')",
            name="ck_content_item_status",
        ),
        UniqueConstraint("task_id", "question_id", "page_id", name="uq_content_item_task_question_page"),
        UniqueConstraint("thread_id", name="uq_content_item_thread_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("content_generation_tasks.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[int] = mapped_column(ForeignKey("procurement_questions.id", ondelete="CASCADE"), nullable=False)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), nullable=False)
    change_request_id: Mapped[int] = mapped_column(ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("page_snapshots.id", ondelete="CASCADE"), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_summary: Mapped[str] = mapped_column(Text, nullable=False)
    required_fact_ids_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    thread_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    task: Mapped[ContentGenerationTask] = relationship(back_populates="items")


class ChangeApproval(Base):
    """Append-only approval decision bound to one exact revision hash."""

    __tablename__ = "change_approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_request_id: Mapped[int] = mapped_column(ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False)
    revision_id: Mapped[int] = mapped_column(ForeignKey("change_revisions.id", ondelete="CASCADE"), nullable=False)
    revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(200), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    request: Mapped[ChangeRequest] = relationship(back_populates="approvals")
    revision: Mapped[ChangeRevision] = relationship(back_populates="approvals")


class OutboxEvent(Base):
    """Transactional event record. idempotency_key is the public de-duplication key."""

    __tablename__ = "outbox_events"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_outbox_idempotency_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PublicationAttempt(Base):
    __tablename__ = "publication_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_request_id: Mapped[int] = mapped_column(ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False)
    revision_id: Mapped[int] = mapped_column(ForeignKey("change_revisions.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    request: Mapped[ChangeRequest] = relationship(back_populates="publication_attempts")
    revision: Mapped[ChangeRevision] = relationship()
