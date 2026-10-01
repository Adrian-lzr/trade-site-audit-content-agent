from __future__ import annotations

from hashlib import sha256
from contextlib import contextmanager, nullcontext
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from sqlalchemy import select, update
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.app import app
from backend.database import SessionLocal
from backend.models import Job, Page, PageSnapshot, utcnow


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


def test_frontend_contract_and_missing_title_rule_persists_to_pages(monkeypatch):
    monkeypatch.setattr(app_module, "_fixture_available", lambda: True)
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
        assert client.get(f"/api/sites?workspace_id={site['workspace_id']}").json()[0]["id"] == site["id"]

        run = client.post(f"/api/sites/{site['id']}/audit-runs?workspace_id={site['workspace_id']}", json={})
        assert run.status_code == 202, run.text
        assert isinstance(run.json()["id"], str)
        job_id = run.json()["id"]
        assert run.json()["status"] == "queued"
        from backend.worker import JobWorker

        assert JobWorker().run_once() is True
        completed = client.get(f"/api/audit-runs/{job_id}?workspace_id={site['workspace_id']}")
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "succeeded"

        pages = client.get(f"/api/sites/{site['id']}/pages?workspace_id={site['workspace_id']}")
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

        snapshot = client.get(f"/api/snapshots/{page['id']}?workspace_id={site['workspace_id']}")
        assert snapshot.status_code == 200
        assert snapshot.json()["findings"][0]["code"] == "TITLE_MISSING"
        assert snapshot.json()["content_hash"] == page["content_hash"]
        assert snapshot.json()["is_synthetic"] is True
        assert snapshot.json()["rule_set_version"] == "1.0.0"
        assert len(snapshot.json()["rule_results"]) == 12
        assert client.get(f"/api/audit-runs/not-a-number?workspace_id={site['workspace_id']}").status_code == 404


def test_workspace_scoped_audit_routes_enforce_ownership_and_report_actual_page_counts():
    with TestClient(app) as client:
        owner = client.post("/api/workspaces", json={"name": "Audit owner"}).json()
        other = client.post("/api/workspaces", json={"name": "Audit other"}).json()
        site_response = client.post(
            "/api/sites",
            json={
                "workspace_id": owner["id"],
                "name": "Scoped audit site",
                "origin": "https://scoped-audit.example.test",
                "audit_page_limit": 5,
            },
        )
        assert site_response.status_code == 201, site_response.text
        site_id = int(site_response.json()["id"])

        wrong_site = client.post(
            f"/api/workspaces/{other['id']}/sites/{site_id}/audit-runs",
            json={},
        )
        assert wrong_site.status_code == 404
        created = client.post(
            f"/api/workspaces/{owner['id']}/sites/{site_id}/audit-runs",
            json={},
        )
        assert created.status_code == 202, created.text
        job_id = int(created.json()["id"])
        assert created.json()["pages_total"] == 5
        assert created.json()["pages_completed"] == 0

        assert client.get(f"/api/workspaces/{other['id']}/audit-runs/{job_id}").status_code == 404
        assert client.get(f"/api/workspaces/{owner['id']}/audit-runs/{job_id}").status_code == 200
        assert client.get(f"/api/workspaces/{other['id']}/sites/{site_id}/jobs").status_code == 404
        assert client.get(f"/api/workspaces/{owner['id']}/sites/{site_id}/jobs").status_code == 200

        with SessionLocal() as db:
            page_one = Page(site_id=site_id, canonical_url="https://scoped-audit.example.test/one")
            page_two = Page(site_id=site_id, canonical_url="https://scoped-audit.example.test/two")
            db.add_all([page_one, page_two])
            db.flush()
            snapshot_one = PageSnapshot(
                page_id=page_one.id,
                job_id=job_id,
                url=page_one.canonical_url,
                status_code=200,
                content_hash="a" * 64,
                fetched_at=utcnow(),
            )
            snapshot_two = PageSnapshot(
                page_id=page_two.id,
                job_id=job_id,
                url=page_two.canonical_url,
                status_code=200,
                content_hash="b" * 64,
                fetched_at=utcnow(),
            )
            db.add_all([snapshot_one, snapshot_two])
            db.get(Job, job_id).status = "succeeded"
            db.commit()
            snapshot_ids = (snapshot_one.id, snapshot_two.id)

        completed = client.get(f"/api/workspaces/{owner['id']}/audit-runs/{job_id}")
        assert completed.status_code == 200, completed.text
        assert completed.json()["pages_total"] == 2
        assert completed.json()["pages_completed"] == 2
        assert completed.json()["progress"] == 100
        for snapshot_id in snapshot_ids:
            assert client.get(f"/api/workspaces/{other['id']}/snapshots/{snapshot_id}").status_code == 404

        snapshots = client.get(f"/api/workspaces/{owner['id']}/sites/{site_id}/snapshots")
        assert snapshots.status_code == 200
        assert len(snapshots.json()) == 2
        pages = client.get(f"/api/workspaces/{owner['id']}/sites/{site_id}/pages")
        assert pages.status_code == 200
        assert {entry["page_id"] for entry in pages.json()} == {page_one.id, page_two.id}
        assert client.get(f"/api/workspaces/{other['id']}/sites/{site_id}/snapshots").status_code == 404
        assert client.get(f"/api/workspaces/{other['id']}/sites/{site_id}/pages").status_code == 404


