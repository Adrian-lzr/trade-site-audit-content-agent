from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RunnableConfig, interrupt
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    ChangeRequest,
    ChangeRevision,
    ChangeState,
    Fact,
    FactStatus,
    FactVisibility,
    utcnow,
)


MAX_REQUIRED_FACTS = 100
MAX_SUMMARY_LENGTH = 800
MAX_DRAFT_JSON_LENGTH = 40_000
MAX_FIELD_TEXT_LENGTH = 12_000
MAX_SNAPSHOT_CONTEXT_LENGTH = 12_000

# Keep this allowlist aligned with app.ALLOWED_CHANGE_FIELDS. Drafts are still
# rechecked by the existing change API before approval or publication.
ALLOWED_CHANGE_FIELDS = frozenset(
    {"title", "meta_description", "body", "content", "body_blocks", "faq", "internal_links"}
)
_TEXT_FIELDS = frozenset({"title", "meta_description", "body", "content"})
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)?%?(?![A-Za-z])")


class WorkflowStatus(StrEnum):
    retrieving_facts = "retrieving_facts"
    drafting = "drafting"
    validating = "validating"
    awaiting_review = "awaiting_review"
    needs_information = "needs_information"


class ContentWorkflowState(TypedDict, total=False):
    workspace_id: int
    site_id: int
    change_request_id: int
    request_summary: str
    procurement_context: dict[str, str]
    external_guidance: list[dict[str, str]]
    snapshot_context: str
    snapshot_hash: str
    required_fact_ids: list[int]
    fact_ids: list[int]
    fact_versions: list[dict[str, int | str]]
    facts_hash: str
    missing_fact_ids: list[int]
    revision_id: int
    revision_hash: str
    validation_summary: list[str]
    candidate_validation_summary: list[str]
    validation_passed: bool
    repair_attempts: int
    status: str


@dataclass(frozen=True, slots=True)
class ConfirmedFact:
    id: int
    workspace_id: int
    series_id: str
    version: int
    subject: str
    predicate: str
    value: str
    unit: str | None
    source_id: str
    source_locator: str
    visibility: str
    status: str
    valid_from: datetime
    valid_until: datetime | None


@dataclass(frozen=True, slots=True)
class RevisionReference:
    id: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class StoredDraft:
    field_diff: dict[str, Any]
    fact_versions: list[dict[str, int | str]]


@dataclass(frozen=True, slots=True)
class DraftRequest:
    generation_id: str
    change_request_id: int
    request_summary: str
    facts: tuple[ConfirmedFact, ...]
    procurement_context: Mapping[str, str] = field(default_factory=dict)
    external_guidance: tuple[Mapping[str, str], ...] = ()
    snapshot_context: str = ""
    snapshot_hash: str = ""
    repair_issues: tuple[str, ...] = ()
    previous_fields: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class DraftNeedsInformation:
    summary: str


class DraftGateway(Protocol):
    """Gateway implementations must return the same draft for a repeated generation_id."""

    def draft(self, request: DraftRequest) -> Mapping[str, Any] | DraftNeedsInformation: ...


class ContentWorkflowRepository(Protocol):
    def get_confirmed_public_facts(self, workspace_id: int, fact_ids: Sequence[int]) -> Sequence[ConfirmedFact]: ...

    def persist_revision(
        self,
        workspace_id: int,
        site_id: int,
        change_request_id: int,
        generation_id: str,
        field_diff: Mapping[str, Any],
        fact_versions: Sequence[Mapping[str, int | str]],
    ) -> RevisionReference: ...

    def get_revision(
        self,
        workspace_id: int,
        site_id: int,
        change_request_id: int,
        revision_id: int,
        expected_hash: str,
    ) -> StoredDraft: ...


class WorkflowConflict(RuntimeError):
    """The requested content workflow no longer matches its database contract."""


class StaleFactsError(WorkflowConflict):
    """One or more selected facts are no longer current and public."""


