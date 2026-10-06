from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Iterator

from sqlalchemy import select, update

from .config import settings
from .content_workflow import (
    MAX_SNAPSHOT_CONTEXT_LENGTH,
    SQLAlchemyContentWorkflowRepository,
    build_content_workflow,
    content_workflow_input,
    workflow_config,
)
from .database import SessionLocal
from .knowledge import runtime_guidance
from .model_gateway import FixtureModelDraftGateway, ModelGatewayConfig, OpenAICompatibleDraftGateway
from .models import ContentGenerationItem, ContentGenerationTask, ModelCall, Page, PageSnapshot, ProcurementQuestion, ProcurementQuestionSetVersion, ProcurementQuestionSet, Site, utcnow
from .services.accounting import finish_model_call, start_model_call
from .worker_audit import append_worker_audit, initiator_for


CONTENT_LEASE = timedelta(minutes=5)
_COMPLETED_ITEM_STATUSES = frozenset({"succeeded", "awaiting_review", "needs_information"})


@contextmanager
def checkpoint_saver(database_url: str | None = None):
    """Yield a persistent LangGraph saver matching the application database."""
    url = database_url or settings.database_url
    if url.startswith("sqlite:///"):
        raw_path = url[len("sqlite:///") :]
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        from langgraph.checkpoint.sqlite import SqliteSaver

        with SqliteSaver.from_conn_string(str(path)) as saver:
            saver.setup()
            yield saver
        return
    if url.startswith("postgresql") or url.startswith("postgres"):
        from langgraph.checkpoint.postgres import PostgresSaver

        with PostgresSaver.from_conn_string(_postgres_checkpoint_url(url)) as saver:
            saver.setup()
            yield saver
        return
    raise ValueError("DATABASE_URL must use sqlite or postgresql for content checkpoints")


def _thread_id(task_id: int, item_id: int) -> str:
    return f"content-task:{task_id}:item:{item_id}"


def _postgres_checkpoint_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def configured_draft_gateway():
    if not os.getenv("MODEL_GATEWAY_API_KEY", "").strip():
        return FixtureModelDraftGateway()
    return OpenAICompatibleDraftGateway(ModelGatewayConfig.from_env())


def _item_scope(db, task: ContentGenerationTask, item: ContentGenerationItem) -> tuple[ProcurementQuestion, Page]:
    question = db.get(ProcurementQuestion, item.question_id)
    page = db.get(Page, item.page_id)
    site = db.get(Site, task.site_id)
    version = db.get(ProcurementQuestionSetVersion, task.question_set_version_id)
    question_set = db.get(ProcurementQuestionSet, version.question_set_id) if version else None
    if (
        site is None or site.workspace_id != task.workspace_id
        or version is None or version.state != "frozen" or question_set is None
        or question_set.workspace_id != task.workspace_id or question_set.site_id != task.site_id
        or question is None or question.question_set_version_id != version.id
        or page is None or page.site_id != task.site_id
    ):
        raise RuntimeError("content item references data outside the task workspace and site")
    return question, page


def _procurement_context(db, item: ContentGenerationItem, task: ContentGenerationTask | None = None) -> dict[str, str]:
    task = task or db.get(ContentGenerationTask, item.task_id)
    if task is None:
        raise RuntimeError("content item task is no longer available")
    question, page = _item_scope(db, task, item)
    if question is None or page is None:
        raise RuntimeError("content item procurement context is no longer available")
    return {
        "question": question.question,
        "product": question.product,
        "use_case": question.use_case,
        "buyer_role": question.buyer_role,
        "purchase_stage": question.purchase_stage,
        "target_market": question.target_market,
        "language": question.language,
        "page_url": page.canonical_url,
    }


