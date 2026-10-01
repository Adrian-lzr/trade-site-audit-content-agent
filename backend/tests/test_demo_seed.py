from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from backend.models import (
    Fact,
    Page,
    PageSnapshot,
    ProcurementQuestion,
    ProcurementQuestionPageMapping,
    ProcurementQuestionSetVersion,
    Site,
    Workspace,
)
from scripts.seed_demo_data import SeedRefused, _upgrade_schema, seed_demo_data


def _db_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_seed_creates_twenty_frozen_questions_public_facts_and_reuses_rows(tmp_path: Path):
    database_url = _db_url(tmp_path / "demo.sqlite")

    first = seed_demo_data(database_url, synthetic=True)
    second = seed_demo_data(database_url, synthetic=True)

    assert first["dataset_version"] == "phase1-demo-seed-v1"
    assert first["seed_version"] == first["dataset_version"]
    assert first["workspace"]["external_id"] == "demo-workspace"
    assert first["site"]["is_synthetic"] is True
    assert first["site"]["origin"] == "http://127.0.0.1:8765"
    assert first["question_count"] == 20
    assert first["question_set_version"]["state"] == "frozen"
    assert first["question_set_version"]["version"] == 1
    assert len(first["fact_ids"]) >= 1
    assert all(item["synthetic"] for item in first["facts"])
    assert all(item["status"] == "confirmed" for item in first["facts"])
    assert all(item["visibility"] == "public" for item in first["facts"])
    assert all(item["source_locator"] and item["validity"]["valid_from"] for item in first["facts"])
    assert first["snapshot"]["page_count"] == 20
    assert first["source_metadata"]["network_access"] is False
    assert first["source_metadata"]["remote_writes"] is False

    assert second["created"] == {
        "workspace": False,
        "site": False,
        "pages": False,
        "snapshots": False,
        "facts": False,
        "question_set": False,
    }
    assert second["fact_ids"] == first["fact_ids"]
    assert second["page_ids"] == first["page_ids"]
    assert second["snapshot_ids"] == first["snapshot_ids"]
    assert second["question_set_version"]["id"] == first["question_set_version"]["id"]

    engine = create_engine(database_url)
    try:
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Workspace)) == 1
            assert db.scalar(select(func.count()).select_from(Site)) == 1
            assert db.scalar(select(func.count()).select_from(Page)) == 20
            assert db.scalar(select(func.count()).select_from(PageSnapshot)) == 20
            assert db.scalar(select(func.count()).select_from(Fact)) == len(first["fact_ids"])
            assert db.scalar(select(func.count()).select_from(ProcurementQuestion)) == 20
            assert db.scalar(select(func.count()).select_from(ProcurementQuestionPageMapping)) == 20
            version = db.scalar(select(ProcurementQuestionSetVersion))
            assert version is not None
            assert version.state == "frozen"
            assert len(version.questions) == 20
            assert all(question.page_mappings for question in version.questions)
    finally:
        engine.dispose()


def test_seed_requires_sqlite_and_explicit_synthetic_mode(tmp_path: Path):
    with pytest.raises(SeedRefused, match="synthetic mode is required"):
        seed_demo_data(_db_url(tmp_path / "demo.sqlite"))
    with pytest.raises(SeedRefused, match="non-SQLite"):
        seed_demo_data("postgresql+psycopg://user:password@localhost/demo", synthetic=True)


def test_seed_refuses_a_non_synthetic_target_site(tmp_path: Path):
    database_url = _db_url(tmp_path / "demo.sqlite")
    _upgrade_schema(database_url)
    engine = create_engine(database_url)
    try:
        with Session(engine) as db:
            workspace = Workspace(external_id="demo-workspace", name="Existing workspace")
            db.add(workspace)
            db.flush()
            db.add(
                Site(
                    workspace_id=workspace.id,
                    name="Synthetic demo fixture",
                    base_url="http://127.0.0.1:8765",
                    allowed_paths='["/"]',
                    is_synthetic=False,
                )
            )
            db.commit()
    finally:
        engine.dispose()

    with pytest.raises(SeedRefused, match="non-synthetic site"):
        seed_demo_data(database_url, synthetic=True)
