from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from backend.database import SessionLocal
from backend.models import AuditEvent, Membership, Page, PageSnapshot, Site, Workspace


def test_membership_role_and_workspace_user_uniqueness():
    with SessionLocal() as db:
        workspace = Workspace(name="boundary workspace")
        db.add(workspace)
        db.flush()
        db.add(Membership(workspace_id=workspace.id, user_id="alice", role="reviewer"))
        db.commit()
        db.add(Membership(workspace_id=workspace.id, user_id="alice", role="viewer"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(Membership(workspace_id=workspace.id, user_id="bob", role="owner"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_audit_event_is_append_only_record_and_snapshot_metadata_round_trips():
    with SessionLocal() as db:
        workspace = Workspace(name="event workspace")
        db.add(workspace)
        db.flush()
        event = AuditEvent(
            workspace_id=workspace.id,
            actor="system:test",
            action="snapshot.created",
            target_type="page_snapshot",
            target_id="42",
            before_version_json="{}",
            after_version_json='{"content_hash":"abc"}',
            run_id="run-1",
        )
        db.add(event)
        site = Site(workspace_id=workspace.id, name="site", base_url="https://example.test")
        db.add(site)
        db.flush()
        page = Page(site_id=site.id, canonical_url="https://example.test/")
        db.add(page)
        db.flush()
        snapshot = PageSnapshot(
            page_id=page.id,
            url=page.canonical_url,
            status_code=200,
            content_hash="a" * 64,
            artifact_uri="artifact://snapshots/1.html",
            parser_version="parser-v2",
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        assert snapshot.artifact_uri == "artifact://snapshots/1.html"
        assert snapshot.parser_version == "parser-v2"
        assert db.query(AuditEvent).filter_by(workspace_id=workspace.id).count() == 1