class SQLAlchemyContentWorkflowRepository:
    """Persistence adapter for the current Fact and ChangeRevision tables."""

    def __init__(
        self,
        session_factory: Callable[[], Session] | sessionmaker[Session],
        *,
        persist_guard: Callable[[Session], None] | None = None,
    ):
        self._session_factory = session_factory
        self._persist_guard = persist_guard

    def get_confirmed_public_facts(self, workspace_id: int, fact_ids: Sequence[int]) -> Sequence[ConfirmedFact]:
        ids = sorted(set(fact_ids))
        if not ids:
            return ()
        now = utcnow()
        with self._session_factory() as db:
            rows = db.scalars(
                select(Fact)
                .where(
                    Fact.workspace_id == workspace_id,
                    Fact.id.in_(ids),
                    Fact.status == FactStatus.confirmed.value,
                    Fact.visibility == FactVisibility.public.value,
                    Fact.valid_from <= now,
                    or_(Fact.valid_until.is_(None), Fact.valid_until > now),
                )
                .order_by(Fact.id)
            ).all()
            return tuple(_fact_context(row) for row in rows)

    def persist_revision(
        self,
        workspace_id: int,
        site_id: int,
        change_request_id: int,
        generation_id: str,
        field_diff: Mapping[str, Any],
        fact_versions: Sequence[Mapping[str, int | str]],
    ) -> RevisionReference:
        if not generation_id or len(generation_id) > 200:
            raise WorkflowConflict("generation_id must contain 1 to 200 characters")
        canonical_fields = _json_copy(field_diff)
        canonical_facts = _canonical_fact_versions(fact_versions)
        _validate_fact_versions_shape(canonical_facts)
        with self._session_factory() as db:
            if self._persist_guard is not None:
                self._persist_guard(db)
            change = db.get(ChangeRequest, change_request_id)
            if change is None or change.workspace_id != workspace_id or change.site_id != site_id:
                raise WorkflowConflict("change request is outside the requested workspace and site")
            if change.state != ChangeState.draft.value:
                raise WorkflowConflict("only draft change requests accept generated revisions")
            facts = _assert_facts_current(db, workspace_id, canonical_facts)
            issues = validate_structured_draft(canonical_fields, facts)
            if issues:
                raise WorkflowConflict("generated revision does not satisfy the structured change contract")
            previous = db.get(ChangeRevision, change.current_revision_id) if change.current_revision_id else None
            base_snapshot_id = previous.base_snapshot_id if previous is not None else None
            base_content_hash = previous.base_content_hash if previous is not None else None
            content_hash = _revision_hash(base_snapshot_id, base_content_hash, canonical_fields, canonical_facts)
            existing_generation = db.scalar(
                select(ChangeRevision).where(ChangeRevision.generation_id == generation_id)
            )
            if existing_generation is not None:
                if (
                    existing_generation.change_request_id != change.id
                    or existing_generation.base_snapshot_id != base_snapshot_id
                    or existing_generation.base_content_hash != base_content_hash
                    or existing_generation.field_diff_json != _canonical_json(canonical_fields)
                    or existing_generation.fact_versions_json != _canonical_json(canonical_facts)
                    or existing_generation.content_hash != content_hash
                    or change.current_revision_id != existing_generation.id
                ):
                    raise WorkflowConflict("generation_id was already used for a different or stale revision")
                return RevisionReference(existing_generation.id, existing_generation.content_hash)
            if previous is not None and previous.content_hash == content_hash:
                # A replay may have a richer snapshot context hash than an
                # older checkpoint input. The canonical revision hash still
                # proves the persisted content is identical, so keep one
                # revision and avoid creating a duplicate.
                if previous.generation_id is None:
                    previous.generation_id = generation_id
                    db.commit()
                return RevisionReference(previous.id, previous.content_hash)

            existing_number = db.scalar(
                select(ChangeRevision.revision)
                .where(ChangeRevision.change_request_id == change.id)
                .order_by(ChangeRevision.revision.desc())
                .limit(1)
            )
            revision_number = (existing_number or 0) + 1
            revision = ChangeRevision(
                change_request_id=change.id,
                revision=revision_number,
                state=ChangeState.draft.value,
                base_snapshot_id=base_snapshot_id,
                base_content_hash=base_content_hash,
                field_diff_json=_canonical_json(canonical_fields),
                fact_versions_json=_canonical_json(canonical_facts),
                content_hash=content_hash,
                generation_id=generation_id,
            )
            db.add(revision)
            db.flush()
            change.version = revision_number
            change.current_revision_id = revision.id
            change.state = ChangeState.draft.value
            db.commit()
            return RevisionReference(revision.id, revision.content_hash)

    def get_revision(
        self,
        workspace_id: int,
        site_id: int,
        change_request_id: int,
        revision_id: int,
        expected_hash: str,
    ) -> StoredDraft:
        with self._session_factory() as db:
            change = db.get(ChangeRequest, change_request_id)
            revision = db.get(ChangeRevision, revision_id)
            if (
                change is None
                or change.workspace_id != workspace_id
                or change.site_id != site_id
                or revision is None
                or revision.change_request_id != change.id
                or change.current_revision_id != revision.id
                or change.state != ChangeState.draft.value
                or revision.state != ChangeState.draft.value
            ):
                raise WorkflowConflict("revision is no longer the current draft for this workspace, site, and change")
            if revision.content_hash != expected_hash:
                raise WorkflowConflict("revision hash changed while the workflow was running")
            return StoredDraft(
                field_diff=json.loads(revision.field_diff_json or "{}"),
                fact_versions=json.loads(revision.fact_versions_json or "[]"),
            )


