from __future__ import annotations

import builtins
import sys

from backend.database import SessionLocal
from backend.models import ChangeRequest, PublicationAttempt
from backend.worker import JobWorker
from backend.tests.test_publication_worker import _approved_change


def test_publication_progresses_when_optional_langgraph_dependency_is_missing(monkeypatch):
    """A missing content dependency must not block unrelated outbox work."""

    monkeypatch.delenv("GIT_PUBLISH_REPOSITORY", raising=False)
    workspace_id, _site_id, publication = _approved_change("missing-langgraph")
    real_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "langgraph" or name.startswith("langgraph."):
            raise ModuleNotFoundError("langgraph is intentionally unavailable", name="langgraph")
        return real_import(name, globals, locals, fromlist, level)

    sys.modules.pop("backend.content_worker", None)
    monkeypatch.setattr(builtins, "__import__", blocked_import)

    assert JobWorker().run_once() is True
    with SessionLocal() as db:
        attempt = db.get(PublicationAttempt, publication["id"])
        assert attempt is not None
        assert attempt.status == "not_configured"
        change = db.get(ChangeRequest, publication["change_request_id"])
        assert change is not None and change.state == "publishing"
    assert workspace_id > 0


def test_worker_rotates_queue_families_to_prevent_audit_starvation(monkeypatch):
    worker = JobWorker()
    seen: list[str] = []
    monkeypatch.setattr(worker, "recover_interrupted", lambda: 0)

    def run_kind(kind: str) -> bool:
        seen.append(kind)
        return True

    monkeypatch.setattr(worker, "_run_kind", run_kind)
    assert worker.run_once() is True
    assert worker.run_once() is True
    assert seen == ["audit", "content"]
