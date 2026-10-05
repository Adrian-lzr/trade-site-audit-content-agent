from __future__ import annotations

import hashlib
import json

from backend.database import SessionLocal
from backend.models import AuditEvent, Job, Site, Workspace
from backend.audit import claim_job


def test_audit_worker_claim_records_hashed_lease_and_executor_metadata():
    with SessionLocal() as db:
        workspace = Workspace(name="worker audit workspace")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="audit site", base_url="https://example.test")
        db.add(site)
        db.flush()
        job = Job(site_id=site.id)
        db.add(job)
        db.commit()
        token = claim_job(db, job.id)
        event = db.query(AuditEvent).filter(AuditEvent.action == "audit_job.claimed").one()
        after = json.loads(event.after_version_json)

    assert token not in event.after_version_json
    assert after["lease_id"] == hashlib.sha256(token.encode()).hexdigest()[:16]
    before = json.loads(event.before_version_json)
    assert before["executor"] == "worker:audit"
    assert after["task_id"] == job.id
    assert after["result"] == "running"
    assert event.actor == "worker:audit"


def test_worker_audit_error_stores_type_without_error_body():
    from backend.worker_audit import append_worker_audit

    with SessionLocal() as db:
        workspace = Workspace(name="worker error audit")
        db.add(workspace)
        db.commit()
        append_worker_audit(
            db,
            workspace_id=workspace.id,
            worker="visibility",
            action="visibility_run.completed",
            target_type="visibility_run",
            target_id=1,
            initiator="operator",
            lease_token="secret-token",
            attempt=2,
            result="failed",
            error_type="ProviderTimeout",
        )
        db.commit()
        event = db.query(AuditEvent).one()

    assert "secret-token" not in event.after_version_json
    assert "provider response contains secret" not in event.after_version_json
    assert json.loads(event.after_version_json)["error_type"] == "ProviderTimeout"
