from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker

import backend.content_worker as content_worker
import backend.review_worker as review_worker
from backend.content_worker import ContentGenerationWorker, _postgres_checkpoint_url, checkpoint_saver
from backend.review_worker import ReviewResumeWorker
from backend.content_workflow import SQLAlchemyContentWorkflowRepository, build_content_workflow, content_workflow_input, workflow_config
from backend.database import Base
from backend.model_gateway import DraftNeedsInformation, FixtureModelDraftGateway
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
    ChangeApproval,
    OutboxEvent,
    WorkflowReviewEvent,
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


@pytest.mark.parametrize(
    ("question_text", "request_summary", "fact_predicate", "fact_value", "fact_unit", "expected_status"),
    [
        (
            "What should I compare when choosing between different industrial valve types?",
            "Prepare a buyer comparison of industrial valve types.",
            "working_pressure",
            "250",
            "bar",
            "needs_information",
        ),
        (
            "How can I verify the materials used in a specific valve model?",
            "Prepare a buyer FAQ about valve materials.",
            "material_grade_status",
            "No material grade is asserted; confirm the applicable model record.",
            None,
            "awaiting_review",
        ),
    ],
)
def test_worker_maps_fixture_evidence_match_to_review_or_needs_information(
    isolated_database,
    monkeypatch,
    question_text,
    request_summary,
    fact_predicate,
    fact_value,
    fact_unit,
    expected_status,
):
    factory, path = isolated_database
    task_id, item_id, _, _, change_id, fact_id = _records(factory)
    with factory() as db:
        item = db.get(ContentGenerationItem, item_id)
        question = db.get(ProcurementQuestion, item.question_id)
        fact = db.get(Fact, fact_id)
        question.question = question_text
        item.request_summary = request_summary
        fact.predicate = fact_predicate
        fact.value = fact_value
        fact.unit = fact_unit
        db.commit()

    checkpoint_path = path.with_name("graph-checkpoints.sqlite")
    monkeypatch.setattr(
        content_worker,
        "checkpoint_saver",
        lambda: checkpoint_saver(f"sqlite:///{checkpoint_path.as_posix()}"),
    )
    worker = ContentGenerationWorker(gateway=FixtureModelDraftGateway())

    assert _claim_for_test(factory, task_id)
    worker._process(task_id, "test-token")

    with factory() as db:
        task = db.get(ContentGenerationTask, task_id)
        item = db.get(ContentGenerationItem, item_id)
        change = db.get(ChangeRequest, change_id)
        assert task.status == expected_status
        assert item.status == expected_status
        if expected_status == "needs_information":
            assert change.current_revision_id is None
        else:
            assert change.current_revision_id is not None


def test_worker_fails_closed_when_task_scope_does_not_match_item_sources(isolated_database, monkeypatch):
    factory, path = isolated_database
    task_id, item_id, _, site_id, _, _ = _records(factory)
    with factory() as db:
        other_workspace = Workspace(name="Other worker tenant", external_id="other-worker-tenant")
        db.add(other_workspace)
        db.flush()
        other_site = Site(workspace_id=other_workspace.id, name="Other site", base_url="http://other.invalid")
        db.add(other_site)
        db.flush()
        task = db.get(ContentGenerationTask, task_id)
        task.workspace_id = other_workspace.id
        task.site_id = other_site.id
        db.commit()

    checkpoint_path = path.with_name("scope-checkpoints.sqlite")
    monkeypatch.setattr(content_worker, "checkpoint_saver", lambda: checkpoint_saver(f"sqlite:///{checkpoint_path.as_posix()}"))
    class RecordingGateway(FixtureModelDraftGateway):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def draft(self, request):
            self.calls += 1
            return super().draft(request)

    gateway = RecordingGateway()
    assert ContentGenerationWorker(gateway=gateway).run_once() is True
    assert gateway.calls == 0
    with factory() as db:
        assert db.get(ContentGenerationTask, task_id).status == "failed"


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


def test_expired_content_lease_cannot_be_renewed(isolated_database):
    factory, _ = isolated_database
    with factory() as db:
        workspace = Workspace(name="Expired renew", external_id="expired-renew")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="Lease site", base_url="https://fixture.invalid")
        db.add(site)
        db.flush()
        question_set = ProcurementQuestionSet(workspace_id=workspace.id, site_id=site.id, name="Set")
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
            lease_token="expired-token",
            lease_expires_at=utcnow() - timedelta(seconds=1),
        )
        db.add(task)
        db.commit()
        task_id = task.id

    worker = ContentGenerationWorker(lease_duration=timedelta(minutes=5))
    with factory() as db:
        with pytest.raises(RuntimeError, match="lease was lost"):
            worker._renew(db, task_id, "expired-token")


