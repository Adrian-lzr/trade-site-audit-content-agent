"""Small transaction-local audit helper for asynchronous worker lifecycle events."""

from __future__ import annotations

import hashlib
import json
from sqlalchemy import select

from .models import AuditEvent


def initiator_for(db, *, workspace_id: int, target_type: str, target_id: int, action: str) -> tuple[str | None, str | None]:
    """Return the authenticated creator and its request ID, if recorded."""
    event = db.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.target_type == target_type,
            AuditEvent.target_id == str(target_id),
            AuditEvent.action == action,
        )
        .order_by(AuditEvent.id.desc())
    )
    return (event.actor, event.run_id) if event is not None else (None, None)


def append_worker_audit(
    db,
    *,
    workspace_id: int,
    worker: str,
    action: str,
    target_type: str,
    target_id: int | str,
    initiator: str | None,
    run_id: str | None = None,
    task_id: int | None = None,
    thread_id: str | None = None,
    lease_token: str | None = None,
    attempt: int | None = None,
    revision_id: int | None = None,
    result: str | None = None,
    error_type: str | None = None,
) -> AuditEvent:
    """Append bounded IDs and lifecycle status to the caller's transaction.

    Raw lease tokens, prompts, provider bodies, and exception messages are never
    persisted. ``run_id`` is only a request correlation value when available.
    """
    lease_id = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()[:16] if lease_token else None
    event = AuditEvent(
        workspace_id=workspace_id,
        actor=f"worker:{worker}"[:255],
        action=action[:120],
        target_type=target_type[:120],
        target_id=str(target_id)[:120],
        before_version_json=json.dumps(
            {"initiator": initiator or "unavailable", "executor": f"worker:{worker}"},
            sort_keys=True,
            separators=(",", ":"),
        ),
        after_version_json=json.dumps(
            {
                key: value
                for key, value in {
                    "task_id": task_id,
                    "thread_id": thread_id,
                    "lease_id": lease_id,
                    "attempt": attempt,
                    "revision_id": revision_id,
                    "result": result,
                    "error_type": error_type,
                }.items()
                if value is not None
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        run_id=run_id,
    )
    db.add(event)
    return event
