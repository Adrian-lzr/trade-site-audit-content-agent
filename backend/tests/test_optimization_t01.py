from __future__ import annotations

import hashlib
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app import app
from backend.database import SessionLocal, engine
from backend.models import Fact, Page, PageSnapshot, Site, Workspace, utcnow
from backend.publication_worker import PublicationWorker
from backend.schemas import FactCreate


def _approved_publication_with_fact() -> tuple[int, dict[str, object]]:
    with SessionLocal() as db:
        workspace = Workspace(name="T01 publication workspace")
        db.add(workspace)
        db.flush()
        site = Site(workspace_id=workspace.id, name="T01 site", base_url="https://t01.example", allowed_paths='["/"]')
        db.add(site)
        db.flush()
        page = Page(site_id=site.id, canonical_url="https://t01.example/spec")
        db.add(page)
        db.flush()
        content = "<html><head><title>Base</title></head><body>Base</body></html>"
        snapshot = PageSnapshot(
            page_id=page.id,
            url=page.canonical_url,
            status_code=200,
            title="Base",
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            headers_json="{}",
        )
        fact = Fact(
            workspace_id=workspace.id,
            subject="Valve VX-21",
            predicate="minimum_order_quantity",
            value="20",
            unit="pieces",
            source_id="t01-source",
            source_locator="page 1, MOQ row",
            visibility="public",
            status="confirmed",
            valid_from=utcnow(),
        )
        db.add_all([snapshot, fact])
        db.commit()
        workspace_id = workspace.id
        site_id = site.id
        snapshot_id = snapshot.id
        snapshot_hash = snapshot.content_hash
        fact_id = fact.id
        series_id = fact.series_id

    with TestClient(app) as client:
        change_response = client.post(
            f"/api/workspaces/{workspace_id}/sites/{site_id}/changes",
            json={
                "workspace_id": workspace_id,
                "site_id": site_id,
                "base_snapshot_id": snapshot_id,
                "base_content_hash": snapshot_hash,
                "field_diff": {"body": "Minimum order: 20 pieces."},
                "fact_versions": [{"fact_id": fact_id, "series_id": series_id, "version": 1}],
            },
        )
        assert change_response.status_code == 201, change_response.text
        change = change_response.json()
        submitted = client.post(
            f"/api/changes/{change['id']}/submit-approval?workspace_id={workspace_id}",
            json={"expected_version": 1},
        )
        assert submitted.status_code == 200, submitted.text
        approved = client.post(
            f"/api/changes/{change['id']}/approval?workspace_id={workspace_id}",
            json={
                "reviewer": "t01-reviewer",
                "decision": "approved",
                "revision_id": change["revision"]["id"],
                "revision_hash": change["revision"]["content_hash"],
                "expected_version": 1,
            },
        )
        assert approved.status_code == 200, approved.text
        queued = client.post(
            f"/api/changes/{change['id']}/publish?workspace_id={workspace_id}",
            json={"expected_version": 1},
        )
        assert queued.status_code == 200, queued.text
        return workspace_id, queued.json()


def test_sqlite_foreign_keys_are_enabled_for_every_application_connection():
    with engine.connect() as connection:
        enabled = connection.scalar(text("PRAGMA foreign_keys"))
    assert enabled == 1


def test_fact_effective_times_require_explicit_timezone():
    with pytest.raises(ValidationError, match="timezone"):
        FactCreate(
            subject="Valve VX-21",
            predicate="minimum_order_quantity",
            value="20",
            unit="pieces",
            source_id="datasheet",
            source_locator="page 1",
            valid_from=datetime(2026, 10, 2),
        )


def test_sqlite_fact_bound_approval_reaches_publication_worker_without_timezone_error(monkeypatch):
    monkeypatch.delenv("GIT_PUBLISH_REPOSITORY", raising=False)
    _, attempt = _approved_publication_with_fact()

    assert PublicationWorker().run_once() is True

    with SessionLocal() as db:
        from backend.models import PublicationAttempt

        persisted = db.get(PublicationAttempt, int(attempt["id"]))
        assert persisted is not None
        assert persisted.status == "not_configured"
        assert "offset-naive" not in (persisted.error or "")


def test_fact_parent_cannot_cross_workspace():
    with SessionLocal() as db:
        first = Workspace(name="T01 parent workspace")
        second = Workspace(name="T01 child workspace")
        db.add_all([first, second])
        db.flush()
        parent = Fact(
            workspace_id=first.id,
            subject="Valve VX-21",
            predicate="minimum_order_quantity",
            value="20",
            source_id="source",
            source_locator="row 1",
            valid_from=utcnow(),
        )
        db.add(parent)
        db.flush()
        child = Fact(
            workspace_id=second.id,
            parent_id=parent.id,
            subject="Valve VX-21",
            predicate="minimum_order_quantity",
            value="30",
            source_id="source",
            source_locator="row 2",
            valid_from=utcnow(),
        )
        db.add(child)
        with pytest.raises(IntegrityError):
            db.commit()
