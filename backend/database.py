from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def integrity_diagnostics() -> dict[str, list[dict[str, int]]]:
    """Report existing referential/workspace conflicts without repairing them."""
    from sqlalchemy import text

    with engine.connect() as connection:
        orphan_facts = connection.execute(text(
            "SELECT f.id, f.workspace_id FROM facts f LEFT JOIN workspaces w ON w.id=f.workspace_id WHERE w.id IS NULL"
        )).mappings().all()
        cross_workspace_parents = connection.execute(text(
            "SELECT child.id, child.workspace_id FROM facts child JOIN facts parent ON parent.id=child.parent_id "
            "WHERE child.workspace_id <> parent.workspace_id"
        )).mappings().all()
    return {
        "orphan_facts": [dict(row) for row in orphan_facts],
        "cross_workspace_fact_parents": [dict(row) for row in cross_workspace_parents],
    }


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    migration_config = Config(str(Path(__file__).with_name("alembic.ini")))
    migration_config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    command.upgrade(migration_config, "head")
    from . import models  # noqa: F401
    from .models import Workspace

    with SessionLocal() as db:
        if db.query(Workspace).filter(Workspace.external_id == "demo-workspace").first() is None:
            db.add(Workspace(name="演示工作区", external_id="demo-workspace"))
            db.commit()
