from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import backend.content_worker as content_worker
from backend.content_worker import ContentGenerationWorker, _postgres_checkpoint_url, checkpoint_saver
from backend.content_workflow import SQLAlchemyContentWorkflowRepository, build_content_workflow, content_workflow_input, workflow_config
from backend.database import Base
from backend.model_gateway import FixtureModelDraftGateway
from backend.models import (
    ChangeRequest,
    ChangeRevision,
    ContentGenerationItem,
    ContentGenerationTask,
    Fact,
    Page,
    PageSnapshot,
    ProcurementQuestion,
    ProcurementQuestionSet,
    ProcurementQuestionSetVersion,
    Site,
    Workspace,
    utcnow,
)


@pytest.fixture
def isolated_database(tmp_path, monkeypatch):
    path = tmp_path / "content-worker.sqlite"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(content_worker, "SessionLocal", factory)
    yield factory, path
    engine.dispose()


def _records(factory):
    with factory() as db:
        workspace = Workspace(name="Worker test", external_id="content-worker-test")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="Fixture", base_url="http://fixture.invalid", is_synthetic=True)
        db.add(site)
        db.flush()
        page = Page(site_id=site.id, canonical_url="http://fixture.invalid/valve")
        db.add(page)
        db.flush()
        snapshot = PageSnapshot(page_id=page.id, url=page.canonical_url, status_code=200, content_hash="a" * 64, is_synthetic=True)
        question_set = ProcurementQuestionSet(workspace_id=workspace.id, site_id=site.id, name="Set")
        db.add_all([snapshot, question_set])
        db.flush()
        version = ProcurementQuestionSetVersion(question_set_id=question_set.id, version=1, state="frozen")
        db.add(version)
        db.flush()
        question = ProcurementQuestion(
            question_set_version_id=version.id,
            position=1,
            question="What is the confirmed pressure?",
            product="Valve",
            use_case="Fixture",
            buyer_role="Buyer",
            purchase_stage="Evaluation",
            target_market="Fixture",
            language="en",
        )
        change = ChangeRequest(workspace_id=workspace.id, site_id=site.id, title="Fixture valve")
        fact = Fact(
            workspace_id=workspace.id,
            subject="Synthetic fixture valve",
            predicate="working_pressure",
            value="250",
            unit="bar",
            source_id="fixture:catalog",
            source_locator="fixture://valve",
            visibility="public",
            status="confirmed",
        )
        db.add_all([question, change, fact])
        db.flush()
        task = ContentGenerationTask(
            workspace_id=workspace.id,
            site_id=site.id,
            question_set_version_id=version.id,
            status="queued",
        )
        db.add(task)
        db.flush()
        item = ContentGenerationItem(
            task_id=task.id,
            question_id=question.id,
            page_id=page.id,
            change_request_id=change.id,
            snapshot_id=snapshot.id,
            snapshot_hash=snapshot.content_hash,
            request_summary="Write a synthetic valve with confirmed working pressure details.",
            required_fact_ids_json=f"[{fact.id}]",
            thread_id=f"content-task:{task.id}:item:pending",
        )
        db.add(item)
        db.commit()
        item.thread_id = f"content-task:{task.id}:item:{item.id}"
        db.commit()
        return task.id, item.id, workspace.id, site.id, change.id, fact.id


def test_expired_content_lease_is_requeued(isolated_database):
    factory, _ = isolated_database
    with factory() as db:
        workspace = Workspace(name="Lease test", external_id="lease-test")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="Lease site", base_url="http://fixture.invalid")
        db.add(site)
        db.flush()
        question_set = ProcurementQuestionSet(workspace_id=workspace.id, site_id=site.id, name="Lease set")
        db.add(question_set)
        db.flush()
        version = ProcurementQuestionSetVersion(question_set_id=question_set.id, version=1, state="frozen")
        db.add(version)
        db.flush()
        task = ContentGenerationTask(
            workspace_id=workspace.id,
            site_id=site.id,
            question_set_version_id=version.id,
            status="running",
            lease_token="expired",
            lease_expires_at=utcnow() - timedelta(seconds=1),
        )
        db.add(task)
        db.commit()
        task_id = task.id

    assert ContentGenerationWorker().recover_interrupted() == 1
    with factory() as db:
        task = db.get(ContentGenerationTask, task_id)
        assert task.status == "queued"
        assert task.lease_token is None
        assert task.lease_expires_at is None


def test_postgres_checkpoint_url_uses_psycopg_dsn_scheme():
    assert _postgres_checkpoint_url("postgresql+psycopg://worker:secret@db.example/trade") == (
        "postgresql://worker:secret@db.example/trade"
    )
    assert _postgres_checkpoint_url("postgresql://worker@db.example/trade") == "postgresql://worker@db.example/trade"


def test_persistent_thread_replay_keeps_one_revision(isolated_database, monkeypatch):
    factory, _ = isolated_database
    task_id, item_id, workspace_id, site_id, change_id, fact_id = _records(factory)
    checkpoint_path = Path(factory.kw["bind"].url.database).with_name("graph-checkpoints.sqlite")
    monkeypatch.setattr(content_worker, "checkpoint_saver", lambda: checkpoint_saver(f"sqlite:///{checkpoint_path.as_posix()}"))

    worker = ContentGenerationWorker(gateway=FixtureModelDraftGateway())
    worker._process(task_id, "test-token") if _claim_for_test(factory, task_id) else None

    repository = SQLAlchemyContentWorkflowRepository(factory)
    with checkpoint_saver(f"sqlite:///{checkpoint_path.as_posix()}") as saver:
        graph = build_content_workflow(repository, FixtureModelDraftGateway(), saver)
        with factory() as db:
            item = db.get(ContentGenerationItem, item_id)
            thread_id = item.thread_id
            procurement_context = content_worker._procurement_context(db, item)
        initial = content_workflow_input(
            workspace_id=workspace_id,
            site_id=site_id,
            change_request_id=change_id,
            request_summary="Write a synthetic valve with confirmed working pressure details.",
            required_fact_ids=[fact_id],
            procurement_context=procurement_context,
        )
        replay = graph.invoke(initial, workflow_config(thread_id))
        assert "__interrupt__" in replay

    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ChangeRevision).where(ChangeRevision.change_request_id == change_id)) == 1
        item = db.get(ContentGenerationItem, item_id)
        assert item.thread_id == f"content-task:{task_id}:item:{item_id}"
        task = db.get(ContentGenerationTask, task_id)
        assert task.generation_source == "fixture"
        change = db.get(ChangeRequest, change_id)
        revision = db.get(ChangeRevision, change.current_revision_id)
        fields = json.loads(revision.field_diff_json)
        assert fields["faq"][0]["question"] == "What is the confirmed pressure?"
        assert "working pressure" in fields["faq"][0]["answer"].casefold()


def _claim_for_test(factory, task_id):
    with factory() as db:
        task = db.get(ContentGenerationTask, task_id)
        task.status = "running"
        task.lease_token = "test-token"
        task.lease_expires_at = utcnow() + timedelta(minutes=5)
        db.commit()
    return True
