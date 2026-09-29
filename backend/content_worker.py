from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Iterator

from sqlalchemy import select, update

from .config import settings
from .content_workflow import (
    SQLAlchemyContentWorkflowRepository,
    build_content_workflow,
    content_workflow_input,
    workflow_config,
)
from .database import SessionLocal
from .model_gateway import FixtureModelDraftGateway, ModelGatewayConfig, OpenAICompatibleDraftGateway
from .models import ContentGenerationItem, ContentGenerationTask, Page, ProcurementQuestion, utcnow


CONTENT_LEASE = timedelta(minutes=5)


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


def _procurement_context(db, item: ContentGenerationItem) -> dict[str, str]:
    question = db.get(ProcurementQuestion, item.question_id)
    page = db.get(Page, item.page_id)
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
            db.commit()
            return task.id, token

    def _renew(self, db, task_id: int, token: str) -> None:
        changed = db.execute(
            update(ContentGenerationTask)
            .where(ContentGenerationTask.id == task_id, ContentGenerationTask.status == "running", ContentGenerationTask.lease_token == token)
            .values(lease_expires_at=utcnow() + self.lease_duration)
        )
        if not changed.rowcount:
            raise RuntimeError("content task lease was lost")

    def _process(self, task_id: int, token: str) -> None:
        with SessionLocal() as db:
            task = db.get(ContentGenerationTask, task_id)
            if task is None or task.lease_token != token:
                return
            items = list(db.scalars(select(ContentGenerationItem).where(ContentGenerationItem.task_id == task_id).order_by(ContentGenerationItem.id)).all())
            repository = SQLAlchemyContentWorkflowRepository(SessionLocal)
            owns_gateway = self.gateway is None
            gateway = self.gateway or configured_draft_gateway()
            try:
                with checkpoint_saver() as saver:
                    graph = build_content_workflow(repository, gateway, saver)
                    for item in items:
                        self._renew(db, task_id, token)
                        item.thread_id = item.thread_id or _thread_id(task_id, item.id)
                        if hasattr(task, "generation_source"):
                            task.generation_source = "model_api" if isinstance(gateway, OpenAICompatibleDraftGateway) else "fixture"
                        db.commit()
                        initial = content_workflow_input(
                            workspace_id=task.workspace_id,
                            site_id=task.site_id,
                            change_request_id=item.change_request_id,
                            request_summary=item.request_summary,
                            procurement_context=_procurement_context(db, item),
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
                            item.status = "failed"
                            task.last_error = str(exc)[:2000]
                            db.commit()
                            raise
                        db.commit()
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
                db.commit()
            finally:
                if owns_gateway:
                    close = getattr(gateway, "close", None)
                    if callable(close):
                        close()

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

