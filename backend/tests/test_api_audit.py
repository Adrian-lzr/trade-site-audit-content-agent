from __future__ import annotations

from hashlib import sha256
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from sqlalchemy import select, update
from fastapi.testclient import TestClient

from backend.app import app


@contextmanager
def fixture_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<!doctype html><html><head></head><body><h1>Missing title fixture</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_frontend_contract_and_missing_title_rule_persists_to_pages():
    with fixture_server() as origin, TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["fixture"] is False
        assert health.json()["fixture_available"] is True
        direct_health = client.get("/health")
        assert direct_health.status_code == 200
        assert direct_health.json()["fixture"] is False
        assert direct_health.json()["fixture_available"] is True

        created = client.post("/api/sites", json={
            "workspace_id": "demo-workspace",
            "name": "Fixture site",
            "origin": origin,
            "is_synthetic": True,
            "allowed_paths": ["/missing-title.html"],
        })
        assert created.status_code == 201, created.text
        site = created.json()
        assert isinstance(site["id"], str)
        assert site["origin"] == origin
        assert site["is_synthetic"] is True
        assert site["workspace_id"] == "demo-workspace"
        assert client.get("/api/sites").json()[0]["id"] == site["id"]

        run = client.post(f"/api/sites/{site['id']}/audit-runs", json={})
        assert run.status_code == 202, run.text
        assert isinstance(run.json()["id"], str)
        job_id = run.json()["id"]
        assert run.json()["status"] == "queued"
        from backend.worker import JobWorker

        assert JobWorker().run_once() is True
        completed = client.get(f"/api/audit-runs/{job_id}")
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "succeeded"

        pages = client.get(f"/api/sites/{site['id']}/pages")
        assert pages.status_code == 200
        assert len(pages.json()) == 1
        page = pages.json()[0]
        assert page["title"] is None
        assert page["is_synthetic"] is True
        assert page["finding_count"] == 1
        assert page["rule_count"] == 12
        assert page["rule_problem_count"] == 2
        assert page["rule_review_count"] == 1
        assert page["rule_unknown_count"] == 0
        expected_html = "<!doctype html><html><head></head><body><h1>Missing title fixture</h1></body></html>"
        assert page["content_hash"] == sha256(expected_html.encode("utf-8")).hexdigest()
        snapshot = client.get(f"/api/snapshots/{page['id']}")
        assert snapshot.status_code == 200
        assert snapshot.json()["findings"][0]["code"] == "TITLE_MISSING"
        assert snapshot.json()["content_hash"] == page["content_hash"]
        assert snapshot.json()["is_synthetic"] is True
        assert snapshot.json()["rule_set_version"] == "1.0.0"
        assert len(snapshot.json()["rule_results"]) == 12
        assert client.get("/api/audit-runs/not-a-number").status_code == 404


def test_site_registration_rejects_encoded_traversal_allowed_path():
    with fixture_server() as origin, TestClient(app) as client:
        response = client.post("/api/sites", json={
            "workspace_id": "demo-workspace",
            "name": "Invalid path site",
            "origin": origin,
            "allowed_paths": ["/allowed/%252e%252e/admin"],
        })
        assert response.status_code == 422


def test_worker_recovery_leaves_unexpired_lease_running_and_requeues_expired_lease():
    from backend.database import SessionLocal
    from backend.models import Job, Site, Workspace, utcnow
    from backend.worker import JobWorker

    with SessionLocal() as db:
        workspace = Workspace(name="Workspace", external_id="restart-test")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="Site", base_url="http://127.0.0.1/", allowed_paths='["/"]')
        db.add(site)
        db.flush()
        queued = Job(site_id=site.id, status="queued")
        active = Job(site_id=site.id, status="running", lease_token="active-owner", lease_expires_at=utcnow() + timedelta(minutes=3))
        expired = Job(site_id=site.id, status="running", lease_token="expired-owner", lease_expires_at=utcnow() - timedelta(seconds=1))
        db.add_all([queued, active, expired])
        db.commit()
        queued_id, active_id, expired_id = queued.id, active.id, expired.id

    recovered = JobWorker().recover_interrupted()
    assert recovered == 1
    with SessionLocal() as db:
        assert db.get(Job, queued_id).status == "queued"
        assert db.get(Job, active_id).status == "running"
        assert db.get(Job, active_id).lease_token == "active-owner"
        assert db.get(Job, expired_id).status == "queued"
        assert db.get(Job, expired_id).lease_token is None


def test_stale_lease_owner_cannot_commit_snapshot_after_takeover():
    from backend.audit import JobLeaseLost, claim_job, execute_claimed_job
    from backend.crawler import CrawlResult
    from backend.database import SessionLocal
    from backend.models import Job, PageSnapshot, Site, Workspace, utcnow

    with SessionLocal() as db:
        workspace = Workspace(name="Workspace", external_id="lease-test")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="Site", base_url="http://127.0.0.1/", allowed_paths='["/"]', is_synthetic=True)
        db.add(site)
        db.flush()
        job = Job(site_id=site.id, status="queued")
        db.add(job)
        db.commit()
        job_id = job.id

    with SessionLocal() as db:
        old_token = claim_job(db, job_id)

        class LeaseTakenOver:
            def fetch(self, url, *, base_url, allowed_paths, allow_loopback):
                assert allow_loopback is True
                db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(lease_token="new-owner", lease_expires_at=utcnow() + timedelta(minutes=3))
                )
                db.commit()
                return CrawlResult(url=url, status_code=200, headers={"content-type": "text/html"}, content="<html><title>ok</title></html>", title="ok")

        with pytest.raises(JobLeaseLost):
            execute_claimed_job(db, job_id, old_token, crawler=LeaseTakenOver())

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        assert job.status == "running"
        assert job.lease_token == "new-owner"
        assert db.scalar(select(PageSnapshot.id).where(PageSnapshot.job_id == job_id)) is None


def test_page_upsert_returns_existing_page_when_unique_key_already_exists():
    from backend.audit import _get_or_create_page
    from backend.database import SessionLocal
    from backend.models import Page, Site, Workspace

    with SessionLocal() as db:
        workspace = Workspace(name="Workspace", external_id="page-upsert-test")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="Site", base_url="https://example.com", allowed_paths='["/"]')
        db.add(site)
        db.commit()
        site_id = site.id

    with SessionLocal() as db:
        first = _get_or_create_page(db, site_id, "https://example.com/")
        first_id = first.id
        db.commit()

    with SessionLocal() as db:
        second = _get_or_create_page(db, site_id, "https://example.com/")
        assert second.id == first_id
        db.commit()
        assert db.query(Page).filter(Page.site_id == site_id, Page.canonical_url == "https://example.com/").count() == 1


def test_synthetic_registration_requires_literal_loopback_origin_and_root_url():
    with TestClient(app) as client:
        base_payload = {"workspace_id": "demo-workspace", "name": "Invalid synthetic", "is_synthetic": True}
        non_loopback = client.post("/api/sites", json={**base_payload, "origin": "http://example.com/"})
        assert non_loopback.status_code == 422

        with fixture_server() as origin:
            unmarked_loopback = client.post("/api/sites", json={**base_payload, "name": "Unmarked", "origin": origin, "is_synthetic": False})
            assert unmarked_loopback.status_code == 422
            nested_origin = client.post("/api/sites", json={**base_payload, "origin": f"{origin}/nested"})
            assert nested_origin.status_code == 422
