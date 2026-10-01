from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


_db_file = tempfile.NamedTemporaryFile(prefix="site-audit-test-", suffix=".sqlite", delete=False)
_db_file.close()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_db_file.name).as_posix()}"
os.environ["ALLOW_LOOPBACK"] = "true"


@pytest.fixture(autouse=True)
def clean_database():
    from alembic import command
    from alembic.config import Config
    from backend.database import SessionLocal
    from backend.models import (
        AuditEvent,
        AuditFinding,
        AuditRuleResult,
        ChangeApproval,
        ChangeRequest,
        ChangeRevision,
        ContentGenerationItem,
        ContentGenerationTask,
        Fact,
        Job,
        Membership,
        OutboxEvent,
        Page,
        PageSnapshot,
        ProcurementQuestion,
        ProcurementQuestionPageMapping,
        ProcurementQuestionSet,
        ProcurementQuestionSetVersion,
        PublicationAttempt,
        Site,
        VisibilityRun,
        VisibilitySample,
        Workspace,
    )

    command.upgrade(Config("backend/alembic.ini"), "head")
    with SessionLocal() as db:
        for model in (
            AuditEvent,
            Membership,
            OutboxEvent,
            VisibilitySample,
            VisibilityRun,
            PublicationAttempt,
            ChangeApproval,
            ContentGenerationItem,
            ContentGenerationTask,
            ChangeRevision,
            ChangeRequest,
            ProcurementQuestionPageMapping,
            ProcurementQuestion,
            ProcurementQuestionSetVersion,
            ProcurementQuestionSet,
            Fact,
            AuditRuleResult,
            AuditFinding,
            PageSnapshot,
            Job,
            Page,
            Site,
            Workspace,
        ):
            db.query(model).delete()
        db.commit()
    yield


def pytest_sessionfinish(session, exitstatus):
    try:
        from backend.database import engine
        engine.dispose()
        Path(_db_file.name).unlink(missing_ok=True)
    except Exception:
        pass
