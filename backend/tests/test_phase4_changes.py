from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient

from backend.app import app
from backend.database import SessionLocal
from backend.models import ChangeApproval, ChangeRequest, Fact, OutboxEvent, Page, PageSnapshot, Site, Workspace, utcnow
from datetime import timedelta


def _site(name: str = "site") -> tuple[int, int]:
    with SessionLocal() as db:
        workspace = Workspace(name=f"{name} workspace")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name=name, base_url="https://example.test", allowed_paths='["/"]')
        db.add(site)
        db.commit()
        return workspace.id, site.id


def _change_payload(workspace_id: int, site_id: int, **extra):
    payload = {
        "workspace_id": workspace_id,
        "site_id": site_id,
        "field_diff": {"title": "Updated product"},
        "fact_versions": [],
    }
    payload.update(extra)
    return payload


def test_change_draft_submit_approval_and_idempotent_outbox():
    workspace_id, site_id = _site("approval")
    with TestClient(app) as client:
        created = client.post(f"/api/workspaces/{workspace_id}/sites/{site_id}/changes", json=_change_payload(workspace_id, site_id))
        assert created.status_code == 201, created.text
        change = created.json()
        assert change["state"] == "draft"
        revision_hash = change["revision"]["content_hash"]

        rejected_field = client.post(f"/api/workspaces/{workspace_id}/sites/{site_id}/changes", json=_change_payload(workspace_id, site_id, field_diff={"price": 10}))
        assert rejected_field.status_code == 422
        tampered_hash = client.post(
            f"/api/workspaces/{workspace_id}/sites/{site_id}/changes",
            json=_change_payload(workspace_id, site_id, content_hash="f" * 64),
        )
        assert tampered_hash.status_code == 409

        submitted = client.post(f"/api/changes/{change['id']}/submit-approval", headers={"If-Match": '"1"'})
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["state"] == "pending_approval"
        duplicate_submit = client.post(f"/api/changes/{change['id']}/submit-approval", json={"expected_version": 1})
        assert duplicate_submit.status_code == 200
        approved = client.post(
            f"/api/changes/{change['id']}/approval",
            headers={"If-Match": '"1"'},
            json={"reviewer": "alice", "decision": "approved", "revision_id": change["revision"]["id"], "revision_hash": revision_hash, "expected_version": 1},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["state"] == "approved"
        duplicate_approval = client.post(
            f"/api/changes/{change['id']}/approval",
            json={"reviewer": "alice", "decision": "approved", "revision_id": change["revision"]["id"], "revision_hash": revision_hash, "expected_version": 1},
        )
        assert duplicate_approval.status_code == 200

        with SessionLocal() as db:
            assert db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == str(change["id"])).count() == 2
            first = db.query(OutboxEvent).filter(OutboxEvent.event_type == "change.approved").one()
            assert first.idempotency_key.endswith(":approved")


def test_new_revision_invalidates_old_approval_and_requires_if_match():
    workspace_id, site_id = _site("revision")
    with TestClient(app) as client:
        change = client.post(f"/api/workspaces/{workspace_id}/sites/{site_id}/changes", json=_change_payload(workspace_id, site_id)).json()
        client.post(f"/api/changes/{change['id']}/submit-approval", json={"expected_version": 1})
        old_revision = change["revision"]
        assert client.post(f"/api/changes/{change['id']}/approval", json={"reviewer": "alice", "decision": "approved", "revision_id": old_revision["id"], "revision_hash": old_revision["content_hash"], "expected_version": 1}).status_code == 200
        revised = client.post(f"/api/changes/{change['id']}/revisions", headers={"If-Match": '"1"'}, json={"field_diff": {"body": "new"}, "expected_version": 1})
        assert revised.status_code == 200, revised.text
        assert revised.json()["state"] == "draft"
        assert revised.json()["approvals"]
        stale = client.post(f"/api/changes/{change['id']}/revisions", headers={"If-Match": '"1"'}, json={"field_diff": {"body": "another"}, "expected_version": 1})
        assert stale.status_code == 409
        assert client.post(f"/api/changes/{change['id']}/approval", json={"reviewer": "alice", "decision": "approved", "revision_id": old_revision["id"], "revision_hash": old_revision["content_hash"], "expected_version": 3}).status_code == 409


