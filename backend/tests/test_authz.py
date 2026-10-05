from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import app
from backend.database import SessionLocal
from backend.models import AuditEvent, Membership, Site, Workspace


def test_session_and_workspace_listing_are_scoped_to_identity(monkeypatch):
    with TestClient(app) as client:
        owner = _workspace(client, "session owner", ("alice", "reviewer"))
        other = _workspace(client, "session other", ("bob", "viewer"))
        anonymous = client.get("/api/session")
        assert anonymous.status_code == 200, anonymous.text
        assert anonymous.json()["source"] == "anonymous_demo"
        assert [item["id"] for item in anonymous.json()["workspaces"]] == ["demo-workspace"]

        alice = client.get("/api/workspaces", headers={"X-Local-User": "alice"})
        assert alice.status_code == 200, alice.text
        assert [item["id"] for item in alice.json()] == [str(owner["id"])]
        assert alice.json()[0]["role"] == "reviewer"
        assert str(other["id"]) not in {item["id"] for item in alice.json()}

        monkeypatch.setenv("AUTH_MODE", "production")
        denied = client.get("/api/session", headers={"X-Local-User": "alice"})
        assert denied.status_code == 401, denied.text
        missing = client.get("/api/workspaces")
        assert missing.status_code == 401, missing.text


def test_workspace_creator_is_bound_as_admin_and_production_creation_fails_closed(monkeypatch):
    with TestClient(app) as client:
        created = client.post("/api/workspaces", json={"name": "creator workspace"}, headers={"X-Local-User": "creator"})
        assert created.status_code == 201, created.text
        workspace_id = created.json()["id"]

        with SessionLocal() as db:
            membership = db.query(Membership).filter(
                Membership.workspace_id == int(workspace_id), Membership.user_id == "creator"
            ).one()
            assert membership.role == "admin"

        visible = client.get("/api/workspaces", headers={"X-Local-User": "creator"})
        assert visible.status_code == 200, visible.text
        assert any(item["id"] == str(workspace_id) and item["role"] == "admin" for item in visible.json())

        monkeypatch.setenv("AUTH_MODE", "production")
        anonymous = client.post("/api/workspaces", json={"name": "anonymous production workspace"})
        assert anonymous.status_code == 401, anonymous.text
        forged = client.post(
            "/api/workspaces",
            json={"name": "forged production workspace"},
            headers={"X-Local-User": "creator"},
        )
        assert forged.status_code == 401, forged.text


def test_change_submission_audit_uses_the_verified_request_actor():
    with TestClient(app) as client:
        workspace_id, site_id = _change_workspace_for_authz("submit actor")
        created = client.post(
            f"/api/workspaces/{workspace_id}/sites/{site_id}/changes",
            json={"workspace_id": workspace_id, "site_id": site_id, "field_diff": {"title": "Updated"}},
        )
        assert created.status_code == 201, created.text
        change = created.json()
        submitted = client.post(
            f"/api/changes/{change['id']}/submit-approval?workspace_id={workspace_id}",
            headers={"X-Local-User": "operator", "X-Workspace-Id": str(workspace_id), "If-Match": '"1"'},
        )
        assert submitted.status_code == 200, submitted.text

        with SessionLocal() as db:
            event = db.query(AuditEvent).filter(
                AuditEvent.workspace_id == int(workspace_id),
                AuditEvent.action == "change.pending_approval",
                AuditEvent.target_id == str(change["id"]),
            ).one()
            assert event.actor == "operator"


def _change_workspace_for_authz(name: str) -> tuple[int, int]:
    workspace = Workspace(name=name)
    with SessionLocal() as db:
        db.add(workspace)
        db.flush()
        db.add(Membership(workspace_id=workspace.id, user_id="operator", role="operator"))
        site = Site(workspace_id=workspace.id, name=f"{name} site", base_url="https://submit-actor.example.test")
        db.add(site)
        db.commit()
        return workspace.id, site.id


