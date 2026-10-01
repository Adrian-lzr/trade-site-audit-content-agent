from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from urllib.parse import urlsplit


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run lease and budget concurrency checks against an isolated local PostgreSQL database.")
    parser.add_argument("--database-url", required=True, help="SQLAlchemy PostgreSQL URL for a new, empty local database")
    return parser.parse_args()


def _assert_local_postgres(database_url: str) -> None:
    parsed = urlsplit(database_url.replace("postgresql+psycopg://", "postgresql://", 1))
    if parsed.scheme != "postgresql" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("refusing to use a non-local PostgreSQL database")


def main() -> int:
    args = _parse_args()
    _assert_local_postgres(args.database_url)
    os.environ["DATABASE_URL"] = args.database_url
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import MetaData, func, inspect, select, update
    from sqlalchemy.schema import Table

    from backend.database import SessionLocal, engine
    from backend.models import (
        OutboxEvent,
        ProcurementQuestionSet,
        ProcurementQuestionSetVersion,
        Site,
        VisibilityRun,
        Workspace,
        utcnow,
    )
    from backend.publication_worker import PublicationWorker
    from backend.visibility_worker import VisibilityWorker, _reserve_sample_budget

    config = Config(str(project_root / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "backend" / "migrations"))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        names = [name for name in inspect(engine).get_table_names() if name != "alembic_version"]
        metadata = MetaData()
        populated = []
        for name in names:
            table = Table(name, metadata, autoload_with=engine)
            if connection.scalar(select(func.count()).select_from(table)):
                populated.append(name)
        if populated:
            raise SystemExit(f"refusing to run against a non-empty database: {', '.join(sorted(populated))}")

    marker = secrets.token_hex(8)
    external_id = f"phase6-concurrency-{marker}"
    outbox_key = f"phase6-concurrency:{marker}:publish"
    workspace_id = budget_run_id = recovery_run_id = outbox_id = None
    try:
        with SessionLocal() as db:
            workspace = Workspace(name="temporary PostgreSQL concurrency check", external_id=external_id)
            db.add(workspace)
            db.flush()
            site = Site(workspace_id=workspace.id, name="temporary", base_url="https://example.invalid")
            db.add(site)
            db.flush()
            question_set = ProcurementQuestionSet(workspace_id=workspace.id, site_id=site.id, name="temporary")
            db.add(question_set)
            db.flush()
            version = ProcurementQuestionSetVersion(question_set_id=question_set.id, version=1, state="frozen")
            db.add(version)
            db.flush()

            budget_run = VisibilityRun(
                workspace_id=workspace.id,
                site_id=site.id,
                question_set_id=question_set.id,
                question_set_version_id=version.id,
                provider="fixture",
                provider_kind="model_api",
                status="running",
                market="US",
                language="en",
                lease_token="budget-token",
                lease_expires_at=utcnow() + timedelta(minutes=5),
                budget_usd="0.000010",
                total_cost_usd="0.000000",
                reserved_cost_usd="0.000000",
            )
            recovery_run = VisibilityRun(
                workspace_id=workspace.id,
                site_id=site.id,
                question_set_id=question_set.id,
                question_set_version_id=version.id,
                provider="fixture",
                provider_kind="model_api",
                status="queued",
                market="US",
                language="en",
                budget_usd="0.010000",
                total_cost_usd="0.000000",
                reserved_cost_usd="0.000000",
            )
            outbox = OutboxEvent(
                event_type="change.publish_requested",
                aggregate_type="change_request",
                aggregate_id="temporary",
                idempotency_key=outbox_key,
                payload_json="{}",
            )
            db.add_all([budget_run, recovery_run, outbox])
            db.commit()
            workspace_id = workspace.id
            budget_run_id = budget_run.id
            recovery_run_id = recovery_run.id
            outbox_id = outbox.id

        barrier = Barrier(2)

        def reserve_once() -> bool:
            with SessionLocal() as db:
                barrier.wait(timeout=10)
                reserved = _reserve_sample_budget(
                    db,
                    budget_run_id,
                    1,
                    Decimal("0.000006"),
                    Decimal("0.000010"),
                    "budget-token",
                )
                db.commit()
                return reserved

        with ThreadPoolExecutor(max_workers=2) as pool:
            reservations = list(pool.map(lambda _: reserve_once(), range(2)))
        if sorted(reservations) != [False, True]:
            raise AssertionError(f"budget reservation race produced {reservations!r}")
        with SessionLocal() as db:
            reserved_amount = db.scalar(select(VisibilityRun.reserved_cost_usd).where(VisibilityRun.id == budget_run_id))
        if reserved_amount != "0.000006":
            raise AssertionError(f"unexpected reserved amount: {reserved_amount!r}")

        publication_worker = PublicationWorker(lease_duration=timedelta(seconds=30))
        barrier = Barrier(2)

        def claim_once() -> int | None:
            barrier.wait(timeout=10)
            return publication_worker._claim()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outbox_claims = list(pool.map(lambda _: claim_once(), range(2)))
        if sum(claim is not None for claim in outbox_claims) != 1 or next(
            claim for claim in outbox_claims if claim is not None
        ) != outbox_id:
            raise AssertionError(f"outbox claim race produced {outbox_claims!r}")

        with SessionLocal() as db:
            db.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == outbox_id)
                .values(lease_expires_at=utcnow() - timedelta(seconds=1))
            )
            db.execute(
                update(VisibilityRun)
                .where(VisibilityRun.id == recovery_run_id)
                .values(reserved_cost_usd="0.000006")
            )
            db.commit()
        if publication_worker.recover_interrupted() != 1 or publication_worker._claim() != outbox_id:
            raise AssertionError("outbox lease recovery failed")

        visibility_worker = VisibilityWorker(lease_duration=timedelta(seconds=30))
        first_claim = visibility_worker._claim(run_id=recovery_run_id)
        if first_claim is None:
            raise AssertionError("visibility run was not initially claimed")
        with SessionLocal() as db:
            db.execute(
                update(VisibilityRun)
                .where(VisibilityRun.id == recovery_run_id)
                .values(lease_expires_at=utcnow() - timedelta(seconds=1), reserved_cost_usd="0.000006")
            )
            db.commit()
        if visibility_worker.recover_interrupted() != 1:
            raise AssertionError("visibility lease recovery did not requeue the run")
        with SessionLocal() as db:
            recovered = db.get(VisibilityRun, recovery_run_id)
            if recovered is None or recovered.status != "queued" or recovered.reserved_cost_usd != "0.000000":
                raise AssertionError("visibility recovery did not release the reservation")
        second_claim = visibility_worker._claim(run_id=recovery_run_id)
        if second_claim is None or second_claim[1] == first_claim[1]:
            raise AssertionError("visibility run was not reclaimed with a new lease")

        print(
            json.dumps(
                {
                    "alembic_head": "0014_memberships_audit_events_snapshot_metadata",
                    "budget_concurrent_reservations": reservations,
                    "budget_reserved_usd": reserved_amount,
                    "outbox_concurrent_claims": outbox_claims,
                    "outbox_lease_recovery": "passed",
                    "visibility_lease_recovery": "passed",
                    "process_crash_simulated": False,
                },
                sort_keys=True,
            )
        )
    finally:
        with SessionLocal() as db:
            if outbox_key:
                db.query(OutboxEvent).filter(OutboxEvent.idempotency_key == outbox_key).delete(synchronize_session=False)
            if workspace_id is not None:
                workspace = db.get(Workspace, workspace_id)
                if workspace is not None:
                    db.delete(workspace)
            db.commit()
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