def test_change_workspace_isolation_snapshot_and_fact_validation():
    workspace_id, site_id = _site("owner")
    other_workspace_id, other_site_id = _site("other")
    with SessionLocal() as db:
        page = Page(site_id=site_id, canonical_url="https://example.test/")
        db.add(page)
        db.flush()
        content = "<html><title>base</title></html>"
        snapshot = PageSnapshot(page_id=page.id, url=page.canonical_url, status_code=200, title="base", content_hash=hashlib.sha256(content.encode()).hexdigest(), content=content, headers_json="{}")
        db.add(snapshot)
        fact = Fact(workspace_id=workspace_id, subject="x", predicate="y", value="z", source_id="s", source_locator="l", status="confirmed", visibility="public", valid_from=utcnow())
        db.add(fact)
        db.commit()
        snapshot_id, snapshot_hash, fact_id = snapshot.id, snapshot.content_hash, fact.id
        proposed = Fact(workspace_id=workspace_id, subject="p", predicate="q", value="r", source_id="s", source_locator="l", status="proposed", visibility="public", valid_from=utcnow())
        future = Fact(workspace_id=workspace_id, subject="f", predicate="q", value="r", source_id="s", source_locator="l", status="confirmed", visibility="public", valid_from=utcnow() + timedelta(days=1))
        expired = Fact(workspace_id=workspace_id, subject="e", predicate="q", value="r", source_id="s", source_locator="l", status="confirmed", visibility="public", valid_from=utcnow() - timedelta(days=2), valid_until=utcnow() - timedelta(days=1))
        internal = Fact(workspace_id=workspace_id, subject="i", predicate="q", value="r", source_id="s", source_locator="l", status="confirmed", visibility="internal_only", valid_from=utcnow())
        db.add_all([proposed, future, expired, internal])
        db.commit()
        proposed_id, future_id, expired_id, internal_id = proposed.id, future.id, expired.id, internal.id
    with TestClient(app) as client:
        cross = client.post(f"/api/workspaces/{other_workspace_id}/sites/{site_id}/changes", json=_change_payload(other_workspace_id, site_id))
        assert cross.status_code == 404
        bad_hash = client.post(f"/api/workspaces/{workspace_id}/sites/{site_id}/changes", json=_change_payload(workspace_id, site_id, base_snapshot_id=snapshot_id, base_content_hash="0" * 64))
        assert bad_hash.status_code == 409
        invalid_fact = client.post(f"/api/workspaces/{workspace_id}/sites/{site_id}/changes", json=_change_payload(workspace_id, site_id, fact_versions=[{"fact_id": fact_id, "version": 2}]))
        assert invalid_fact.status_code == 409
        for invalid_id in (proposed_id, future_id, expired_id, internal_id):
            invalid_state = client.post(f"/api/workspaces/{workspace_id}/sites/{site_id}/changes", json=_change_payload(workspace_id, site_id, fact_versions=[{"fact_id": invalid_id, "version": 1}]))
            assert invalid_state.status_code == 409
        valid = client.post(f"/api/workspaces/{workspace_id}/sites/{site_id}/changes", json=_change_payload(workspace_id, site_id, base_snapshot_id=snapshot_id, base_content_hash=snapshot_hash, fact_versions=[{"fact_id": fact_id, "version": 1}]))
        assert valid.status_code == 201, valid.text


def test_publish_rechecks_fact_public_visibility():
    workspace_id, site_id = _site("publish-public-fact")
    with SessionLocal() as db:
        page = Page(site_id=site_id, canonical_url="https://example.test/")
        db.add(page)
        db.flush()
        content = "<html><title>base</title></html>"
        snapshot = PageSnapshot(page_id=page.id, url=page.canonical_url, status_code=200, title="base", content_hash=hashlib.sha256(content.encode()).hexdigest(), content=content, headers_json="{}")
        fact = Fact(workspace_id=workspace_id, subject="x", predicate="y", value="z", source_id="s", source_locator="l", status="confirmed", visibility="public", valid_from=utcnow())
        db.add_all([snapshot, fact])
        db.commit()
        snapshot_id, snapshot_hash, fact_id = snapshot.id, snapshot.content_hash, fact.id

    with TestClient(app) as client:
        change_response = client.post(
            f"/api/workspaces/{workspace_id}/sites/{site_id}/changes",
            json=_change_payload(
                workspace_id,
                site_id,
                base_snapshot_id=snapshot_id,
                base_content_hash=snapshot_hash,
                fact_versions=[{"fact_id": fact_id, "version": 1}],
            ),
        )
        assert change_response.status_code == 201, change_response.text
        change = change_response.json()
        assert client.post(f"/api/changes/{change['id']}/submit-approval", json={"expected_version": 1}).status_code == 200
        approved = client.post(
            f"/api/changes/{change['id']}/approval",
            json={"reviewer": "alice", "decision": "approved", "revision_id": change["revision"]["id"], "revision_hash": change["revision"]["content_hash"], "expected_version": 1},
        )
        assert approved.status_code == 200, approved.text
        with SessionLocal() as db:
            db.get(Fact, fact_id).visibility = "internal_only"
            db.commit()
        response = client.post(f"/api/changes/{change['id']}/publish", json={"expected_version": 1})
        assert response.status_code == 409


def test_publish_requires_current_base_snapshot_binding():
    workspace_id, site_id = _site("publish")
    with TestClient(app) as client:
        change = client.post(f"/api/workspaces/{workspace_id}/sites/{site_id}/changes", json=_change_payload(workspace_id, site_id)).json()
        assert client.post(f"/api/changes/{change['id']}/submit-approval", json={"expected_version": 1}).status_code == 200
        assert client.post(
            f"/api/changes/{change['id']}/approval",
            json={"reviewer": "alice", "decision": "approved", "revision_id": change["revision"]["id"], "revision_hash": change["revision"]["content_hash"], "expected_version": 1},
        ).status_code == 200
        response = client.post(f"/api/changes/{change['id']}/publish", json={"expected_version": 1})
        assert response.status_code == 409
