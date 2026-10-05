from __future__ import annotations

import os
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, create_engine, inspect, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
import pytest


def test_alembic_upgrade_head_creates_minimal_schema(monkeypatch):
    with tempfile.NamedTemporaryFile(prefix="site-audit-migration-", suffix=".sqlite", delete=False) as db_file:
        path = Path(db_file.name)
    try:
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
        config = Config("backend/alembic.ini")
        command.upgrade(config, "head")
        migration_engine = create_engine(f"sqlite:///{path.as_posix()}")
        inspector = inspect(migration_engine)
        assert {"workspaces", "sites", "pages", "page_snapshots", "jobs", "audit_findings", "facts"}.issubset(set(inspector.get_table_names()))
        assert "content_hash" in {column["name"] for column in inspector.get_columns("page_snapshots")}
        assert {"lease_token", "lease_expires_at"}.issubset({column["name"] for column in inspector.get_columns("jobs")})
        assert "is_synthetic" in {column["name"] for column in inspector.get_columns("sites")}
        assert "is_synthetic" in {column["name"] for column in inspector.get_columns("page_snapshots")}
        assert {"subject", "predicate", "value", "unit", "source_id", "source_locator", "visibility", "status", "version", "valid_from", "valid_until", "reviewer"}.issubset({column["name"] for column in inspector.get_columns("facts")})
        assert {"change_requests", "change_revisions", "change_approvals", "outbox_events", "publication_attempts"}.issubset(set(inspector.get_table_names()))
        assert {"idempotency_key", "lease_token", "lease_expires_at", "attempts", "last_error"}.issubset({column["name"] for column in inspector.get_columns("outbox_events")})
        publication_columns = {column["name"] for column in inspector.get_columns("publication_attempts")}
        assert {"idempotency_key", "target", "branch", "commit_sha", "external_id", "updated_at", "deployment_status", "deployment_id", "deployed_commit_sha", "deployed_at", "verified_at", "rollback_of_attempt_id", "expected_current_sha", "rollback_reason"}.issubset(publication_columns)
        rollback_foreign_keys = inspector.get_foreign_keys("publication_attempts")
        assert any(
            foreign_key.get("name") == "fk_publication_attempts_rollback_of"
            and foreign_key.get("referred_table") == "publication_attempts"
            and foreign_key.get("constrained_columns") == ["rollback_of_attempt_id"]
            for foreign_key in rollback_foreign_keys
        )
        visibility_run_columns = {column["name"] for column in inspector.get_columns("visibility_runs")}
        assert {
            "provider_config_version",
            "prompt_version",
            "pricing_basis_json",
            "request_id",
            "reserved_cost_usd",
        }.issubset(visibility_run_columns)
        visibility_sample_columns = {column["name"] for column in inspector.get_columns("visibility_samples")}
        assert {"request_id", "estimated_cost_usd"}.issubset(visibility_sample_columns)
        migration_engine.dispose()
    finally:
        os.environ.pop("DATABASE_URL", None)
        path.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("dialect", "expected_literals"),
    [
        (sqlite.dialect(), "cost_known IN (0, 1)"),
        (postgresql.dialect(), "cost_known IN (false, true)"),
    ],
)
def test_model_call_boolean_constraint_compiles_for_database_dialect(monkeypatch, dialect, expected_literals):
    """Compile the actual 0018 table definition using both SQL dialects."""
    import importlib

    migration = importlib.import_module("backend.migrations.versions.0018_model_call_budget_accounting")
    constraints = []

    def capture_table(_name, *elements, **_kwargs):
        constraints.extend(element for element in elements if isinstance(element, CheckConstraint))

    monkeypatch.setattr(migration.op, "create_table", capture_table)
    migration.upgrade()

    cost_constraint = next(item for item in constraints if item.name == "ck_model_call_cost_known")
    rendered = str(cost_constraint.sqltext.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
    assert rendered == expected_literals


def test_worker_lease_migration_requeues_legacy_running_jobs(monkeypatch):
    with tempfile.NamedTemporaryFile(prefix="site-audit-legacy-migration-", suffix=".sqlite", delete=False) as db_file:
        path = Path(db_file.name)
    try:
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
        config = Config("backend/alembic.ini")
        command.upgrade(config, "0001_initial")
        migration_engine = create_engine(f"sqlite:///{path.as_posix()}")
        with migration_engine.begin() as connection:
            connection.execute(text("INSERT INTO workspaces (id, name, created_at) VALUES (1, 'Legacy', '2026-09-28 00:00:00')"))
            connection.execute(text("INSERT INTO sites (id, workspace_id, name, base_url, allowed_paths, created_at) VALUES (1, 1, 'Legacy site', 'https://example.com', '[/]', '2026-09-28 00:00:00')"))
            connection.execute(text("INSERT INTO jobs (id, site_id, status, created_at, started_at) VALUES (1, 1, 'running', '2026-09-28 00:00:00', '2026-09-28 00:00:00')"))
        command.upgrade(config, "head")
        with migration_engine.connect() as connection:
            job = connection.execute(text("SELECT status, started_at, error FROM jobs WHERE id = 1")).one()
        assert job.status == "queued"
        assert job.started_at is None
        assert job.error == "requeued during worker lease migration"
        migration_engine.dispose()
    finally:
        os.environ.pop("DATABASE_URL", None)
        path.unlink(missing_ok=True)


def test_fact_visibility_migration_backfills_legacy_facts_as_internal(monkeypatch):
    with tempfile.NamedTemporaryFile(prefix="site-audit-fact-visibility-", suffix=".sqlite", delete=False) as db_file:
        path = Path(db_file.name)
    try:
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
        config = Config("backend/alembic.ini")
        command.upgrade(config, "0005_change_management")
        migration_engine = create_engine(f"sqlite:///{path.as_posix()}")
        with migration_engine.begin() as connection:
            connection.execute(text("INSERT INTO workspaces (id, name, created_at) VALUES (1, 'Legacy', '2026-09-28 00:00:00')"))
            connection.execute(text("INSERT INTO facts (id, workspace_id, series_id, subject, predicate, value, source_id, source_locator, valid_from, created_at) VALUES (1, 1, 'legacy-series', 'Legacy product', 'pressure', '16', 'legacy-source', 'https://example.test/spec', '2026-09-27 00:00:00', '2026-09-27 00:00:00')"))
        command.upgrade(config, "head")
        with migration_engine.begin() as connection:
            old_visibility = connection.execute(text("SELECT visibility FROM facts WHERE id = 1")).scalar_one()
            connection.execute(text("INSERT INTO facts (workspace_id, series_id, subject, predicate, value, source_id, source_locator, valid_from, created_at) VALUES (1, 'new-series', 'New product', 'pressure', '18', 'source', 'https://example.test/new', '2026-09-28 00:00:00', '2026-09-28 00:00:00')"))
            new_visibility = connection.execute(text("SELECT visibility FROM facts WHERE series_id = 'new-series'")).scalar_one()
        assert old_visibility == "internal_only"
        assert new_visibility == "internal_only"
        with pytest.raises(IntegrityError):
            with migration_engine.begin() as connection:
                connection.execute(text("UPDATE facts SET visibility = 'private' WHERE id = 1"))
        migration_engine.dispose()
    finally:
        os.environ.pop("DATABASE_URL", None)
        path.unlink(missing_ok=True)