def test_compatibility_routes_require_explicit_workspace_scope():
    """Legacy resource paths must not fall back to an unscoped database lookup."""
    with TestClient(app) as client:
        missing_scope_reads = [
            "/api/sites",
            "/api/sites/1",
            "/api/jobs/1",
            "/api/snapshots/1",
            "/api/sites/1/pages",
            "/api/sites/1/jobs",
            "/api/sites/1/snapshots",
            "/api/sites/1/model",
            "/api/sites/1/monitor",
            "/api/changes/1",
        ]
        for path in missing_scope_reads:
            assert client.get(path).status_code == 422, path

        assert client.post("/api/sites/1/audits").status_code == 422
        assert client.post("/api/sites/1/changes", json={}).status_code == 422
        assert client.get("/api/facts/1").status_code == 422
        assert client.post("/api/facts/1/confirm", json={"reviewer": "test"}).status_code == 422
        assert client.post("/api/facts", json={
            "subject": "s",
            "predicate": "p",
            "value": "v",
            "source_id": "source",
            "source_locator": "locator",
        }).status_code == 422

        owner = client.post("/api/workspaces", json={"name": "scope owner"}).json()
        other = client.post("/api/workspaces", json={"name": "scope other"}).json()
        site = client.post("/api/sites", json={
            "workspace_id": owner["id"],
            "name": "scoped site",
            "origin": "https://scope.example.test",
        }).json()
        fact = client.post("/api/facts", json={
            "workspace_id": owner["id"],
            "subject": "s",
            "predicate": "p",
            "value": "v",
            "source_id": "source",
            "source_locator": "locator",
        }).json()
        change = client.post(f"/api/workspaces/{owner['id']}/sites/{site['id']}/changes", json={
            "workspace_id": owner["id"],
            "field_diff": {"title": "updated"},
        }).json()
        assert client.get(f"/api/facts/{fact['id']}?workspace_id={other['id']}").status_code == 404
        assert client.get(f"/api/changes/{change['id']}?workspace_id={other['id']}").status_code == 404
        assert client.post(
            f"/api/changes/{change['id']}/submit-approval?workspace_id={other['id']}",
            json={"expected_version": 1},
        ).status_code == 404


def test_fixture_health_probe_reports_listener_state(monkeypatch):
    monkeypatch.setattr(app_module.socket, "create_connection", lambda *_args, **_kwargs: nullcontext())
    assert app_module._fixture_available() is True

    def refused(*_args, **_kwargs):
        raise ConnectionRefusedError

    monkeypatch.setattr(app_module.socket, "create_connection", refused)
    assert app_module._fixture_available() is False
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.json()["status"] == "ok"
        assert health.json()["fixture_available"] is False


def test_knowledge_entries_are_curated_and_keep_source_boundaries():
    with TestClient(app) as client:
        response = client.get("/api/knowledge")
        assert response.status_code == 200
        entries = response.json()
        assert len(entries) == 29
        assert len({entry["id"] for entry in entries}) == len(entries)
        assert {
            "google-seo-starter",
            "google-ai-features",
            "product-structured-data",
            "w3c-prov-o",
            "ftc-ad-substantiation",
            "rfq-page-content-checklist",
            "product-eligibility-boundary",
            "technical-signal-interpretation",
            "internationalized-b2b-pages",
            "accessibility-content-usability",
            "source-freshness-fact-separation",
            "google-search-essentials",
            "google-search-console-observation",
            "google-structured-data-policies",
            "ftc-green-guides",
            "ftc-endorsements-reviews",
            "eu-access2markets",
            "usitc-hts",
            "uk-trade-tariff",
            "wto-tariff-data",
            "icc-incoterms-2020",
            "google-title-links",
            "google-meta-descriptions",
            "valve-selection-input-checklist",
            "product-document-applicability-checklist",
            "valve-actuator-input-checklist",
            "valve-test-record-applicability-checklist",
            "valve-drawing-installation-maintenance-checklist",
            "eu-pressure-equipment-directive",
        }.issubset({entry["id"] for entry in entries})
        assert all(not entry["source_url"] or entry["source_url"].startswith("https://") for entry in entries)
        assert all(entry["source_kind"] == "internal_heuristic" for entry in entries if not entry["source_url"])
        assert {entry["accessed_at"] for entry in entries} == {"2026-09-29", "2026-09-30"}
        assert all(entry["scope"].strip() for entry in entries)
        assert all(entry["entry_type"] == "external_guidance" for entry in entries)
        assert all(entry["claim_type"].strip() for entry in entries)
        assert all(entry["market_scope"].strip() for entry in entries)
        assert all(entry["valid_until"] == "revalidate-before-use" for entry in entries)
        assert all(entry["source_kind"] for entry in entries)
        assert all(entry["topic_tags"] for entry in entries)
        assert all(entry["last_verified_at"] == entry["accessed_at"] for entry in entries)
        assert "不保证" in entries[0]["scope"]
        assert "不等于排名提升" in entries[2]["scope"]
        assert "不证明" in entries[3]["scope"]


def test_knowledge_filters_match_market_and_claim_type_without_short_substring_false_positives():
    with TestClient(app) as client:
        us = client.get("/api/knowledge", params={"market": "US"})
        assert us.status_code == 200
        us_ids = {entry["id"] for entry in us.json()}
        assert {"ftc-green-guides", "ftc-endorsements-reviews", "usitc-hts"}.issubset(us_ids)
        assert "eu-access2markets" not in us_ids
        assert "uk-trade-tariff" not in us_ids

        germany = client.get("/api/knowledge", params={"market": "Germany"})
        assert "eu-access2markets" in {entry["id"] for entry in germany.json()}

        policy = client.get("/api/knowledge", params={"claim_type": "tariff_classification_boundary"})
        assert [entry["id"] for entry in policy.json()] == ["usitc-hts"]


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