class _AccountingDraftGateway:
    """Record each provider attempt without holding the task lease transaction.

    The content workflow owns revision persistence and checkpointing, while this
    wrapper owns the durable model-call ledger.  A fixture has an explicit zero
    price; a configured provider without a returned charge remains
    ``unknown_result`` so a restart cannot silently retry a possibly billable
    request.
    """

    def __init__(self, gateway, *, workspace_id: int):
        self._gateway = gateway
        self._workspace_id = workspace_id
        self._synthetic = isinstance(gateway, FixtureModelDraftGateway) or bool(
            getattr(gateway, "is_synthetic", False)
        )

    def draft(self, request):
        call_key = f"content:{self._workspace_id}:{request.generation_id}"
        estimated_cost = Decimal("0") if self._synthetic else None
        with SessionLocal() as ledger_db:
            existing = ledger_db.scalar(
                select(ModelCall).where(
                    ModelCall.workspace_id == self._workspace_id,
                    ModelCall.call_key == call_key,
                )
            )
            if existing is not None:
                # A reserved row may represent a process crash after the
                # provider request was sent.  Treat that window as uncertain
                # and never issue a blind duplicate request.  Deterministic
                # zero-cost fixtures are the only safe replay exception.
                if existing.status == "reserved" and self._synthetic:
                    call_id = existing.id
                    ledger_db.commit()
                else:
                    if existing.status == "reserved":
                        finish_model_call(
                            ledger_db,
                            existing.id,
                            status="failed",
                            actual_cost=None,
                            cost_known=False,
                            error_code="uncertain_prior_attempt",
                            error_message=None,
                        )
                    ledger_db.commit()
                    raise RuntimeError(
                        f"model call {call_key} is already {existing.status}; reconcile before retry"
                    )
            else:
                call = start_model_call(
                    ledger_db,
                    workspace_id=self._workspace_id,
                    call_key=call_key,
                    call_type="content_generation",
                    input_value=request,
                    estimated_cost=estimated_cost,
                    budget_limit=Decimal("0"),
                    generation_id=request.generation_id,
                    attempt=1,
                    price_version="fixture-v1" if self._synthetic else None,
                    cost_source="synthetic_fixture" if self._synthetic else None,
                    strict_budget=False,
                )
                call_id = call.id
                ledger_db.commit()

        # A settled call has no replayable provider body in ModelCall.  Refuse
        # to invoke the provider again; LangGraph checkpoints normally avoid
        # this path, while a stale checkpoint gets an explicit reconciliation
        # error instead of a duplicate billable attempt.
        started = perf_counter()
        try:
            result = self._gateway.draft(request)
        except Exception as exc:
            self._finish(
                call_id,
                status="failed",
                output_value=None,
                actual_cost=Decimal("0") if self._synthetic else None,
                cost_known=self._synthetic,
                cost_source="synthetic_fixture" if self._synthetic else None,
                error_code=type(exc).__name__,
                latency_ms=_elapsed_ms(started),
            )
            raise

        self._finish(
            call_id,
            status="succeeded",
            output_value=result,
            actual_cost=Decimal("0") if self._synthetic else None,
            cost_known=self._synthetic,
            cost_source="synthetic_fixture" if self._synthetic else "provider_response",
            latency_ms=_elapsed_ms(started),
        )
        return result

    @staticmethod
    def _finish(call_id: int, **kwargs) -> None:
        with SessionLocal() as ledger_db:
            finish_model_call(ledger_db, call_id, **kwargs)
            ledger_db.commit()


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


