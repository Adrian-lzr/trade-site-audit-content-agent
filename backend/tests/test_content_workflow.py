from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from backend.content_workflow import (
    ConfirmedFact,
    FixtureDraftGateway,
    RevisionReference,
    SQLAlchemyContentWorkflowRepository,
    StaleFactsError,
    StoredDraft,
    WorkflowStatus,
    build_content_workflow,
    content_workflow_input,
    workflow_config,
)
from backend.database import SessionLocal
from backend.model_gateway import FixtureModelDraftGateway
from backend.models import ChangeRequest, ChangeRevision, Fact, Site, Workspace, utcnow


@pytest.fixture
def valve_fact() -> ConfirmedFact:
    now = utcnow()
    return ConfirmedFact(
        id=11,
        workspace_id=3,
        series_id="synthetic-valve-pressure",
        version=1,
        subject="Synthetic fixture valve",
        predicate="working_pressure",
        value="250",
        unit="bar",
        source_id="fixture:valve-catalog",
        source_locator="fixture://synthetic-valves/vx-42",
        visibility="public",
        status="confirmed",
        valid_from=now - timedelta(days=1),
        valid_until=None,
    )


class MemoryContentRepository:
    def __init__(self, facts: list[ConfirmedFact]):
        self.facts = {fact.id: fact for fact in facts}
        self.revisions: dict[int, StoredDraft] = {}
        self.hashes: dict[int, str] = {}
        self.generation_ids: dict[str, RevisionReference] = {}
        self.persist_calls = 0

    def get_confirmed_public_facts(self, workspace_id: int, fact_ids: list[int]):
        return [
            fact
            for fact_id in fact_ids
            if (fact := self.facts.get(fact_id)) is not None
            and fact.workspace_id == workspace_id
            and fact.visibility == "public"
            and fact.status == "confirmed"
        ]

    def persist_revision(self, workspace_id, site_id, change_request_id, generation_id, field_diff, fact_versions):
        if generation_id in self.generation_ids:
            return self.generation_ids[generation_id]
        self.persist_calls += 1
        revision_id = self.persist_calls
        payload = json.dumps(field_diff, sort_keys=True, separators=(",", ":"))
        content_hash = hashlib.sha256(f"{change_request_id}:{revision_id}:{payload}".encode()).hexdigest()
        self.revisions[revision_id] = StoredDraft(dict(field_diff), [dict(item) for item in fact_versions])
        self.hashes[revision_id] = content_hash
        reference = RevisionReference(revision_id, content_hash)
        self.generation_ids[generation_id] = reference
        return reference

    def get_revision(self, workspace_id, site_id, change_request_id, revision_id, expected_hash):
        assert self.hashes[revision_id] == expected_hash
        return self.revisions[revision_id]


def _initial_state() -> dict:
    return content_workflow_input(
        workspace_id=3,
        site_id=5,
        change_request_id=44,
        request_summary="Draft a short synthetic valve listing from confirmed catalog facts.",
        required_fact_ids=[11],
    )


def _valid_fixture_draft() -> dict[str, str]:
    return {
        "title": "Synthetic fixture valve",
        "body": "Synthetic fixture listing. Confirmed working pressure: 250 bar.",
    }


def test_draft_is_fact_grounded_checkpointed_by_reference_and_pauses_for_review(valve_fact):
    repository = MemoryContentRepository([valve_fact])
    gateway = FixtureDraftGateway([_valid_fixture_draft()])
    checkpointer = InMemorySaver()
    graph = build_content_workflow(repository, gateway, checkpointer)
    config = workflow_config("content-change:44")

    result = graph.invoke(_initial_state(), config=config)

    assert "__interrupt__" in result, result
    assert result["__interrupt__"][0].value == {
        "type": "content_draft_review",
        "change_request_id": 44,
        "revision_id": 1,
        "revision_hash": repository.hashes[1],
    }
    snapshot = graph.get_state(config)
    state_json = json.dumps(snapshot.values, sort_keys=True)
    assert snapshot.values["status"] == WorkflowStatus.awaiting_review.value
    assert snapshot.values["fact_versions"] == [{"fact_id": 11, "series_id": "synthetic-valve-pressure", "version": 1}]
    assert snapshot.values["revision_id"] == 1
    assert "250 bar" not in state_json
    assert "Synthetic fixture valve" not in state_json
    assert "fixture://synthetic-valves/vx-42" not in state_json
    assert gateway.requests[0].facts[0].value == "250"

    resumed = graph.invoke(Command(resume={"reviewed": True}), config=config)
    assert resumed["status"] == WorkflowStatus.awaiting_review.value