def _workspace(client: TestClient, name: str, *members: tuple[str, str]) -> dict:
    response = client.post("/api/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    workspace = response.json()
    with SessionLocal() as db:
        db.add_all(
            [Membership(workspace_id=int(workspace["id"]), user_id=user_id, role=role) for user_id, role in members]
        )
        db.commit()
    return workspace


def _fact(client: TestClient, workspace_id: str) -> dict:
    response = client.post(
        "/api/facts",
        json={
            "workspace_id": workspace_id,
            "subject": "VX-21",
            "predicate": "material",
            "value": "steel",
            "source_id": "catalog",
            "source_locator": "page 1",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_local_identity_enforces_fact_review_roles_and_binds_reviewer():
    with TestClient(app) as client:
        workspace = _workspace(
            client,
            "fact authz",
            ("viewer", "viewer"),
            ("operator", "operator"),
            ("reviewer", "reviewer"),
            ("admin", "admin"),
        )
        fact = _fact(client, workspace["id"])
        path = f"/api/facts/{fact['id']}/confirm?workspace_id={workspace['id']}"
        body = {"reviewer": "spoofed", "expected_version": 1}

        for user_id in ("viewer", "operator"):
            denied = client.post(path, json=body, headers={"X-Local-User": user_id, "X-Workspace-Id": str(workspace["id"])})
            assert denied.status_code == 403, (user_id, denied.text)

        approved = client.post(
            path,
            json=body,
            headers={"X-Local-User": "reviewer", "X-Workspace-Id": str(workspace["id"])},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["reviewer"] == "reviewer"
        with SessionLocal() as db:
            event = db.query(AuditEvent).filter(
                AuditEvent.workspace_id == int(workspace["id"]),
                AuditEvent.action == "fact.confirmed",
                AuditEvent.target_id == str(fact["id"]),
            ).one()
            assert event.actor == "reviewer"


def test_local_identity_rejects_cross_workspace_audit_event_and_allows_viewer_reads():
    with TestClient(app) as client:
        owner = _workspace(client, "audit owner", ("owner", "operator"), ("reader", "viewer"))
        other = _workspace(client, "audit other", ("other", "viewer"))
        path = f"/api/workspaces/{owner['id']}/audit-events"
        payload = {
            "actor": "spoofed",
            "action": "test",
            "target_type": "workspace",
            "target_id": str(owner["id"]),
        }

        created = client.post(
            path,
            json=payload,
            headers={"X-Local-User": "owner", "X-Workspace-Id": str(owner["id"])},
        )
        assert created.status_code == 201, created.text
        assert created.json()["actor"] == "owner"

        viewer_append = client.post(
            path,
            json=payload,
            headers={"X-Local-User": "reader", "X-Workspace-Id": str(owner["id"])},
        )
        assert viewer_append.status_code == 403, viewer_append.text

        viewer_read = client.get(
            path,
            headers={"X-Local-User": "reader", "X-Workspace-Id": str(owner["id"])},
        )
        assert viewer_read.status_code == 200, viewer_read.text
        assert len(viewer_read.json()) == 1

        listed = client.get(
            path,
            headers={"X-Local-User": "other", "X-Workspace-Id": str(other["id"])},
        )
        assert listed.status_code == 403, listed.text

        read = client.get(
            f"/api/workspaces/{owner['id']}/audit-events",
            headers={"X-Local-User": "other", "X-Workspace-Id": str(owner["id"])},
        )
        assert read.status_code == 403, read.text


def test_change_approval_requires_reviewer_or_admin_and_binds_reviewer():
    with TestClient(app) as client:
        workspace = _workspace(client, "change authz", ("operator", "operator"), ("reviewer", "reviewer"))
        site = client.post(
            "/api/sites",
            json={"workspace_id": workspace["id"], "name": "authz site", "origin": "https://authz.example.test"},
        )
        assert site.status_code == 201, site.text
        site_id = site.json()["id"]
        change = client.post(
            f"/api/workspaces/{workspace['id']}/sites/{site_id}/changes",
            json={"workspace_id": workspace["id"], "field_diff": {"title": "Updated"}},
        )
        assert change.status_code == 201, change.text
        change_payload = change.json()
        submitted = client.post(
            f"/api/changes/{change_payload['id']}/submit-approval?workspace_id={workspace['id']}",
            json={"expected_version": 1},
        )
        assert submitted.status_code == 200, submitted.text
        revision = submitted.json()["revision"]
        approval = {
            "reviewer": "spoofed",
            "decision": "approved",
            "revision_id": revision["id"],
            "revision_hash": revision["content_hash"],
            "expected_version": submitted.json()["version"],
        }
        denied = client.post(
            f"/api/changes/{change_payload['id']}/approval?workspace_id={workspace['id']}",
            json=approval,
            headers={"X-Local-User": "operator", "X-Workspace-Id": str(workspace["id"])},
        )
        assert denied.status_code == 403, denied.text
        approved = client.post(
            f"/api/changes/{change_payload['id']}/approval?workspace_id={workspace['id']}",
            json=approval,
            headers={"X-Local-User": "reviewer", "X-Workspace-Id": str(workspace["id"])},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["approvals"][0]["reviewer"] == "reviewer"


def test_change_create_and_revision_audits_bind_the_verified_operator():
    with TestClient(app) as client:
        workspace_id, site_id = _change_workspace_for_authz("revision audit")
        headers = {"X-Local-User": "operator", "X-Workspace-Id": str(workspace_id)}
        created = client.post(
            f"/api/workspaces/{workspace_id}/sites/{site_id}/changes",
            json={"workspace_id": workspace_id, "site_id": site_id, "field_diff": {"title": "First"}},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        change = created.json()
        revision = change["revision"]
        revised = client.post(
            f"/api/changes/{change['id']}/revisions?workspace_id={workspace_id}",
            json={"expected_version": 1, "field_diff": {"title": "Second"}},
            headers=headers,
        )
        assert revised.status_code == 200, revised.text
        with SessionLocal() as db:
            events = db.query(AuditEvent).filter(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.action.in_(["change.revision_created", "change.created"]),
            ).order_by(AuditEvent.id).all()
            assert [(event.action, event.actor) for event in events] == [
                ("change.revision_created", "operator"),
                ("change.created", "operator"),
                ("change.revision_created", "operator"),
            ]


def test_missing_identity_is_explicit_only_when_local_auth_is_required(monkeypatch):
    with TestClient(app) as client:
        workspace = _workspace(client, "strict auth", ("reviewer", "reviewer"))
        fact = _fact(client, workspace["id"])
        path = f"/api/facts/{fact['id']}/confirm?workspace_id={workspace['id']}"
        monkeypatch.setenv("LOCAL_AUTH_MODE", "required")
        denied = client.post(path, json={"reviewer": "legacy-demo"})
        assert denied.status_code == 401, denied.text


def test_workspace_reads_and_writes_use_membership_roles_and_scope():
    with TestClient(app) as client:
        owner = _workspace(
            client,
            "route authz owner",
            ("viewer", "viewer"),
            ("operator", "operator"),
        )
        other = _workspace(client, "route authz other", ("other", "operator"))
        owner_headers = lambda user: {"X-Local-User": user, "X-Workspace-Id": str(owner["id"])}

        read_sites = client.get("/api/sites", params={"workspace_id": owner["id"]}, headers=owner_headers("viewer"))
        assert read_sites.status_code == 200, read_sites.text

        denied_registration = client.post(
            "/api/sites",
            json={"workspace_id": owner["id"], "name": "viewer cannot register", "origin": "https://viewer.example.test"},
            headers=owner_headers("viewer"),
        )
        assert denied_registration.status_code == 403, denied_registration.text

        registered = client.post(
            "/api/sites",
            json={"workspace_id": owner["id"], "name": "operator site", "origin": "https://operator.example.test"},
            headers=owner_headers("operator"),
        )
        assert registered.status_code == 201, registered.text
        site_id = registered.json()["id"]

        cross_scope = client.get(
            f"/api/sites/{site_id}",
            params={"workspace_id": other["id"]},
            headers={"X-Local-User": "other", "X-Workspace-Id": str(other["id"])},
        )
        assert cross_scope.status_code == 404, cross_scope.text

        denied_fact = client.post(
            "/api/facts",
            json={
                "workspace_id": owner["id"],
                "subject": "VX-22",
                "predicate": "material",
                "value": "steel",
                "source_id": "catalog",
                "source_locator": "page 1",
            },
            headers=owner_headers("viewer"),
        )
        assert denied_fact.status_code == 403, denied_fact.text

        imported = client.post(
            "/api/facts",
            json={
                "workspace_id": owner["id"],
                "subject": "VX-22",
                "predicate": "material",
                "value": "steel",
                "source_id": "catalog",
                "source_locator": "page 1",
            },
            headers=owner_headers("operator"),
        )
        assert imported.status_code == 201, imported.text

        denied_question_set = client.post(
            f"/api/workspaces/{owner['id']}/sites/{site_id}/query-sets",
            json={"name": "viewer query set", "questions": []},
            headers=owner_headers("viewer"),
        )
        assert denied_question_set.status_code == 403, denied_question_set.text

        question_set = client.post(
            f"/api/workspaces/{owner['id']}/sites/{site_id}/query-sets",
            json={"name": "operator query set", "questions": []},
            headers=owner_headers("operator"),
        )
        assert question_set.status_code == 201, question_set.text

        listed = client.get(
            f"/api/workspaces/{owner['id']}/sites/{site_id}/query-sets",
            headers=owner_headers("viewer"),
        )
        assert listed.status_code == 200, listed.text


def test_workspace_reads_require_identity_in_strict_mode(monkeypatch):
    with TestClient(app) as client:
        workspace = _workspace(client, "strict reads", ("reader", "viewer"))
        monkeypatch.setenv("LOCAL_AUTH_MODE", "strict")
        response = client.get("/api/sites", params={"workspace_id": workspace["id"]})
        assert response.status_code == 401, response.text