class FixtureDraftGateway:
    """A deterministic gateway for fixture workflows and local demonstrations."""

    def __init__(self, responses: Sequence[Mapping[str, Any]]):
        self._responses = tuple(_json_copy(response) for response in responses)
        self._by_generation_id: dict[str, dict[str, Any]] = {}
        self.requests: list[DraftRequest] = []

    def draft(self, request: DraftRequest) -> Mapping[str, Any]:
        self.requests.append(request)
        if request.generation_id not in self._by_generation_id:
            index = min(len(self._by_generation_id), len(self._responses) - 1)
            if index < 0:
                raise RuntimeError("fixture gateway has no draft response")
            self._by_generation_id[request.generation_id] = _json_copy(self._responses[index])
        return _json_copy(self._by_generation_id[request.generation_id])


def content_workflow_input(
    *,
    workspace_id: int,
    site_id: int,
    change_request_id: int,
    request_summary: str,
    required_fact_ids: Sequence[int],
    procurement_context: Mapping[str, str] | None = None,
    external_guidance: Sequence[Mapping[str, str]] | None = None,
    snapshot_context: str = "",
    snapshot_hash: str = "",
) -> ContentWorkflowState:
    summary = request_summary.strip()
    if not summary or len(summary) > MAX_SUMMARY_LENGTH:
        raise ValueError(f"request_summary must contain 1 to {MAX_SUMMARY_LENGTH} characters")
    raw_ids = list(required_fact_ids)
    if any(type(item) is not int or item <= 0 for item in raw_ids):
        raise ValueError("required_fact_ids must contain positive integer IDs")
    ids = sorted(set(raw_ids))
    if not 1 <= len(ids) <= MAX_REQUIRED_FACTS:
        raise ValueError(f"required_fact_ids must contain 1 to {MAX_REQUIRED_FACTS} positive integer IDs")
    if any(type(value) is not int or value <= 0 for value in (workspace_id, site_id, change_request_id)):
        raise ValueError("workspace_id, site_id, and change_request_id must be positive integers")
    context_limits = {
        "question": 2000,
        "product": 200,
        "use_case": 300,
        "buyer_role": 120,
        "purchase_stage": 120,
        "target_market": 120,
        "language": 35,
        "page_url": 2048,
    }
    context: dict[str, str] = {}
    for name, value in (procurement_context or {}).items():
        limit = context_limits.get(name)
        if limit is None or not isinstance(value, str):
            raise ValueError("procurement_context contains an unsupported field")
        cleaned = value.strip()
        if len(cleaned) > limit:
            raise ValueError(f"procurement_context.{name} exceeds the maximum length")
        if cleaned:
            context[name] = cleaned
    guidance: list[dict[str, str]] = []
    for raw_entry in external_guidance or ():
        if not isinstance(raw_entry, Mapping):
            raise ValueError("external_guidance entries must be objects")
        entry: dict[str, str] = {}
        for name in (
            "id", "title", "summary", "scope", "market_scope", "source_url",
            "source_date", "accessed_at", "last_verified_at", "freshness_policy",
            "source_kind", "topic_tags", "license",
        ):
            value = raw_entry.get(name, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ValueError("external_guidance fields must be text")
            cleaned = value.strip()
            if len(cleaned) > 4000:
                raise ValueError("external_guidance field exceeds the maximum length")
            if cleaned:
                entry[name] = cleaned
        if not entry.get("id") or not entry.get("summary"):
            raise ValueError("external_guidance entries require id and summary")
        guidance.append(entry)
    if len(guidance) > 8:
        raise ValueError("external_guidance must contain at most 8 entries")
    if not isinstance(snapshot_context, str):
        raise ValueError("snapshot_context must be text")
    snapshot_context = snapshot_context[:MAX_SNAPSHOT_CONTEXT_LENGTH]
    if not isinstance(snapshot_hash, str):
        raise ValueError("snapshot_hash must be text")
    return {
        "workspace_id": workspace_id,
        "site_id": site_id,
        "change_request_id": change_request_id,
        "request_summary": summary,
        "procurement_context": context,
        "external_guidance": guidance,
        "snapshot_context": snapshot_context,
        "snapshot_hash": snapshot_hash,
        "required_fact_ids": ids,
        "repair_attempts": 0,
        "status": WorkflowStatus.retrieving_facts.value,
    }


def workflow_config(thread_id: str | int) -> RunnableConfig:
    value = str(thread_id).strip()
    if not value or len(value) > 200:
        raise ValueError("thread_id must contain 1 to 200 characters")
    return {"configurable": {"thread_id": value}}


def build_content_workflow(
    repository: ContentWorkflowRepository,
    gateway: DraftGateway,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    """Build the fact-grounded workflow; `thread_id` is supplied through workflow_config."""

    def retrieve_facts(state: ContentWorkflowState) -> dict[str, Any]:
        ids = state.get("required_fact_ids", [])
        if not ids:
            return {
                "missing_fact_ids": [],
                "validation_summary": ["Select at least one confirmed public fact before drafting."],
                "status": WorkflowStatus.needs_information.value,
            }
        facts, missing = _read_facts(repository, state, ids)
        if missing:
            return _needs_information_update(missing, "Confirm or refresh every selected public fact before drafting.")
        return {
            "fact_ids": [fact.id for fact in facts],
            "fact_versions": _fact_versions(facts),
            "facts_hash": _facts_hash(facts),
            "missing_fact_ids": [],
            "validation_summary": [],
            "repair_attempts": 0,
            "status": WorkflowStatus.drafting.value,
        }

    def generate_draft(state: ContentWorkflowState, config: RunnableConfig) -> dict[str, Any]:
        return _generate_draft(state, config, repository, gateway, repair=False)

    def repair_draft(state: ContentWorkflowState, config: RunnableConfig) -> dict[str, Any]:
        return _generate_draft(state, config, repository, gateway, repair=True)

    def validate_draft(state: ContentWorkflowState) -> dict[str, Any]:
        if state.get("status") == WorkflowStatus.needs_information.value:
            return {
                "validation_passed": False,
                "validation_summary": state.get("validation_summary", []),
                "candidate_validation_summary": [],
                "status": WorkflowStatus.needs_information.value,
            }
        candidate_issues = state.get("candidate_validation_summary", [])
        if candidate_issues:
            return {
                "validation_passed": False,
                "validation_summary": candidate_issues,
                "status": WorkflowStatus.validating.value,
            }

        facts, missing = _read_facts(repository, state, state.get("fact_ids", []))
        if missing or _facts_hash(facts) != state.get("facts_hash"):
            missing_ids = missing or state.get("fact_ids", [])
            return _needs_information_update(missing_ids, "Selected facts changed during drafting; refresh the fact set and retry.")

        revision_id = state.get("revision_id")
        revision_hash = state.get("revision_hash")
        if revision_id is None or not revision_hash:
            return {
                "validation_passed": False,
                "validation_summary": ["The generated draft was not saved as a change revision."],
                "status": WorkflowStatus.validating.value,
            }
        stored = repository.get_revision(
            state["workspace_id"],
            state["site_id"],
            state["change_request_id"],
            revision_id,
            revision_hash,
        )
        issues = validate_structured_draft(stored.field_diff, facts)
        return {
            "validation_passed": not issues,
            "validation_summary": issues,
            "status": WorkflowStatus.awaiting_review.value if not issues else WorkflowStatus.validating.value,
        }

    def needs_information(state: ContentWorkflowState) -> dict[str, Any]:
        return {
            "status": WorkflowStatus.needs_information.value,
            "validation_summary": state.get("validation_summary", []),
        }

    def pause_for_human_review(state: ContentWorkflowState) -> dict[str, Any]:
        interrupt(
            {
                "type": "content_draft_review",
                "change_request_id": state["change_request_id"],
                "revision_id": state["revision_id"],
                "revision_hash": state["revision_hash"],
            }
        )
        return {"status": WorkflowStatus.awaiting_review.value}

    def route_after_retrieval(state: ContentWorkflowState) -> str:
        if state.get("status") == WorkflowStatus.needs_information.value:
            return "needs_information"
        return "generate_draft"

    def route_after_validation(state: ContentWorkflowState) -> str:
        if state.get("status") == WorkflowStatus.needs_information.value:
            return "needs_information"
        if state.get("validation_passed"):
            return "pause_for_human_review"
        if state.get("repair_attempts", 0) < 1:
            return "repair_draft"
        return "needs_information"

    graph = StateGraph(ContentWorkflowState)
    graph.add_node("retrieve_facts", retrieve_facts)
    graph.add_node("generate_draft", generate_draft)
    graph.add_node("repair_draft", repair_draft)
    graph.add_node("validate_draft", validate_draft)
    graph.add_node("needs_information", needs_information)
    graph.add_node("pause_for_human_review", pause_for_human_review)
    graph.set_entry_point("retrieve_facts")
    graph.add_conditional_edges(
        "retrieve_facts",
        route_after_retrieval,
        {"generate_draft": "generate_draft", "needs_information": "needs_information"},
    )
    graph.add_edge("generate_draft", "validate_draft")
    graph.add_edge("repair_draft", "validate_draft")
    graph.add_conditional_edges(
        "validate_draft",
        route_after_validation,
        {
            "pause_for_human_review": "pause_for_human_review",
            "repair_draft": "repair_draft",
            "needs_information": "needs_information",
        },
    )
    graph.add_edge("needs_information", END)
    graph.add_edge("pause_for_human_review", END)
    return graph.compile(checkpointer=checkpointer)


def validate_structured_draft(field_diff: Mapping[str, Any], facts: Sequence[ConfirmedFact]) -> list[str]:
    if not isinstance(field_diff, Mapping) or not field_diff:
        return ["Provide at least one structured page field."]
    if any(not isinstance(name, str) for name in field_diff):
        return ["Field names must be text."]
    unknown = sorted(set(field_diff) - ALLOWED_CHANGE_FIELDS)
    issues = ["Draft contains fields outside the approved change contract."] if unknown else []
    if len(issues):
        return issues
    for name, value in field_diff.items():
        if name in _TEXT_FIELDS and not isinstance(value, str):
            issues.append(f"{name} must be text.")
        elif name in {"body_blocks", "faq", "internal_links"} and not isinstance(value, (list, dict)):
            issues.append(f"{name} must be a structured list or object.")
        elif isinstance(value, str) and len(value) > MAX_FIELD_TEXT_LENGTH:
            issues.append(f"{name} exceeds the maximum field length.")
        elif not _is_json_value(value):
            issues.append(f"{name} contains a value that cannot be stored as JSON.")
    if issues:
        return issues[:5]
    try:
        encoded = _canonical_json(dict(field_diff))
    except (TypeError, ValueError):
        return ["Draft contains a value that cannot be stored as JSON."]
    if len(encoded) > MAX_DRAFT_JSON_LENGTH:
        return ["Draft exceeds the maximum structured payload size."]

    supported = {_normalize_number(number) for fact in facts for text in (fact.subject, fact.predicate, fact.value, fact.unit or "") for number in _NUMBER_PATTERN.findall(text)}
    for field_name, value in field_diff.items():
        for text in _claim_texts_in(value):
            unsupported = {
                _normalize_number(number)
                for number in _NUMBER_PATTERN.findall(text)
                if _normalize_number(number) not in supported
            }
            if unsupported:
                issues.append(f"{field_name} includes an unconfirmed numeric claim.")
                break
        if len(issues) >= 5:
            break
    return issues


def _generate_draft(
    state: ContentWorkflowState,
    config: RunnableConfig,
    repository: ContentWorkflowRepository,
    gateway: DraftGateway,
    *,
    repair: bool,
) -> dict[str, Any]:
    facts, missing = _read_facts(repository, state, state.get("fact_ids", []))
    if missing or _facts_hash(facts) != state.get("facts_hash"):
        missing_ids = missing or state.get("fact_ids", [])
        return _needs_information_update(missing_ids, "Selected facts changed before drafting; refresh the fact set and retry.")

    previous_fields: Mapping[str, Any] | None = None
    current_revision_id = state.get("revision_id")
    current_revision_hash = state.get("revision_hash")
    if current_revision_id is not None and current_revision_hash:
        previous = repository.get_revision(
            state["workspace_id"],
            state["site_id"],
            state["change_request_id"],
            current_revision_id,
            current_revision_hash,
        )
        previous_fields = previous.field_diff

    repair_attempts = state.get("repair_attempts", 0) + (1 if repair else 0)
    thread_id = str(config.get("configurable", {}).get("thread_id", state["change_request_id"]))
    thread_key = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:32]
    snapshot_hash = state.get("snapshot_hash") or hashlib.sha256(state.get("snapshot_context", "").encode("utf-8")).hexdigest()
    external_guidance = state.get("external_guidance", [])
    guidance_hash = hashlib.sha256(_canonical_json(external_guidance).encode("utf-8")).hexdigest()[:16] if external_guidance else ""
    generation_id = f"thread:{thread_key}:snapshot:{snapshot_hash}:draft:{repair_attempts}"
    if guidance_hash:
        generation_id += f":knowledge:{guidance_hash}"
    request = DraftRequest(
        generation_id=generation_id,
        change_request_id=state["change_request_id"],
        request_summary=state["request_summary"],
        facts=tuple(facts),
        procurement_context=state.get("procurement_context", {}),
        external_guidance=tuple(state.get("external_guidance", [])),
        snapshot_context=state.get("snapshot_context", ""),
        snapshot_hash=snapshot_hash,
        repair_issues=tuple(state.get("validation_summary", ())) if repair else (),
        previous_fields=previous_fields,
    )
    candidate = gateway.draft(request)
    if isinstance(candidate, DraftNeedsInformation):
        update = _needs_information_update([], candidate.summary)
        update["repair_attempts"] = repair_attempts
        return update
    if not isinstance(candidate, Mapping):
        issues = ["The draft gateway did not return a structured field object."]
        return _candidate_failure(issues, repair_attempts)

    issues = validate_structured_draft(candidate, facts)
    if issues:
        return _candidate_failure(issues, repair_attempts)

    fact_versions = _fact_versions(facts)
    try:
        reference = repository.persist_revision(
            state["workspace_id"],
            state["site_id"],
            state["change_request_id"],
            request.generation_id,
            candidate,
            fact_versions,
        )
    except StaleFactsError:
        return _needs_information_update(state.get("fact_ids", []), "Selected facts changed before the draft could be saved; refresh and retry.")
    return {
        "revision_id": reference.id,
        "revision_hash": reference.content_hash,
        "candidate_validation_summary": [],
        "validation_summary": [],
        "validation_passed": False,
        "repair_attempts": repair_attempts,
        "status": WorkflowStatus.validating.value,
    }


def _candidate_failure(issues: list[str], repair_attempts: int) -> dict[str, Any]:
    return {
        "candidate_validation_summary": issues,
        "validation_summary": issues,
        "validation_passed": False,
        "repair_attempts": repair_attempts,
        "status": WorkflowStatus.validating.value,
    }


def _read_facts(
    repository: ContentWorkflowRepository,
    state: ContentWorkflowState,
    fact_ids: Sequence[int],
) -> tuple[list[ConfirmedFact], list[int]]:
    requested = sorted(set(fact_ids))
    if not requested:
        return [], []
    facts = list(repository.get_confirmed_public_facts(state["workspace_id"], requested))
    now = datetime.now(timezone.utc)
    valid_by_id = {
        fact.id: fact
        for fact in facts
        if fact.workspace_id == state["workspace_id"]
        and fact.status == FactStatus.confirmed.value
        and fact.visibility == FactVisibility.public.value
        and _datetime_utc(fact.valid_from) <= now
        and (fact.valid_until is None or _datetime_utc(fact.valid_until) > now)
    }
    missing = sorted(set(requested) - set(valid_by_id))
    return [valid_by_id[fact_id] for fact_id in requested if fact_id in valid_by_id], missing


def _needs_information_update(missing_fact_ids: Sequence[int], summary: str) -> dict[str, Any]:
    return {
        "missing_fact_ids": sorted(set(missing_fact_ids)),
        "validation_summary": [summary],
        "candidate_validation_summary": [],
        "validation_passed": False,
        "status": WorkflowStatus.needs_information.value,
    }


def _fact_versions(facts: Sequence[ConfirmedFact]) -> list[dict[str, int | str]]:
    return [
        {"fact_id": fact.id, "series_id": fact.series_id, "version": fact.version}
        for fact in sorted(facts, key=lambda item: item.id)
    ]


def _facts_hash(facts: Sequence[ConfirmedFact]) -> str:
    payload = [
        {
            "fact_id": fact.id,
            "workspace_id": fact.workspace_id,
            "series_id": fact.series_id,
            "version": fact.version,
            "subject": fact.subject,
            "predicate": fact.predicate,
            "value": fact.value,
            "unit": fact.unit,
            "source_id": fact.source_id,
            "source_locator": fact.source_locator,
            "visibility": fact.visibility,
            "status": fact.status,
            "valid_from": _datetime_utc(fact.valid_from).isoformat(),
            "valid_until": _datetime_utc(fact.valid_until).isoformat() if fact.valid_until else None,
        }
        for fact in sorted(facts, key=lambda item: item.id)
    ]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _assert_facts_current(
    db: Session,
    workspace_id: int,
    versions: Sequence[Mapping[str, int | str]],
) -> list[ConfirmedFact]:
    ids = [int(item["fact_id"]) for item in versions]
    rows = db.scalars(select(Fact).where(Fact.workspace_id == workspace_id, Fact.id.in_(ids))).all() if ids else []
    facts = {row.id: row for row in rows}
    now = utcnow()
    for item in versions:
        fact = facts.get(int(item["fact_id"]))
        if (
            fact is None
            or fact.series_id != str(item["series_id"])
            or fact.version != int(item["version"])
            or fact.status != FactStatus.confirmed.value
            or fact.visibility != FactVisibility.public.value
            or _datetime_utc(fact.valid_from) > now
            or (fact.valid_until is not None and _datetime_utc(fact.valid_until) <= now)
        ):
            raise StaleFactsError("selected fact version is no longer current and public")
    return [_fact_context(facts[int(item["fact_id"])]) for item in versions]


def _validate_fact_versions_shape(versions: Sequence[Mapping[str, int | str]]) -> None:
    ids: set[int] = set()
    for item in versions:
        try:
            fact_id = int(item["fact_id"])
            version = int(item["version"])
            series_id = str(item["series_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowConflict("fact references require fact_id, series_id, and version") from exc
        if fact_id <= 0 or version <= 0 or not series_id or fact_id in ids:
            raise WorkflowConflict("fact references must be unique, positive current versions")
        ids.add(fact_id)


def _canonical_fact_versions(versions: Sequence[Mapping[str, int | str]]) -> list[dict[str, int | str]]:
    try:
        normalized = [
            {"fact_id": int(item["fact_id"]), "series_id": str(item["series_id"]), "version": int(item["version"])}
            for item in versions
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowConflict("fact references require fact_id, series_id, and version") from exc
    return sorted(normalized, key=lambda item: (int(item["fact_id"]), int(item["version"])))


def _revision_hash(
    base_snapshot_id: int | None,
    base_content_hash: str | None,
    field_diff: Mapping[str, Any],
    facts: Sequence[Mapping[str, int | str]],
) -> str:
    payload = {
        "base_snapshot_id": base_snapshot_id,
        "base_content_hash": base_content_hash,
        "field_diff": field_diff,
        "fact_versions": facts,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _fact_context(fact: Fact) -> ConfirmedFact:
    return ConfirmedFact(
        id=fact.id,
        workspace_id=fact.workspace_id,
        series_id=fact.series_id,
        version=fact.version,
        subject=fact.subject,
        predicate=fact.predicate,
        value=fact.value,
        unit=fact.unit,
        source_id=fact.source_id,
        source_locator=fact.source_locator,
        visibility=fact.visibility,
        status=fact.status,
        valid_from=fact.valid_from,
        valid_until=fact.valid_until,
    )


def _datetime_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _normalize_number(value: str) -> str:
    return value.rstrip("%").replace(",", "")


def _strings_in(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for nested in value.values() for text in _strings_in(nested)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [text for nested in value for text in _strings_in(nested)]
    return []


def _claim_texts_in(value: Any) -> list[str]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [str(value)]
    return _strings_in(value)


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int, float)):
        return not isinstance(value, float) or (value == value and abs(value) != float("inf"))
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_json_value(nested) for key, nested in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return all(_is_json_value(nested) for nested in value)
    return False


def _json_copy(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