def test_review_command_resume_reaches_authoritative_terminal_state(valve_fact):
    repository = MemoryContentRepository([valve_fact])
    gateway = FixtureDraftGateway([_valid_fixture_draft()])
    graph = build_content_workflow(repository, gateway, InMemorySaver())
    config = workflow_config("content-change:44-approved")
    graph.invoke(_initial_state(), config=config)

    approved = graph.invoke(Command(resume={"decision": "approved", "decision_id": "decision-1"}), config=config)

    assert approved["status"] == "approved"
    state = graph.get_state(config).values
    assert state["review_decision"] == "approved"


def test_review_command_rejection_reaches_terminal_state_without_publish_signal(valve_fact):
    repository = MemoryContentRepository([valve_fact])
    gateway = FixtureDraftGateway([_valid_fixture_draft()])
    graph = build_content_workflow(repository, gateway, InMemorySaver())
    config = workflow_config("content-change:44-rejected")
    graph.invoke(_initial_state(), config=config)

    rejected = graph.invoke(Command(resume={"decision": "rejected", "decision_id": "decision-2"}), config=config)

    assert rejected["status"] == "rejected"
    assert "review_decision" not in rejected or rejected["review_decision"] == "rejected"


def test_missing_confirmed_fact_returns_needs_information_without_calling_gateway(valve_fact):
    proposed = replace(valve_fact, status="proposed")
    repository = MemoryContentRepository([proposed])
    gateway = FixtureDraftGateway([_valid_fixture_draft()])
    graph = build_content_workflow(repository, gateway, InMemorySaver())

    result = graph.invoke(_initial_state(), config=workflow_config("content-change:45"))

    assert result["status"] == WorkflowStatus.needs_information.value
    assert result["missing_fact_ids"] == [11]
    assert gateway.requests == []
    assert repository.persist_calls == 0


def test_fixture_no_topic_match_returns_needs_information_without_saving_a_revision(valve_fact):
    repository = MemoryContentRepository([valve_fact])
    gateway = FixtureModelDraftGateway()
    graph = build_content_workflow(repository, gateway, InMemorySaver())
    initial = _initial_state()
    initial["procurement_context"] = {
        "question": "What should I compare when choosing between different industrial valve types?",
        "product": "Industrial valves",
        "use_case": "industrial flow control",
        "buyer_role": "design engineer",
    }

    result = graph.invoke(initial, config=workflow_config("content-change:48"))

    assert result["status"] == WorkflowStatus.needs_information.value
    assert result["missing_fact_ids"] == []
    assert "no directly relevant confirmed supplier facts" in result["validation_summary"][0].casefold()
    assert "__interrupt__" not in result
    assert repository.persist_calls == 0


def test_external_guidance_is_passed_to_gateway_and_versions_generation_identity(valve_fact):
    repository = MemoryContentRepository([valve_fact])
    gateway = FixtureDraftGateway([_valid_fixture_draft()])
    base = _initial_state()
    first_guidance = {
        "id": "google-search-essentials",
        "summary": "Do not claim guaranteed rankings.",
        "source_url": "https://developers.google.com/search/docs/essentials",
        "source_date": "2025-12-10",
    }
    changed_guidance = {**first_guidance, "summary": "Never present rankings as guaranteed."}
    first_graph = build_content_workflow(repository, gateway, InMemorySaver())
    second_graph = build_content_workflow(repository, gateway, InMemorySaver())

    first = dict(base, external_guidance=[first_guidance])
    second = dict(base, external_guidance=[changed_guidance])
    first_graph.invoke(first, config=workflow_config("stable-thread"))
    second_graph.invoke(second, config=workflow_config("stable-thread"))

    assert gateway.requests[0].external_guidance == (first_guidance,)
    assert gateway.requests[1].external_guidance == (changed_guidance,)
    assert gateway.requests[0].generation_id != gateway.requests[1].generation_id
    assert ":knowledge:" in gateway.requests[0].generation_id