def test_lost_content_lease_cannot_persist_a_generated_revision(isolated_database, monkeypatch):
    factory, _ = isolated_database
    task_id, item_id, _, _, change_id, _ = _records(factory)
    checkpoint_path = Path(factory.kw["bind"].url.database).with_name("graph-checkpoints.sqlite")
    monkeypatch.setattr(content_worker, "checkpoint_saver", lambda: checkpoint_saver(f"sqlite:///{checkpoint_path.as_posix()}"))
    assert _claim_for_test(factory, task_id)

    take_lease = False

    def replace_lease_then_renew(db, current_task_id, token):
        nonlocal take_lease
        if take_lease:
            take_lease = False
            with factory() as replacement_db:
                replacement_db.execute(
                    update(ContentGenerationTask)
                    .where(ContentGenerationTask.id == current_task_id)
                    .values(
                        lease_token="replacement-token",
                        lease_expires_at=utcnow() + timedelta(minutes=5),
                    )
                )
                replacement_db.commit()
        return original_renew(db, current_task_id, token)

    gateway = FixtureModelDraftGateway()
    original_draft = gateway.draft

    def draft_and_replace_lease(request):
        nonlocal take_lease
        result = original_draft(request)
        take_lease = True
        return result

    monkeypatch.setattr(gateway, "draft", draft_and_replace_lease)
    worker = ContentGenerationWorker(gateway=gateway)
    original_renew = worker._renew
    monkeypatch.setattr(worker, "_renew", replace_lease_then_renew)
    with pytest.raises(RuntimeError, match="lease was lost"):
        worker._process(task_id, "test-token")

    with factory() as db:
        task = db.get(ContentGenerationTask, task_id)
        item = db.get(ContentGenerationItem, item_id)
        change = db.get(ChangeRequest, change_id)
        assert task.status == "running"
        assert task.lease_token == "replacement-token"
        assert item.status == "queued"
        assert change.current_revision_id is None
        assert db.scalar(select(func.count()).select_from(ChangeRevision).where(ChangeRevision.change_request_id == change_id)) == 0


def test_content_worker_rejects_snapshot_hash_drift(isolated_database):
    factory, _ = isolated_database
    task_id, item_id, _, _, _, _ = _records(factory)
    with factory() as db:
        item = db.get(ContentGenerationItem, item_id)
        snapshot = db.get(PageSnapshot, item.snapshot_id)
        snapshot.content_hash = "b" * 64
        db.commit()
        with pytest.raises(RuntimeError, match="snapshot is no longer available"):
            ContentGenerationWorker._snapshot_context(db, item)


def test_worker_passes_market_scoped_external_guidance_to_gateway(isolated_database):
    factory, _ = isolated_database
    task_id, item_id, *_ = _records(factory)

    with factory() as db:
        item = db.get(ContentGenerationItem, item_id)
        question = db.get(ProcurementQuestion, item.question_id)
        question.question = "Can we claim green performance, and what tariff code applies?"
        question.target_market = "United States"
        item.request_summary = (
            "Prepare a cautious environmental claim and tariff checklist. "
            "Update the product title and meta description using Google search guidance."
        )
        db.commit()

    class RecordingGateway:
        def __init__(self):
            self.requests = []

        def draft(self, request):
            self.requests.append(request)
            return DraftNeedsInformation("No topic-matched fixture fact is available.")

    gateway = RecordingGateway()
    _claim_for_test(factory, task_id)
    ContentGenerationWorker(gateway=gateway)._process(task_id, "test-token")

    assert len(gateway.requests) == 1
    entries = gateway.requests[0].external_guidance
    ids = {entry["id"] for entry in entries}
    assert "ftc-green-guides" in ids
    assert "usitc-hts" in ids
    assert "google-title-links" in ids
    assert "google-meta-descriptions" in ids
    assert "eu-access2markets" not in ids
    assert "uk-trade-tariff" not in ids
    assert all(entry["source_url"].startswith("https://") for entry in entries)
    assert len(entries) <= 8


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


