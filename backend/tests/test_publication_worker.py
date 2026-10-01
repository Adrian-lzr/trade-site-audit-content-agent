from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import app
from backend.database import SessionLocal
from backend.models import ChangeRequest, OutboxEvent, Page, PageSnapshot, PublicationAttempt, Site, Workspace
from backend.publication_worker import PublicationWorker
from backend.worker import JobWorker


def _approved_change(name: str = "publication") -> tuple[int, int, dict]:
    with SessionLocal() as db:
        workspace = Workspace(name=f"{name} workspace")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name=name, base_url="https://example.test", allowed_paths='["/"]')
        db.add(site)
        db.flush()
        page = Page(site_id=site.id, canonical_url="https://example.test/")
        db.add(page)
        db.flush()
        content = "<html><title>Base</title></html>"
        snapshot = PageSnapshot(
            page_id=page.id,
            url=page.canonical_url,
            status_code=200,
            title="Base",
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            headers_json="{}",
        )
        db.add(snapshot)
        db.commit()
        workspace_id, site_id, snapshot_id, snapshot_hash = workspace.id, site.id, snapshot.id, snapshot.content_hash

    with TestClient(app) as client:
        created = client.post(
            f"/api/workspaces/{workspace_id}/sites/{site_id}/changes",
            json={
                "workspace_id": workspace_id,
                "site_id": site_id,
                "base_snapshot_id": snapshot_id,
                "base_content_hash": snapshot_hash,
                "field_diff": {"title": "Updated title"},
                "fact_versions": [],
            },
        )
        assert created.status_code == 201, created.text
        change = created.json()
        assert client.post(f"/api/changes/{change['id']}/submit-approval?workspace_id={workspace_id}", json={"expected_version": 1}).status_code == 200
        approved = client.post(
            f"/api/changes/{change['id']}/approval?workspace_id={workspace_id}",
            json={
                "reviewer": "alice",
                "decision": "approved",
                "revision_id": change["revision"]["id"],
                "revision_hash": change["revision"]["content_hash"],
                "expected_version": 1,
            },
        )
        assert approved.status_code == 200, approved.text
        published = client.post(f"/api/changes/{change['id']}/publish?workspace_id={workspace_id}", json={"expected_version": 1})
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "queued"
        return workspace_id, site_id, published.json()


def test_publication_worker_marks_unconfigured_without_external_write(monkeypatch):
    monkeypatch.delenv("GIT_PUBLISH_REPOSITORY", raising=False)
    workspace_id, _, publication = _approved_change("unconfigured")
    assert JobWorker().run_once() is True
    with SessionLocal() as db:
        attempt = db.get(PublicationAttempt, publication["id"])
        assert attempt is not None
        assert attempt.status == "not_configured"
        event = select_event(db, publication["change_request_id"])
        assert event is not None and event.published_at is not None
        assert db.get(ChangeRequest, publication["change_request_id"]).state == "publishing"


def select_event(db, change_id: int):
    return db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == str(change_id), OutboxEvent.event_type == "change.publish_requested").one()


def select_rollback_event(db, change_id: int):
    return db.query(OutboxEvent).filter(OutboxEvent.aggregate_id == str(change_id), OutboxEvent.event_type == "change.rollback_requested").one()