def test_invalid_numeric_claim_gets_one_repair_attempt(valve_fact):
    repository = MemoryContentRepository([valve_fact])
    gateway = FixtureDraftGateway(
        [
            {"body": "Synthetic fixture valve with 450 bar working pressure."},
            {"body": "Synthetic fixture valve with 250 bar working pressure."},
        ]
    )
    graph = build_content_workflow(repository, gateway, InMemorySaver())

    result = graph.invoke(_initial_state(), config=workflow_config("content-change:46"))

    assert result["__interrupt__"][0].value["type"] == "content_draft_review"
    state = graph.get_state(workflow_config("content-change:46")).values
    assert state["repair_attempts"] == 1
    assert len(gateway.requests) == 2
    assert gateway.requests[1].repair_issues == ("body includes an unconfirmed numeric claim.",)
    assert repository.persist_calls == 1


def test_invalid_draft_stops_after_one_repair_and_requires_information(valve_fact):
    repository = MemoryContentRepository([valve_fact])
    gateway = FixtureDraftGateway([{"body": "Synthetic fixture rated to 450 bar."}])
    graph = build_content_workflow(repository, gateway, InMemorySaver())

    result = graph.invoke(_initial_state(), config=workflow_config("content-change:47"))

    assert result["status"] == WorkflowStatus.needs_information.value
    assert result["repair_attempts"] == 1
    assert len(gateway.requests) == 2
    assert repository.persist_calls == 0
    assert "Synthetic fixture rated to 450 bar." not in json.dumps(result)


def test_sql_repository_uses_current_public_fact_and_change_revision_contract(valve_fact):
    with SessionLocal() as db:
        workspace = Workspace(name="Synthetic valve fixture")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="Fixture catalog", base_url="https://fixture.invalid")
        db.add(site)
        db.flush()
        change = ChangeRequest(workspace_id=workspace.id, site_id=site.id, state="draft", version=1, title="Synthetic fixture copy")
        db.add(change)
        db.flush()
        fact = Fact(
            workspace_id=workspace.id,
            series_id=valve_fact.series_id,
            subject=valve_fact.subject,
            predicate=valve_fact.predicate,
            value=valve_fact.value,
            unit=valve_fact.unit,
            source_id=valve_fact.source_id,
            source_locator=valve_fact.source_locator,
            visibility="public",
            status="confirmed",
            version=1,
        )
        internal = Fact(
            workspace_id=workspace.id,
            subject="Internal fixture note",
            predicate="working_pressure",
            value="999",
            unit="bar",
            source_id="fixture:internal",
            source_locator="fixture://synthetic-valves/internal",
            visibility="internal_only",
            status="confirmed",
        )
        db.add_all([fact, internal])
        db.commit()
        workspace_id, site_id, change_id, fact_id = workspace.id, site.id, change.id, fact.id

    repository = SQLAlchemyContentWorkflowRepository(SessionLocal)
    facts = repository.get_confirmed_public_facts(workspace_id, [fact_id, fact_id + 1])
    assert [item.id for item in facts] == [fact_id]
    references = [{"fact_id": fact_id, "series_id": facts[0].series_id, "version": 1}]
    reference = repository.persist_revision(
        workspace_id,
        site_id,
        change_id,
        "content-task:1:item:1:draft:0",
        {"title": "Synthetic fixture valve at 250 bar"},
        references,
    )
    replayed_reference = repository.persist_revision(
        workspace_id,
        site_id,
        change_id,
        "content-task:1:item:1:draft:0",
        {"title": "Synthetic fixture valve at 250 bar"},
        references,
    )
    assert replayed_reference == reference
    stored = repository.get_revision(workspace_id, site_id, change_id, reference.id, reference.content_hash)
    assert stored.field_diff == {"title": "Synthetic fixture valve at 250 bar"}
    assert stored.fact_versions == references
    with SessionLocal() as db:
        assert db.query(ChangeRevision).filter(ChangeRevision.generation_id == "content-task:1:item:1:draft:0").count() == 1

    with pytest.raises(StaleFactsError):
        repository.persist_revision(
            workspace_id,
            site_id,
            change_id,
            "content-task:1:item:1:draft:1",
            {"title": "Synthetic fixture valve at 999 bar"},
            [{"fact_id": fact_id + 1, "series_id": "missing", "version": 1}],
        )