def test_content_worker_process_crash_and_restart_recovers_expired_lease(isolated_database, tmp_path):
    factory, database_path = isolated_database
    task_id, item_id, _, _, change_id, _ = _records(factory)
    marker = tmp_path / "content-worker-gateway-entered"
    database_url = f"sqlite:///{database_path.as_posix()}"
    child_env = os.environ.copy()
    child_env["DATABASE_URL"] = database_url
    child_env["MODEL_GATEWAY_API_KEY"] = ""

    crashing_script = textwrap.dedent(
        """
        import os
        import time
        from datetime import timedelta
        from pathlib import Path

        import backend.content_worker as content_worker
        from backend.content_worker import ContentGenerationWorker

        marker = Path(os.environ["CRASH_MARKER"])

        class BlockingGateway:
            def draft(self, request):
                marker.write_text("entered", encoding="ascii")
                while True:
                    time.sleep(1)

        content_worker.configured_draft_gateway = lambda: BlockingGateway()
        ContentGenerationWorker(lease_duration=timedelta(seconds=0.3)).run_once()
        """
    )
    child_env["CRASH_MARKER"] = str(marker)
    crashing_worker = subprocess.Popen(
        [sys.executable, "-c", crashing_script],
        cwd=Path(__file__).resolve().parents[2],
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and crashing_worker.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "crashed worker did not reach the draft gateway"
        crashing_worker.kill()
        crash_stdout, crash_stderr = crashing_worker.communicate(timeout=5)
    finally:
        if crashing_worker.poll() is None:
            crashing_worker.kill()
            crashing_worker.communicate(timeout=5)

    # The process was killed while holding the task lease. The replacement must
    # observe expiry, requeue the task, and run it under a new lease.
    time.sleep(0.5)
    restarting_script = textwrap.dedent(
        """
        import os
        import sys
        import time
        from datetime import timedelta

        from backend.content_worker import ContentGenerationWorker
        from backend.database import SessionLocal
        from backend.model_gateway import FixtureModelDraftGateway
        from backend.models import ContentGenerationTask

        task_id = int(os.environ["CONTENT_TASK_ID"])
        worker = ContentGenerationWorker(gateway=FixtureModelDraftGateway(), lease_duration=timedelta(seconds=5))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            worker.run_once()
            with SessionLocal() as db:
                task = db.get(ContentGenerationTask, task_id)
                if task is not None and task.status in {"succeeded", "failed", "needs_information", "awaiting_review"}:
                    print(task.status, flush=True)
                    break
            time.sleep(0.05)
        else:
            print("content task did not reach a terminal state", file=sys.stderr, flush=True)
            raise SystemExit(3)
        """
    )
    child_env["CONTENT_TASK_ID"] = str(task_id)
    restarted_worker = subprocess.run(
        [sys.executable, "-c", restarting_script],
        cwd=Path(__file__).resolve().parents[2],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert restarted_worker.returncode == 0, restarted_worker.stderr or restarted_worker.stdout or crash_stderr or crash_stdout

    with factory() as db:
        task = db.get(ContentGenerationTask, task_id)
        item = db.get(ContentGenerationItem, item_id)
        change = db.get(ChangeRequest, change_id)
        assert task.status == "awaiting_review"
        assert task.attempts == 2
        assert task.lease_token is None
        assert task.lease_expires_at is None
        assert item.status == "awaiting_review"
        assert change.current_revision_id is not None


def test_content_worker_restart_skips_terminal_items_and_processes_remaining_items(isolated_database, monkeypatch):
    factory, path = isolated_database
    task_id, first_item_id, workspace_id, site_id, first_change_id, fact_id = _records(factory)
    checkpoint_path = path.with_name("terminal-item-checkpoints.sqlite")
    monkeypatch.setattr(
        content_worker,
        "checkpoint_saver",
        lambda: checkpoint_saver(f"sqlite:///{checkpoint_path.as_posix()}"),
    )

    # Complete the first item and persist its revision. The second item is
    # added afterwards to model a batch that was interrupted between items.
    assert _claim_for_test(factory, task_id)
    ContentGenerationWorker(gateway=FixtureModelDraftGateway())._process(task_id, "test-token")

    with factory() as db:
        task = db.get(ContentGenerationTask, task_id)
        question = db.get(ProcurementQuestion, db.get(ContentGenerationItem, first_item_id).question_id)
        second_question = ProcurementQuestion(
            question_set_version_id=question.question_set_version_id,
            position=2,
            question="What is the confirmed pressure for the second page?",
            product=question.product,
            use_case=question.use_case,
            buyer_role=question.buyer_role,
            purchase_stage=question.purchase_stage,
            target_market=question.target_market,
            language=question.language,
        )
        second_page = Page(site_id=site_id, canonical_url="http://fixture.invalid/second")
        db.add_all([second_question, second_page])
        db.flush()
        second_snapshot = PageSnapshot(
            page_id=second_page.id,
            url=second_page.canonical_url,
            status_code=200,
            content_hash="b" * 64,
            is_synthetic=True,
        )
        second_change = ChangeRequest(workspace_id=workspace_id, site_id=site_id, title="Second fixture valve")
        db.add_all([second_snapshot, second_change])
        db.flush()
        second_item = ContentGenerationItem(
            task_id=task.id,
            question_id=second_question.id,
            page_id=second_page.id,
            change_request_id=second_change.id,
            snapshot_id=second_snapshot.id,
            snapshot_hash=second_snapshot.content_hash,
            request_summary="Write a synthetic valve with confirmed working pressure details.",
            required_fact_ids_json=f"[{fact_id}]",
            thread_id=f"content-task:{task.id}:item:second",
            status="queued",
        )
        db.add(second_item)
        db.flush()
        task.status = "running"
        task.lease_token = "restart-token"
        task.lease_expires_at = utcnow() + timedelta(minutes=5)
        db.commit()
        second_item_id = second_item.id

    class CountingGateway(FixtureModelDraftGateway):
        def __init__(self):
            super().__init__()
            self.calls: list[str] = []

        def draft(self, request):
            self.calls.append(request.generation_id)
            return super().draft(request)

    gateway = CountingGateway()
    ContentGenerationWorker(gateway=gateway)._process(task_id, "restart-token")

    with factory() as db:
        first_item = db.get(ContentGenerationItem, first_item_id)
        second_item = db.get(ContentGenerationItem, second_item_id)
        task = db.get(ContentGenerationTask, task_id)
        assert first_item.status == "awaiting_review"
        assert second_item.status == "awaiting_review"
        assert task.status == "awaiting_review"
        assert len(gateway.calls) == 1
        assert db.scalar(select(func.count()).select_from(ChangeRevision).where(ChangeRevision.change_request_id == first_change_id)) == 1
        assert db.scalar(select(func.count()).select_from(ChangeRevision).where(ChangeRevision.change_request_id == second_item.change_request_id)) == 1


def test_review_resume_worker_consumes_authoritative_approval_once(isolated_database, monkeypatch):
    factory, path = isolated_database
    task_id, item_id, workspace_id, _, change_id, _ = _records(factory)
    checkpoint_path = path.with_name("review-resume-checkpoints.sqlite")
    monkeypatch.setattr(content_worker, "checkpoint_saver", lambda: checkpoint_saver(f"sqlite:///{checkpoint_path.as_posix()}"))
    monkeypatch.setattr(review_worker, "SessionLocal", factory)
    monkeypatch.setattr(review_worker, "checkpoint_saver", lambda: checkpoint_saver(f"sqlite:///{checkpoint_path.as_posix()}"))

    assert _claim_for_test(factory, task_id)
    ContentGenerationWorker(gateway=FixtureModelDraftGateway())._process(task_id, "test-token")

    with factory() as db:
        item = db.get(ContentGenerationItem, item_id)
        change = db.get(ChangeRequest, change_id)
        revision = db.get(ChangeRevision, change.current_revision_id)
        change.state = "approved"
        revision.state = "approved"
        decision_id = "review-decision-1"
        db.add(ChangeApproval(change_request_id=change.id, revision_id=revision.id, revision_hash=revision.content_hash, reviewer="reviewer", decision="approved"))
        review = WorkflowReviewEvent(
            workspace_id=workspace_id,
            change_request_id=change.id,
            revision_id=revision.id,
            revision_hash=revision.content_hash,
            thread_id=item.thread_id,
            decision_id=decision_id,
            decision="approved",
            actor="reviewer",
            payload_hash="p" * 64,
        )
        db.add(review)
        db.flush()
        db.add(OutboxEvent(
            event_type="workflow.review_decision",
            aggregate_type="change_request",
            aggregate_id=str(change.id),
            idempotency_key="workflow-review:review-decision-1",
            payload_json=json.dumps({
                "workspace_id": workspace_id,
                "change_request_id": change.id,
                "revision_id": revision.id,
                "revision_hash": revision.content_hash,
                "thread_id": item.thread_id,
                "decision_id": decision_id,
                "decision": "approved",
                "actor": "reviewer",
            }),
        ))
        db.commit()

    worker = ReviewResumeWorker()
    assert worker.run_once() is True
    assert worker.run_once() is False

    with factory() as db:
        assert db.get(ContentGenerationItem, item_id).status == "succeeded"
        assert db.get(ContentGenerationTask, task_id).status == "succeeded"
        review = db.query(WorkflowReviewEvent).filter(WorkflowReviewEvent.decision_id == "review-decision-1").one()
        assert review.consumed_at is not None
        outbox = db.query(OutboxEvent).filter(OutboxEvent.idempotency_key == "workflow-review:review-decision-1").one()
        assert outbox.published_at is not None


def _claim_for_test(factory, task_id):
    with factory() as db:
        task = db.get(ContentGenerationTask, task_id)
        task.status = "running"
        task.lease_token = "test-token"
        task.lease_expires_at = utcnow() + timedelta(minutes=5)
        db.commit()
    return True
