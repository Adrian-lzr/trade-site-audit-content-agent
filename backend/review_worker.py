"""Consume durable human review commands and resume paused content graphs."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from sqlalchemy import select, update

from .content_workflow import SQLAlchemyContentWorkflowRepository, build_content_workflow, workflow_config
from .content_worker import checkpoint_saver, configured_draft_gateway
from .database import SessionLocal
from .models import (
    ChangeApproval,
    ChangeRequest,
    ChangeRevision,
    ContentGenerationItem,
    ContentGenerationTask,
    OutboxEvent,
    WorkflowReviewEvent,
    utcnow,
)
from langgraph.types import Command


REVIEW_LEASE = timedelta(minutes=5)
REVIEW_EVENT_TYPE = "workflow.review_decision"
MAX_REVIEW_ATTEMPTS = 3


class ReviewResumeWorker:
    """Resume one approved/rejected graph event with lease fencing and idempotency."""

    def recover_interrupted(self) -> int:
        now = utcnow()
        with SessionLocal() as db:
            result = db.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.event_type == REVIEW_EVENT_TYPE,
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.lease_expires_at.is_not(None),
                    OutboxEvent.lease_expires_at <= now,
                )
                .values(lease_token=None, lease_expires_at=None)
            )
            db.commit()
            return result.rowcount or 0

    def _claim(self) -> tuple[int, str] | None:
        now = utcnow()
        token = str(uuid.uuid4())
        with SessionLocal() as db:
            event_id = db.scalar(
                select(OutboxEvent.id)
                .where(
                    OutboxEvent.event_type == REVIEW_EVENT_TYPE,
                    OutboxEvent.published_at.is_(None),
                    (OutboxEvent.lease_expires_at.is_(None) | (OutboxEvent.lease_expires_at <= now)),
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
            )
            if event_id is None:
                return None
            changed = db.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.published_at.is_(None),
                    (OutboxEvent.lease_expires_at.is_(None) | (OutboxEvent.lease_expires_at <= now)),
                )
                .values(lease_token=token, lease_expires_at=now + REVIEW_LEASE, attempts=OutboxEvent.attempts + 1)
            )
            if changed.rowcount != 1:
                db.rollback()
                return None
            db.commit()
            return event_id, token

    def _terminal(self, event_id: int, token: str, *, error: str | None = None) -> None:
        with SessionLocal() as db:
            db.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event_id, OutboxEvent.lease_token == token)
                .values(
                    published_at=utcnow(),
                    lease_token=None,
                    lease_expires_at=None,
                    last_error=error,
                )
            )
            db.commit()

    def _process(self, event_id: int, token: str) -> None:
        with SessionLocal() as db:
            outbox = db.get(OutboxEvent, event_id)
            if outbox is None or outbox.published_at is not None or outbox.lease_token != token:
                return
            payload = json.loads(outbox.payload_json or "{}")
            review = db.scalar(select(WorkflowReviewEvent).where(WorkflowReviewEvent.decision_id == payload.get("decision_id")))
            change = db.get(ChangeRequest, int(payload.get("change_request_id", 0)))
            revision = db.get(ChangeRevision, int(payload.get("revision_id", 0)))
            if review is None or change is None or revision is None:
                self._terminal(event_id, token, error="review event references missing records")
                return
            if review.consumed_at is not None:
                self._terminal(event_id, token, error=review.error)
                return
            if (
                review.workspace_id != change.workspace_id
                or change.current_revision_id != revision.id
                or review.revision_id != revision.id
                or review.revision_hash != revision.content_hash
                or payload.get("revision_hash") != revision.content_hash
                or payload.get("thread_id") != review.thread_id
            ):
                review.error = "review response is stale for the current revision"
                review.consumed_at = utcnow()
                db.commit()
                self._terminal(event_id, token, error=review.error)
                return
            approval = db.scalar(
                select(ChangeApproval).where(
                    ChangeApproval.change_request_id == change.id,
                    ChangeApproval.revision_id == revision.id,
                    ChangeApproval.revision_hash == revision.content_hash,
                    ChangeApproval.decision == review.decision,
                ).order_by(ChangeApproval.created_at.desc())
            )
            if approval is None:
                review.error = "no authoritative approval exists for review response"
                review.consumed_at = utcnow()
                db.commit()
                self._terminal(event_id, token, error=review.error)
                return
            item = db.scalar(select(ContentGenerationItem).where(ContentGenerationItem.thread_id == review.thread_id))

        # The graph call is outside the record transaction. Its checkpoint is
        # durable, and the outbox lease fences the final state write below.
        if item is None:
            self._mark_consumed(event_id, token, None, None, None)
            return
        gateway = configured_draft_gateway()
        try:
            repository = SQLAlchemyContentWorkflowRepository(SessionLocal)
            with checkpoint_saver() as saver:
                graph = build_content_workflow(repository, gateway, saver)
                result = graph.invoke(
                    Command(resume={"decision": review.decision, "decision_id": review.decision_id}),
                    config=workflow_config(review.thread_id),
                )
            expected = review.decision
            if result.get("status") != expected:
                raise RuntimeError(f"review checkpoint ended in {result.get('status')!r}, expected {expected!r}")
            self._mark_consumed(event_id, token, item.id, item.task_id, expected)
        except Exception as exc:
            with SessionLocal() as db:
                row = db.get(OutboxEvent, event_id)
                if row is not None and row.lease_token == token:
                    row.last_error = str(exc)[:2000]
                    row.lease_token = None
                    row.lease_expires_at = None
                    if row.attempts >= MAX_REVIEW_ATTEMPTS:
                        row.published_at = utcnow()
                        review = db.scalar(select(WorkflowReviewEvent).where(WorkflowReviewEvent.decision_id == payload.get("decision_id")))
                        if review is not None:
                            review.error = str(exc)[:2000]
                    db.commit()
            raise
        finally:
            close = getattr(gateway, "close", None)
            if callable(close):
                close()

    def _mark_consumed(self, event_id: int, token: str, item_id: int | None, task_id: int | None, decision: str | None) -> None:
        with SessionLocal() as db:
            outbox = db.get(OutboxEvent, event_id)
            if outbox is None or outbox.lease_token != token:
                return
            review = db.scalar(select(WorkflowReviewEvent).where(WorkflowReviewEvent.decision_id == json.loads(outbox.payload_json or "{}").get("decision_id")))
            if review is not None:
                review.consumed_at = utcnow()
                review.error = None
            if item_id is not None and decision is not None:
                item = db.get(ContentGenerationItem, item_id)
                if item is not None:
                    item.status = "succeeded" if decision == "approved" else "needs_information"
                    if task_id is not None:
                        task = db.get(ContentGenerationTask, task_id)
                        if task is not None:
                            statuses = {row.status for row in task.items}
                            task.status = "needs_information" if "needs_information" in statuses else "succeeded"
            outbox.published_at = utcnow()
            outbox.lease_token = None
            outbox.lease_expires_at = None
            outbox.last_error = None
            db.commit()

    def run_once(self) -> bool:
        self.recover_interrupted()
        claimed = self._claim()
        if claimed is None:
            return False
        event_id, token = claimed
        self._process(event_id, token)
        return True


__all__ = ["ReviewResumeWorker", "REVIEW_EVENT_TYPE"]