def test_publication_worker_creates_idempotent_isolated_git_commit(tmp_path: Path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.setenv("GIT_PUBLISH_REPOSITORY", str(tmp_path))
    workspace_id, _, publication = _approved_change("git")
    assert JobWorker().run_once() is True
    with SessionLocal() as db:
        attempt = db.get(PublicationAttempt, publication["id"])
        assert attempt is not None and attempt.status == "submitted"
        assert attempt.branch and attempt.commit_sha
        commit_sha = attempt.commit_sha
        assert select_event(db, publication["change_request_id"]).published_at is not None
    with TestClient(app) as client:
        deployed = client.post(
            f"/api/publication-attempts/{publication['id']}/deployment?workspace_id={workspace_id}",
            json={"status": "deployed", "commit_sha": commit_sha, "deployment_id": "local-deploy-1"},
        )
        assert deployed.status_code == 200, deployed.text
        assert deployed.json()["deployment_status"] == "deployed"
        verified = client.post(f"/api/publication-attempts/{publication['id']}/verify?workspace_id={workspace_id}")
        assert verified.status_code == 200, verified.text
        assert verified.json()["status"] == "verified"
        assert verified.json()["deployment_status"] == "verified"
    file_path = f".trade-visibility/changes/{publication['change_request_id']}/revision-1.json"
    listed = subprocess.run(["git", "cat-file", "-e", f"{commit_sha}:{file_path}"], cwd=tmp_path, check=False)
    assert listed.returncode == 0
    assert PublicationWorker().run_once() is False
    assert workspace_id > 0


def test_publication_worker_creates_guarded_revert_and_closes_rollback(tmp_path: Path, monkeypatch):
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.setenv("GIT_PUBLISH_REPOSITORY", str(tmp_path))
    workspace_id, _, publication = _approved_change("rollback")
    assert JobWorker().run_once() is True

    with SessionLocal() as db:
        source = db.get(PublicationAttempt, publication["id"])
        assert source is not None and source.status == "submitted"
        source_branch = source.branch
        source_commit = source.commit_sha
    assert source_branch and source_commit
    subprocess.run(["git", "switch", "-q", source_branch], cwd=tmp_path, check=True)

    with TestClient(app) as client:
        deployed = client.post(
            f"/api/publication-attempts/{publication['id']}/deployment?workspace_id={workspace_id}",
            json={"status": "deployed", "commit_sha": source_commit, "deployment_id": "rollback-source"},
        )
        assert deployed.status_code == 200, deployed.text
        verified = client.post(f"/api/publication-attempts/{publication['id']}/verify?workspace_id={workspace_id}")
        assert verified.status_code == 200, verified.text
        rollback = client.post(
            f"/api/publication-attempts/{publication['id']}/rollback?workspace_id={workspace_id}",
            json={"expected_current_sha": source_commit, "reason": "Restore the previous page"},
        )
        assert rollback.status_code == 200, rollback.text
        rollback_payload = rollback.json()
        assert rollback_payload["status"] == "queued"
        assert rollback_payload["rollback_of_attempt_id"] == publication["id"]

    assert PublicationWorker().run_once() is True
    with SessionLocal() as db:
        attempt = db.get(PublicationAttempt, rollback_payload["id"])
        assert attempt is not None
        assert attempt.status == "submitted"
        assert attempt.branch and attempt.commit_sha
        event = select_rollback_event(db, publication["change_request_id"])
        assert event.published_at is not None
        source = db.get(PublicationAttempt, publication["id"])
        assert source is not None and source.deployment_status == "rollback_pending"
        rollback_commit = attempt.commit_sha

    assert rollback_commit and source_commit
    parent = subprocess.run(["git", "show", "-s", "--format=%P", rollback_commit], cwd=tmp_path, check=True, text=True, capture_output=True).stdout.strip()
    assert parent == source_commit

    with TestClient(app) as client:
        deployed = client.post(
            f"/api/publication-attempts/{rollback_payload['id']}/deployment?workspace_id={workspace_id}",
            json={"status": "deployed", "commit_sha": rollback_commit, "deployment_id": "rollback-deploy"},
        )
        assert deployed.status_code == 200, deployed.text
        verified = client.post(f"/api/publication-attempts/{rollback_payload['id']}/verify?workspace_id={workspace_id}")
        assert verified.status_code == 200, verified.text
        assert verified.json()["status"] == "verified"
        assert verified.json()["deployment_status"] == "verified"

    with SessionLocal() as db:
        source = db.get(PublicationAttempt, publication["id"])
        assert source is not None and source.status == "rolled_back"
        assert source.deployment_status == "rolled_back"
        assert db.get(PublicationAttempt, rollback_payload["id"]).status == "verified"


def test_publication_worker_fails_rollback_after_manual_source_commit(tmp_path: Path, monkeypatch):
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.setenv("GIT_PUBLISH_REPOSITORY", str(tmp_path))
    workspace_id, _, publication = _approved_change("rollback-conflict")
    assert JobWorker().run_once() is True
    with SessionLocal() as db:
        source = db.get(PublicationAttempt, publication["id"])
        assert source is not None and source.branch and source.commit_sha
        source_branch, source_commit = source.branch, source.commit_sha
    subprocess.run(["git", "switch", "-q", source_branch], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("manual change\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Manual", "-c", "user.email=manual@example.invalid", "commit", "-q", "-m", "manual edit"],
        cwd=tmp_path,
        check=True,
    )

    with TestClient(app) as client:
        assert client.post(
            f"/api/publication-attempts/{publication['id']}/deployment?workspace_id={workspace_id}",
            json={"status": "deployed", "commit_sha": source_commit, "deployment_id": "conflict-source"},
        ).status_code == 200
        assert client.post(f"/api/publication-attempts/{publication['id']}/verify?workspace_id={workspace_id}").status_code == 200
        rollback = client.post(
            f"/api/publication-attempts/{publication['id']}/rollback?workspace_id={workspace_id}",
            json={"expected_current_sha": source_commit, "reason": "Restore safely"},
        )
        assert rollback.status_code == 200, rollback.text
        rollback_id = rollback.json()["id"]

    assert PublicationWorker().run_once() is True
    with SessionLocal() as db:
        attempt = db.get(PublicationAttempt, rollback_id)
        assert attempt is not None and attempt.status == "failed"
        assert "expected" in (attempt.error or "")
        event = select_rollback_event(db, publication["change_request_id"])
        assert event.published_at is not None