class ContentGenerationWorker:
    def __init__(self, gateway=None, lease_duration: timedelta = CONTENT_LEASE):
        self.gateway = gateway
        self.lease_duration = lease_duration

    def recover_interrupted(self) -> int:
        now = utcnow()
        with SessionLocal() as db:
            result = db.execute(
                update(ContentGenerationTask)
                .where(
                    ContentGenerationTask.status == "running",
                    ContentGenerationTask.lease_expires_at.is_not(None),
                    ContentGenerationTask.lease_expires_at <= now,
                )
                .values(status="queued", lease_token=None, lease_expires_at=None, last_error="requeued after worker lease expired")
            )
            db.commit()
            return result.rowcount or 0

    def _claim(self) -> tuple[int, str] | None:
        now = utcnow()
        token = str(uuid.uuid4())
        with SessionLocal() as db:
            task = db.scalar(select(ContentGenerationTask).where(ContentGenerationTask.status == "queued").order_by(ContentGenerationTask.created_at))
            if task is None:
                return None
            changed = db.execute(
                update(ContentGenerationTask)
                .where(ContentGenerationTask.id == task.id, ContentGenerationTask.status == "queued")
                .values(status="running", lease_token=token, lease_expires_at=now + self.lease_duration, attempts=ContentGenerationTask.attempts + 1)
            )
            if not changed.rowcount:
                db.rollback()
                return None
            initiator, run_id = initiator_for(
                db, workspace_id=task.workspace_id, target_type="content_generation_task",
                target_id=task.id, action="content_generation_task.created",
            )
            append_worker_audit(
                db, workspace_id=task.workspace_id, worker="content", action="content_generation_task.claimed",
                target_type="content_generation_task", target_id=task.id, initiator=initiator,
                run_id=run_id, task_id=task.id, lease_token=token, attempt=task.attempts + 1,
                result="running",
            )
            db.commit()
            return task.id, token

    def _renew(self, db, task_id: int, token: str) -> None:
        now = utcnow()
        changed = db.execute(
            update(ContentGenerationTask)
            .execution_options(synchronize_session=False)
            .where(
                ContentGenerationTask.id == task_id,
                ContentGenerationTask.status == "running",
                ContentGenerationTask.lease_token == token,
                ContentGenerationTask.lease_expires_at.is_not(None),
                ContentGenerationTask.lease_expires_at > now,
            )
            .values(lease_expires_at=now + self.lease_duration)
        )
        if not changed.rowcount:
            raise RuntimeError("content task lease was lost")

    def _process(self, task_id: int, token: str) -> None:
        with SessionLocal() as db:
            task = db.get(ContentGenerationTask, task_id)
            if task is None or task.lease_token != token:
                return
            items = list(db.scalars(select(ContentGenerationItem).where(ContentGenerationItem.task_id == task_id).order_by(ContentGenerationItem.id)).all())
            repository = SQLAlchemyContentWorkflowRepository(
                SessionLocal,
                persist_guard=lambda persist_db: self._renew(persist_db, task_id, token),
            )
            owns_gateway = self.gateway is None
            gateway = self.gateway or configured_draft_gateway()
            accounting_gateway = _AccountingDraftGateway(gateway, workspace_id=task.workspace_id)
            try:
                with checkpoint_saver() as saver:
                    graph = build_content_workflow(repository, accounting_gateway, saver)
                    for item in items:
                        # A task lease can expire after an item has committed its
                        # revision and item status but before the batch is finalized.
                        # Replaying those items would call the model again even
                        # though their durable workflow state is already terminal.
                        if item.status in _COMPLETED_ITEM_STATUSES:
                            continue
                        self._renew(db, task_id, token)
                        item.thread_id = item.thread_id or _thread_id(task_id, item.id)
                        if hasattr(task, "generation_source"):
                            task.generation_source = "model_api" if isinstance(gateway, OpenAICompatibleDraftGateway) else "fixture"
                        db.commit()
                        procurement_context = _procurement_context(db, item, task)
                        initial = content_workflow_input(
                            workspace_id=task.workspace_id,
                            site_id=task.site_id,
                            change_request_id=item.change_request_id,
                            request_summary=item.request_summary,
                            procurement_context=procurement_context,
                            external_guidance=runtime_guidance({
                                **procurement_context,
                                "request_summary": item.request_summary,
                            }),
                            snapshot_context=self._snapshot_context(db, item, task),
                            snapshot_hash=item.snapshot_hash,
                            thread_id=item.thread_id,
                            required_fact_ids=(
                                json.loads(item.required_fact_ids_json)
                                if isinstance(item.required_fact_ids_json, str)
                                else item.required_fact_ids_json
                            ),
                        )
                        try:
                            result = graph.invoke(initial, workflow_config(item.thread_id))
                            # An interrupt is the workflow's explicit human-review pause;
                            # LangGraph may omit the state status in that return shape.
                            item.status = str(
                                result.get("status")
                                or ("awaiting_review" if result.get("__interrupt__") else "needs_information")
                            )
                        except Exception as exc:
                            # A lease can be reclaimed while a provider call is
                            # in flight. Only the current owner may record a
                            # failed item; an expired token must leave the
                            # replacement worker's state untouched.
                            self._renew(db, task_id, token)
                            item.status = "failed"
                            task.last_error = str(exc)[:2000]
                            initiator, run_id = initiator_for(
                                db, workspace_id=task.workspace_id, target_type="content_generation_task",
                                target_id=task.id, action="content_generation_task.created",
                            )
                            from .models import ChangeRequest, ChangeRevision
                            change = db.get(ChangeRequest, item.change_request_id)
                            revision = db.get(ChangeRevision, change.current_revision_id) if change and change.current_revision_id else None
                            append_worker_audit(
                                db, workspace_id=task.workspace_id, worker="content", action="content_generation_item.completed",
                                target_type="content_generation_item", target_id=item.id, initiator=initiator,
                                run_id=run_id, task_id=task.id, thread_id=item.thread_id, lease_token=token,
                                attempt=task.attempts, revision_id=revision.id if revision else None,
                                result="failed", error_type=type(exc).__name__,
                            )
                            db.commit()
                            raise
                        self._renew(db, task_id, token)
                        from .models import ChangeRequest, ChangeRevision
                        change = db.get(ChangeRequest, item.change_request_id)
                        revision = db.get(ChangeRevision, change.current_revision_id) if change and change.current_revision_id else None
                        initiator, run_id = initiator_for(
                            db, workspace_id=task.workspace_id, target_type="content_generation_task",
                            target_id=task.id, action="content_generation_task.created",
                        )
                        append_worker_audit(
                            db, workspace_id=task.workspace_id, worker="content", action="content_generation_item.completed",
                            target_type="content_generation_item", target_id=item.id, initiator=initiator,
                            run_id=run_id, task_id=task.id, thread_id=item.thread_id, lease_token=token,
                            attempt=task.attempts, revision_id=revision.id if revision else None,
                            result=item.status,
                        )
                        db.commit()
                self._renew(db, task_id, token)
                statuses = {item.status for item in items}
                if "failed" in statuses:
                    task.status = "failed"
                elif "needs_information" in statuses:
                    task.status = "needs_information"
                elif "awaiting_review" in statuses:
                    task.status = "awaiting_review"
                else:
                    task.status = "succeeded"
                task.lease_token = None
                task.lease_expires_at = None
                initiator, run_id = initiator_for(
                    db, workspace_id=task.workspace_id, target_type="content_generation_task",
                    target_id=task.id, action="content_generation_task.created",
                )
                append_worker_audit(
                    db, workspace_id=task.workspace_id, worker="content", action="content_generation_task.completed",
                    target_type="content_generation_task", target_id=task.id, initiator=initiator,
                    run_id=run_id, task_id=task.id, lease_token=token, attempt=task.attempts,
                    result=task.status,
                )
                db.commit()
            finally:
                if owns_gateway:
                    close = getattr(gateway, "close", None)
                    if callable(close):
                        close()

    @staticmethod
    def _snapshot_context(db, item: ContentGenerationItem, task: ContentGenerationTask | None = None) -> str:
        task = task or db.get(ContentGenerationTask, item.task_id)
        if task is None:
            raise RuntimeError("content item task is no longer available")
        _, page = _item_scope(db, task, item)
        snapshot = db.get(PageSnapshot, item.snapshot_id)
        if snapshot is None or snapshot.page_id != page.id or snapshot.content_hash != item.snapshot_hash:
            raise RuntimeError("content item snapshot is no longer available")
        # HTML is untrusted input. Keep a bounded excerpt for baseline context;
        # the workflow and model prompt explicitly prohibit treating it as instructions.
        return (snapshot.content or "")[:MAX_SNAPSHOT_CONTEXT_LENGTH]

    def run_once(self) -> bool:
        self.recover_interrupted()
        claimed = self._claim()
        if claimed is None:
            return False
        task_id, token = claimed
        try:
            self._process(task_id, token)
        except Exception:
            with SessionLocal() as db:
                db.execute(
                    update(ContentGenerationTask)
                    .where(ContentGenerationTask.id == task_id, ContentGenerationTask.lease_token == token)
                    .values(status="failed", lease_token=None, lease_expires_at=None)
                )
                db.commit()
        return True

    def run_forever(self, poll_interval: float = 1.0, stop_event=None) -> None:
        from threading import Event

        stop_event = stop_event or Event()
        while not stop_event.is_set():
            if not self.run_once():
                stop_event.wait(poll_interval)

