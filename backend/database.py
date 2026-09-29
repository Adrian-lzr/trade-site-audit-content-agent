from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


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
