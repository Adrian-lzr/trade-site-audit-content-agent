"""Small, dependency-free request context and redacted event logging helpers."""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import platform
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import Response


REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID = contextvars.ContextVar("trade_visibility_request_id", default=None)
_MAX_REQUEST_ID_LENGTH = 120
_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "raw_response",
    "answer_text",
    "content",
    "html",
    "body",
    "value",
}
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
_KEY_VALUE_PATTERN = re.compile(r"(?i)((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[^\s,;]+")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
CORRELATION_REPORT_SCHEMA = "t16.correlation.v1"


def request_id(value: str | None = None) -> str:
    """Return a safe request ID, preserving a valid caller correlation ID."""

    candidate = (value or "").strip()
    if not _REQUEST_ID_PATTERN.fullmatch(candidate[:_MAX_REQUEST_ID_LENGTH]):
        return uuid4().hex
    return candidate


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def redact(value: Any, *, key: str | None = None) -> Any:
    """Redact secrets and large evidence fields before they reach logs."""

    if key and any(marker in key.casefold() for marker in _SENSITIVE_KEYS):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {str(item_key): redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _BEARER_PATTERN.sub(r"\1[redacted]", value)
        value = _KEY_VALUE_PATTERN.sub(r"\1[redacted]", value)
        return value[:2000] + ("..." if len(value) > 2000 else "")
    return value


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit one JSON event with request correlation and redacted fields."""

    payload = {"event": event, "request_id": current_request_id(), **redact(fields)}
    logger.info(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _commit_sha(repo_root: str | Path | None = None) -> str | None:
    root = Path(repo_root or Path(__file__).resolve().parents[1])
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None


def _artifact_metadata(paths: list[str | Path]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path)
        item: dict[str, Any] = {"path": str(path)}
        try:
            data = path.read_bytes()
        except OSError as exc:
            item.update({"exists": False, "sha256": None, "error": type(exc).__name__})
        else:
            item.update({"exists": True, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        artifacts.append(item)
    return artifacts


def _row_fields(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields:
        value = getattr(row, field, None)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        result[field] = value
    return result


def _report_digest(report: Mapping[str, Any]) -> str:
    material = dict(report)
    material.pop("report_sha256", None)
    return hashlib.sha256(json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def build_correlation_report(
    db: Any,
    *,
    workspace_id: int | None = None,
    request_id: str | None = None,
    task_id: int | None = None,
    change_request_id: int | None = None,
    visibility_run_id: int | None = None,
    command: list[str] | tuple[str, ...] | None = None,
    artifact_paths: list[str | Path] | tuple[str | Path, ...] = (),
    repo_root: str | Path | None = None,
    max_records: int = 1000,
) -> dict[str, Any]:
    """Build a read-only, ID-only report across the T16 execution chain.

    The report deliberately excludes prompts, response bodies, page content and
    actor secrets. Missing joins are emitted as blockers; no synthetic row is
    created to make a correlation appear complete.
    """

    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError

    from .models import (
        AuditEvent,
        ChangeRequest,
        ChangeRevision,
        ContentGenerationItem,
        ContentGenerationTask,
        ModelCall,
        PublicationAttempt,
        VisibilityRun,
        VisibilitySample,
    )

    scope_bounded = any(value is not None for value in (workspace_id, request_id, task_id, change_request_id, visibility_run_id))
    if not scope_bounded:
        max_records = 0

    query_errors: list[dict[str, str]] = []

    def rows(model: Any, *predicates: Any) -> list[Any]:
        query = select(model)
        for predicate in predicates:
            query = query.where(predicate)
        try:
            return list(db.scalars(query.limit(max_records)).all())
        except OperationalError as exc:
            query_errors.append({"model": getattr(model, "__tablename__", str(model)), "error": type(exc.orig).__name__})
            return []

    def workspace_filter(model: Any) -> tuple[Any, ...]:
        return (model.workspace_id == workspace_id,) if workspace_id is not None and hasattr(model, "workspace_id") else ()

    audit_predicates: list[Any] = list(workspace_filter(AuditEvent))
    if request_id:
        audit_predicates.append(AuditEvent.run_id == request_id)
    elif task_id is not None or change_request_id is not None or visibility_run_id is not None or (scope_bounded and workspace_id is None):
        # Task/change/run selectors have no safe join to AuditEvent.
        audit_predicates.append(AuditEvent.id == -1)
    audits = rows(AuditEvent, *audit_predicates)
    task_predicates: list[Any] = list(workspace_filter(ContentGenerationTask))
    if task_id is not None:
        task_predicates.append(ContentGenerationTask.id == task_id)
    elif any(value is not None for value in (request_id, change_request_id, visibility_run_id)):
        # Request/change/run selectors do not have a foreign key to content
        # tasks. Do not broaden them into a full task-table scan.
        task_predicates.append(ContentGenerationTask.id == -1)
    tasks = rows(ContentGenerationTask, *task_predicates)
    task_ids = {task.id for task in tasks}
    # Child tables have no workspace column of their own. Never let an empty
    # parent result turn a bounded report into an unfiltered child-table scan.
    items = rows(ContentGenerationItem, ContentGenerationItem.task_id.in_(task_ids) if task_ids else ContentGenerationItem.id == -1)
    item_change_ids = {item.change_request_id for item in items}
    change_predicates: list[Any] = list(workspace_filter(ChangeRequest))
    if change_request_id is not None:
        change_predicates.append(ChangeRequest.id == change_request_id)
    elif item_change_ids:
        change_predicates.append(ChangeRequest.id.in_(item_change_ids))
    elif task_id is not None or request_id is not None or visibility_run_id is not None or (scope_bounded and workspace_id is None):
        change_predicates.append(ChangeRequest.id == -1)
    changes = rows(ChangeRequest, *change_predicates)
    change_ids = {change.id for change in changes}
    revisions = rows(ChangeRevision, ChangeRevision.change_request_id.in_(change_ids) if change_ids else ChangeRevision.id == -1)
    revision_ids = {revision.id for revision in revisions}
    generation_ids = {revision.generation_id for revision in revisions if revision.generation_id}
    model_predicates: list[Any] = list(workspace_filter(ModelCall))
    if generation_ids:
        model_predicates.append(ModelCall.generation_id.in_(generation_ids))
    elif scope_bounded:
        model_predicates.append(ModelCall.id == -1)
    model_calls = rows(ModelCall, *model_predicates)
    publication_predicates: list[Any] = []
    if revision_ids:
        publication_predicates.append(PublicationAttempt.revision_id.in_(revision_ids))
    elif scope_bounded:
        publication_predicates.append(PublicationAttempt.id == -1)
    publications = rows(PublicationAttempt, *publication_predicates)
    visibility_predicates: list[Any] = list(workspace_filter(VisibilityRun))
    if visibility_run_id is not None:
        visibility_predicates.append(VisibilityRun.id == visibility_run_id)
    elif request_id:
        visibility_predicates.append(VisibilityRun.request_id == request_id)
    elif scope_bounded:
        # Task/change selectors have no safe join to visibility runs.
        visibility_predicates.append(VisibilityRun.id == -1)
    visibility_runs = rows(VisibilityRun, *visibility_predicates)
    run_ids = {run.id for run in visibility_runs}
    sample_predicates: list[Any] = [VisibilitySample.run_id.in_(run_ids) if run_ids else VisibilitySample.id == -1]
    if request_id:
        sample_predicates.append(VisibilitySample.request_id == request_id)
    samples = rows(VisibilitySample, *sample_predicates)

    audit_data = [_row_fields(event, ("id", "workspace_id", "actor", "action", "target_type", "target_id", "run_id", "created_at")) for event in audits]
    task_data = [_row_fields(task, ("id", "workspace_id", "site_id", "status", "generation_source", "attempts", "created_at")) for task in tasks]
    item_data = [_row_fields(item, ("id", "task_id", "change_request_id", "snapshot_id", "snapshot_hash", "status", "thread_id", "created_at")) for item in items]
    revision_data = [_row_fields(revision, ("id", "change_request_id", "revision", "state", "content_hash", "generation_id", "created_at")) for revision in revisions]
    model_data = [_row_fields(call, ("id", "workspace_id", "call_key", "call_type", "generation_id", "sample_id", "provider_request_id", "input_hash", "output_hash", "status", "cost_known", "amount", "currency", "price_version", "latency_ms", "created_at", "completed_at")) for call in model_calls]
    publication_data = [_row_fields(attempt, ("id", "change_request_id", "revision_id", "status", "target", "commit_sha", "deployment_status", "deployment_id", "deployed_commit_sha", "verified_at", "created_at", "updated_at")) for attempt in publications]
    visibility_data = [_row_fields(run, ("id", "workspace_id", "site_id", "provider", "provider_kind", "provider_model", "request_id", "status", "is_synthetic", "market", "language", "planned_samples", "successful_samples", "failed_samples", "total_cost_usd", "created_at", "completed_at")) for run in visibility_runs]
    sample_data = [_row_fields(sample, ("id", "run_id", "question_id", "status", "provider_request_id", "request_id", "model", "input_tokens", "output_tokens", "cost_usd", "estimated_cost_usd", "error_code", "completed_at", "created_at")) for sample in samples]

    blockers: list[dict[str, str]] = []
    def blocker(code: str, reason: str) -> None:
        blockers.append({"code": code, "reason": reason, "status": "blocked_external"})

    if not any((audit_data, task_data, revision_data, model_data, publication_data, visibility_data, sample_data)):
        blocker("no_runtime_rows", "no matching runtime rows were supplied; this report does not invent business activity")
    if not scope_bounded:
        blocker("scope_unbounded", "provide a workspace, request, task, change, or visibility run selector before reading runtime rows")
    for item in query_errors:
        blocker("database_query_failed", f"could not read {item['model']} ({item['error']}); migrate the database before collecting runtime evidence")
    if revisions and any(not revision.generation_id for revision in revisions):
        blocker("revision_generation_link_missing", "one or more revisions have no generation_id linking them to a model call")
    if model_calls and any(not call.generation_id and not call.sample_id for call in model_calls):
        blocker("model_call_execution_link_missing", "one or more model calls have neither generation_id nor sample_id")
    if visibility_runs and any(not run.request_id for run in visibility_runs):
        blocker("visibility_run_request_link_missing", "one or more visibility runs have no request_id")
    if samples and any(not sample.request_id and not next((run.request_id for run in visibility_runs if run.id == sample.run_id), None) for sample in samples):
        blocker("visibility_sample_request_link_missing", "one or more visibility samples have no request or run correlation ID")
    if audits and any(not event.run_id for event in audits):
        blocker("audit_request_link_missing", "one or more audit events have no run_id/request correlation")

    verification = (
        "runtime_observed"
        if scope_bounded
        and any((audit_data, task_data, revision_data, model_data, publication_data, visibility_data, sample_data))
        and not blockers
        else "blocked_external"
    )
    report: dict[str, Any] = {
        "schema_version": CORRELATION_REPORT_SCHEMA,
        "task_id": "T16",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_commit": _commit_sha(repo_root),
        "verification": verification,
        "business_data_claim": False,
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "database_engine": getattr(getattr(db, "bind", None), "dialect", None).name if getattr(getattr(db, "bind", None), "dialect", None) else None,
        },
        "command": list(command or ()),
        "scope": {"workspace_id": workspace_id, "request_id": request_id, "task_id": task_id, "change_request_id": change_request_id, "visibility_run_id": visibility_run_id},
        "correlation": {
            "audit_events": audit_data,
            "content_tasks": task_data,
            "content_items": item_data,
            "revisions": revision_data,
            "model_calls": model_data,
            "publication_attempts": publication_data,
            "visibility_runs": visibility_data,
            "visibility_samples": sample_data,
        },
        "coverage": {
            "request_to_audit": bool(audit_data and any(item["run_id"] for item in audit_data)),
            "task_to_revision": bool(item_data and revision_data and any(item["change_request_id"] == revision["change_request_id"] for item in item_data for revision in revision_data)),
            "revision_to_model_call": bool(revision_data and model_data and any(item["generation_id"] and item["generation_id"] == call["generation_id"] for item in revision_data for call in model_data)),
            "revision_to_publication": bool(revision_data and publication_data and any(item["id"] == publication["revision_id"] for item in revision_data for publication in publication_data)),
            "request_to_visibility_sample": bool(visibility_data and sample_data and any(item["request_id"] or next((run["request_id"] for run in visibility_data if run["id"] == item["run_id"]), None) for item in sample_data)),
        },
        "artifacts": _artifact_metadata(list(artifact_paths)),
        "blockers": blockers,
    }
    report["report_sha256"] = _report_digest(report)
    return report


async def request_context_middleware(request: Request, call_next) -> Response:
    """Attach a request ID and log bounded request metadata and duration."""

    import logging as _logging

    value = request_id(request.headers.get(REQUEST_ID_HEADER))
    token = _REQUEST_ID.set(value)
    started = time.perf_counter()
    response: Response | None = None
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        log_event(
            _logging.getLogger("trade_visibility.http"),
            "http_request_error",
            method=request.method,
            path=request.url.path,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            error_type=type(exc).__name__,
        )
        raise
    finally:
        if response is not None:
            response.headers[REQUEST_ID_HEADER] = value
            log_event(
                _logging.getLogger("trade_visibility.http"),
                "http_request_complete",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        _REQUEST_ID.reset(token)
