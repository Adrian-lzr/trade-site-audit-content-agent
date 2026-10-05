from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta

from sqlalchemy import select, update

from .database import SessionLocal
from .git_publisher import ChangeSet, GitPublisher, GitPublisherError, IdempotencyConflictError, RollbackConflictError
from .models import AuditEvent, ChangeApproval, ChangeRequest, ChangeRevision, ChangeState, Fact, FactStatus, FactVisibility, OutboxEvent, Page, PageSnapshot, PublicationAttempt, Site, utcnow
from .services.facts import FactResolutionError, assert_bindings_current
from .worker_audit import append_worker_audit


PUBLICATION_LEASE = timedelta(minutes=5)
MAX_PUBLICATION_ATTEMPTS = 3
PUBLICATION_EVENT_TYPES = ("change.publish_requested", "change.rollback_requested")


def _target_name() -> str:
    return os.getenv("GIT_PUBLISH_TARGET", "demo").strip() or "demo"


def _change_files(change: ChangeRequest, revision: ChangeRevision) -> dict[str, str]:
    payload = {
        "change_request_id": change.id,
        "revision_id": revision.id,
        "revision": revision.revision,
        "revision_hash": revision.content_hash,
        "base_snapshot_id": revision.base_snapshot_id,
        "base_content_hash": revision.base_content_hash,
        "field_diff": json.loads(revision.field_diff_json or "{}"),
        "fact_versions": json.loads(revision.fact_versions_json or "[]"),
    }
    path = f".trade-visibility/changes/{change.id}/revision-{revision.revision}.json"
    return {path: json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"}


def _assert_publishable(db, change: ChangeRequest, revision: ChangeRevision, site: Site) -> None:
    if site.workspace_id != change.workspace_id or site.id != change.site_id:
        raise GitPublisherError("publication site does not belong to the change workspace")
    if change.current_revision_id != revision.id or revision.change_request_id != change.id:
        raise GitPublisherError("publication revision is no longer current")
    approval = db.scalar(
        select(ChangeApproval).where(
            ChangeApproval.change_request_id == change.id,
            ChangeApproval.revision_id == revision.id,
            ChangeApproval.revision_hash == revision.content_hash,
            ChangeApproval.decision == "approved",
        ).order_by(ChangeApproval.created_at.desc())
    )
    if approval is None:
        raise GitPublisherError("publication revision no longer has a matching approval")
    if revision.base_snapshot_id is None or not revision.base_content_hash:
        raise GitPublisherError("publication requires a bound page snapshot")
    snapshot = db.get(PageSnapshot, revision.base_snapshot_id)
    if snapshot is None or snapshot.content_hash != revision.base_content_hash:
        raise GitPublisherError("publication base snapshot is missing or changed")
    page = db.get(Page, snapshot.page_id)
    if page is None or page.site_id != site.id:
        raise GitPublisherError("publication snapshot no longer belongs to the site")
    latest = db.scalar(
        select(PageSnapshot)
        .where(PageSnapshot.page_id == page.id)
        .order_by(PageSnapshot.fetched_at.desc(), PageSnapshot.id.desc())
    )
    if latest is None or latest.id != snapshot.id or latest.content_hash != revision.base_content_hash:
        raise GitPublisherError("publication page snapshot changed after approval")
    try:
        assert_bindings_current(db, change.workspace_id, json.loads(revision.fact_versions_json or "[]"))
    except FactResolutionError as exc:
        raise GitPublisherError(str(exc)) from exc


class PublicationWorker:
    def __init__(self, lease_duration: timedelta = PUBLICATION_LEASE):
        self.lease_duration = lease_duration

    @staticmethod
    def _audit(db, event: OutboxEvent, token: str | None, *, result: str, error_type: str | None = None, rollback: bool = False) -> None:
        payload = json.loads(event.payload_json or "{}")
        change = db.get(ChangeRequest, int(payload.get("change_request_id", 0)))
        if change is None:
            return
        attempt = db.scalar(select(PublicationAttempt).where(PublicationAttempt.idempotency_key == event.idempotency_key))
        creator = db.scalar(select(AuditEvent).where(
            AuditEvent.workspace_id == change.workspace_id,
            AuditEvent.target_type == "publication_attempt",
            AuditEvent.target_id == str(attempt.id if attempt else ""),
            AuditEvent.action.in_(("change.publish_requested", "publication.rollback_requested")),
        ).order_by(AuditEvent.id.desc()))
        append_worker_audit(
            db, workspace_id=change.workspace_id, worker="publication",
            action="publication.rollback.completed" if rollback else "publication.completed",
            target_type="outbox_event", target_id=event.id, initiator=creator.actor if creator else None,
            run_id=creator.run_id if creator else None, task_id=event.id, lease_token=token,
            attempt=event.attempts, revision_id=int(payload.get("revision_id", 0)) or None,
            result=result, error_type=error_type,
        )

    def recover_interrupted(self) -> int:
        now = utcnow()
        with SessionLocal() as db:
            result = db.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.event_type.in_(PUBLICATION_EVENT_TYPES),
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.lease_expires_at.is_not(None),
                    OutboxEvent.lease_expires_at <= now,
                )
                .values(lease_token=None, lease_expires_at=None)
            )
            db.commit()
            return result.rowcount or 0

    def _claim(self) -> int | None:
        now = utcnow()
        with SessionLocal() as db:
            event_id = db.scalar(
                select(OutboxEvent.id)
                .where(
                    OutboxEvent.event_type.in_(PUBLICATION_EVENT_TYPES),
                    OutboxEvent.published_at.is_(None),
                    (OutboxEvent.lease_expires_at.is_(None) | (OutboxEvent.lease_expires_at <= now)),
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
            )
            if event_id is None:
                return None
            token = str(uuid.uuid4())
            result = db.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.published_at.is_(None),
                    (OutboxEvent.lease_expires_at.is_(None) | (OutboxEvent.lease_expires_at <= now)),
                )
                .values(lease_token=token, lease_expires_at=now + self.lease_duration, attempts=OutboxEvent.attempts + 1)
            )
            if result.rowcount == 1:
                event = db.get(OutboxEvent, event_id)
                payload = json.loads(event.payload_json or "{}") if event else {}
                change = db.get(ChangeRequest, int(payload.get("change_request_id", 0)))
                initiator = None
                if change is not None:
                    attempt = db.scalar(select(PublicationAttempt).where(PublicationAttempt.idempotency_key == event.idempotency_key))
                    creator = db.scalar(select(AuditEvent).where(
                        AuditEvent.workspace_id == change.workspace_id,
                        AuditEvent.target_type == "publication_attempt",
                        AuditEvent.target_id == str(attempt.id if attempt else ""),
                        AuditEvent.action == "change.publish_requested",
                    ).order_by(AuditEvent.id.desc()))
                    initiator = creator.actor if creator else None
                    append_worker_audit(
                        db, workspace_id=change.workspace_id, worker="publication", action="publication.claimed",
                        target_type="outbox_event", target_id=event_id, initiator=initiator,
                        run_id=creator.run_id if creator else None, task_id=event_id,
                        lease_token=token, attempt=(event.attempts + 1) if event else None,
                        revision_id=int(payload.get("revision_id", 0)) or None, result="running",
                    )
            db.commit()
            return event_id if result.rowcount == 1 else None

    def _finish(self, event_id: int, *, token: str | None = None, error: str | None = None, published: bool = False) -> None:
        with SessionLocal() as db:
            event = db.get(OutboxEvent, event_id)
            if event is None:
                return
            if token is not None and event.lease_token != token:
                return
            event.last_error = error
            event.lease_token = None
            event.lease_expires_at = None
            if published:
                event.published_at = utcnow()
            db.commit()

    def _process_rollback(self, db, event: OutboxEvent, token: str | None, payload: dict) -> None:
        """Process one guarded rollback event while holding its outbox lease."""
        try:
            change = db.get(ChangeRequest, int(payload["change_request_id"]))
            revision = db.get(ChangeRevision, int(payload["revision_id"]))
            site = db.get(Site, int(payload["site_id"]))
            source_id = int(payload["source_attempt_id"])
            rollback_id = int(payload["rollback_attempt_id"])
            source = db.get(PublicationAttempt, source_id)
            attempt = db.get(PublicationAttempt, rollback_id)
            if (
                change is None
                or revision is None
                or site is None
                or source is None
                or attempt is None
                or source.change_request_id != change.id
                or attempt.change_request_id != change.id
                or attempt.rollback_of_attempt_id != source.id
                or attempt.idempotency_key != event.idempotency_key
                or source.revision_id != revision.id
                or site.workspace_id != change.workspace_id
                or site.id != change.site_id
            ):
                raise GitPublisherError("rollback references are no longer valid")

            expected = str(payload.get("expected_current_sha") or attempt.expected_current_sha or "")
            if not expected or expected != attempt.expected_current_sha:
                raise RollbackConflictError("rollback expected commit is missing or changed")
            if not source.deployed_commit_sha or source.deployed_commit_sha != expected:
                raise RollbackConflictError("current deployment changed; rollback expected SHA no longer matches")
            if source.deployment_status not in {"rollback_pending", "verified"}:
                raise GitPublisherError("rollback source is no longer rollback-compatible")

            if attempt.status in {"submitted", "not_configured", "failed"}:
                self._audit(db, event, token, result=attempt.status, rollback=True)
                marked = db.execute(
                    update(OutboxEvent)
                    .execution_options(synchronize_session=False)
                    .where(OutboxEvent.id == event.id, OutboxEvent.lease_token == token)
                    .values(published_at=utcnow(), lease_token=None, lease_expires_at=None, last_error=None)
                )
                if marked.rowcount != 1:
                    db.rollback()
                    return
                db.commit()
                return

            if not source.branch or not source.commit_sha:
                raise GitPublisherError("rollback source has no local publication branch or commit")
            repository = os.getenv("GIT_PUBLISH_REPOSITORY", "").strip()
            if not repository:
                attempt.status = "not_configured"
                attempt.error = "GIT_PUBLISH_REPOSITORY is not configured; no external write was attempted"
                self._audit(db, event, token, result=attempt.status, rollback=True)
                marked = db.execute(
                    update(OutboxEvent)
                    .execution_options(synchronize_session=False)
                    .where(
                        OutboxEvent.id == event.id,
                        OutboxEvent.lease_token == token,
                        OutboxEvent.lease_expires_at > utcnow(),
                    )
                    .values(published_at=utcnow(), lease_token=None, lease_expires_at=None, last_error=None)
                )
                if marked.rowcount != 1:
                    db.rollback()
                    return
                db.commit()
                return

            result = GitPublisher(targets={attempt.target or payload.get("target") or _target_name(): repository}).rollback(
                target=attempt.target or payload.get("target") or _target_name(),
                source_branch=source.branch,
                source_commit=source.commit_sha,
                expected_current_sha=expected,
                idempotency_key=event.idempotency_key,
                title=attempt.rollback_reason or f"Rollback publication {source.id}",
            )
            attempt.status = "submitted"
            attempt.branch = result.branch
            attempt.commit_sha = result.commit
            attempt.external_id = result.commit
            attempt.error = None
            self._audit(db, event, token, result=attempt.status, rollback=True)
            current_event = db.get(OutboxEvent, event.id)
            if current_event is None or current_event.lease_token != token or current_event.lease_expires_at is None:
                db.rollback()
                return
            marked = db.execute(
                update(OutboxEvent)
                .execution_options(synchronize_session=False)
                .where(
                    OutboxEvent.id == event.id,
                    OutboxEvent.lease_token == token,
                    OutboxEvent.lease_expires_at > utcnow(),
                )
                .values(published_at=utcnow(), lease_token=None, lease_expires_at=None, last_error=None)
            )
            if marked.rowcount != 1:
                db.rollback()
                return
            db.commit()
        except Exception:
            raise

    def _process(self, event_id: int) -> None:
        token: str | None = None
        with SessionLocal() as db:
            event = db.get(OutboxEvent, event_id)
            if event is None or event.published_at is not None:
                return
            token = event.lease_token
            try:
                payload = json.loads(event.payload_json or "{}")
                if event.event_type == "change.rollback_requested":
                    self._process_rollback(db, event, token, payload)
                    return
                change = db.get(ChangeRequest, int(payload["change_request_id"]))
                revision = db.get(ChangeRevision, int(payload["revision_id"]))
                site = db.get(Site, int(payload["site_id"]))
                if change is None or revision is None or site is None or change.current_revision_id != revision.id:
                    raise GitPublisherError("publication references are no longer current")
                _assert_publishable(db, change, revision, site)
                key = event.idempotency_key
                attempt = db.scalar(select(PublicationAttempt).where(PublicationAttempt.idempotency_key == key))
                if attempt is None:
                    attempt = PublicationAttempt(
                        change_request_id=change.id,
                        revision_id=revision.id,
                        idempotency_key=key,
                        target=payload.get("target") or _target_name(),
                        status="queued",
                    )
                    db.add(attempt)
                    db.flush()
                if attempt.status in {"submitted", "not_configured", "failed"}:
                    self._audit(db, event, token, result=attempt.status)
                    event.published_at = utcnow()
                    event.lease_token = None
                    event.lease_expires_at = None
                    db.commit()
                    return

                repository = os.getenv("GIT_PUBLISH_REPOSITORY", "").strip()
                if not repository:
                    attempt.status = "not_configured"
                    attempt.error = "GIT_PUBLISH_REPOSITORY is not configured; no external write was attempted"
                    self._audit(db, event, token, result=attempt.status)
                    marked = db.execute(
                        update(OutboxEvent)
                        .execution_options(synchronize_session=False)
                        .where(
                            OutboxEvent.id == event_id,
                            OutboxEvent.lease_token == token,
                            OutboxEvent.lease_expires_at > utcnow(),
                        )
                        .values(published_at=utcnow(), lease_token=None, lease_expires_at=None, last_error=None)
                    )
                    if marked.rowcount != 1:
                        db.rollback()
                        return
                    db.commit()
                    return

                result = GitPublisher(targets={attempt.target or _target_name(): repository}).publish(
                    ChangeSet(
                        changeset_id=str(change.id),
                        revision=revision.revision,
                        revision_hash=revision.content_hash,
                        target=attempt.target or _target_name(),
                        files=_change_files(change, revision),
                        title=change.title,
                    )
                )
                attempt.status = "submitted"
                attempt.branch = result.branch
                attempt.commit_sha = result.commit
                attempt.external_id = result.commit
                attempt.error = None
                self._audit(db, event, token, result=attempt.status)
                marked = db.execute(
                    update(OutboxEvent)
                    .execution_options(synchronize_session=False)
                    .where(
                        OutboxEvent.id == event_id,
                        OutboxEvent.lease_token == token,
                        OutboxEvent.lease_expires_at > utcnow(),
                    )
                    .values(published_at=utcnow(), lease_token=None, lease_expires_at=None, last_error=None)
                )
                if marked.rowcount != 1:
                    db.rollback()
                    return
                change.state = ChangeState.publishing.value
                db.commit()
            except Exception as exc:
                db.rollback()
                with SessionLocal() as failed_db:
                    event = failed_db.get(OutboxEvent, event_id)
                    if event is None or (token is not None and event.lease_token != token):
                        return
                    rollback_conflict = event.event_type == "change.rollback_requested" and isinstance(
                        exc, (RollbackConflictError, IdempotencyConflictError)
                    )
                    next_attempt_status = "failed" if rollback_conflict or event.attempts >= MAX_PUBLICATION_ATTEMPTS else "queued"
                    marked = failed_db.execute(
                        update(OutboxEvent)
                        .execution_options(synchronize_session=False)
                        .where(
                            OutboxEvent.id == event_id,
                            OutboxEvent.lease_token == token,
                            OutboxEvent.lease_expires_at > utcnow(),
                        )
                        .values(
                            last_error=str(exc)[:1000],
                            lease_token=None,
                            lease_expires_at=None,
                            published_at=utcnow()
                            if rollback_conflict or event.attempts >= MAX_PUBLICATION_ATTEMPTS
                            else None,
                        )
                    )
                    if marked.rowcount != 1:
                        failed_db.rollback()
                        return
                    attempt = failed_db.scalar(select(PublicationAttempt).where(PublicationAttempt.idempotency_key == event.idempotency_key))
                    if attempt is not None:
                        attempt.status = next_attempt_status
                        attempt.error = str(exc)[:1000]
                    self._audit(failed_db, event, token, result=next_attempt_status,
                                error_type=type(exc).__name__,
                                rollback=event.event_type == "change.rollback_requested")
                    failed_db.commit()

    def run_once(self) -> bool:
        self.recover_interrupted()
        event_id = self._claim()
        if event_id is None:
            return False
        self._process(event_id)
        return True


__all__ = ["PublicationWorker", "PUBLICATION_LEASE"]
