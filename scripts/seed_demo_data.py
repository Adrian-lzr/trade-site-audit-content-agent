"""Seed a deterministic, local-only synthetic procurement demo.

The command intentionally works only with a local SQLite database and an
explicit ``--synthetic`` opt-in.  It reads checked-in ``demo-site`` files and
does not start a server, make HTTP requests, call a provider, or touch Git,
CMS, publication, or deployment endpoints.

Example::

    python scripts/seed_demo_data.py --synthetic

The command is idempotent.  It creates or reuses the ``demo-workspace``
workspace, one loopback fixture site, local page snapshots, confirmed public
facts, and one frozen twenty-question procurement question-set version.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import Base  # noqa: E402
from backend.models import (  # noqa: E402
    Fact,
    Page,
    PageSnapshot,
    ProcurementQuestion,
    ProcurementQuestionPageMapping,
    ProcurementQuestionSet,
    ProcurementQuestionSetVersion,
    Site,
    Workspace,
)


DATASET = "phase1-demo-seed"
DATASET_VERSION = "phase1-demo-seed-v1"
SEED_VERSION = DATASET_VERSION
WORKSPACE_EXTERNAL_ID = "demo-workspace"
SITE_NAME = "Synthetic demo fixture"
SITE_ORIGIN = "http://127.0.0.1:8765"
QUESTION_SET_NAME = "Synthetic valve procurement questions"
SOURCE_ID = "synthetic-demo-site-v1"
SEED_CREATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
SEED_FROZEN_AT = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)

# These are the files served by backend.fixture_server.  Keeping the list in
# the recipe makes the seed independent of directory ordering and networking.
DEMO_PAGE_PATHS = (
    "/",
    "/missing-title.html",
    "/applications/food-processing.html",
    "/applications/selection.html",
    "/applications/utilities.html",
    "/applications/water-treatment.html",
    "/engineering/connections.html",
    "/engineering/materials.html",
    "/engineering/pressure.html",
    "/procurement/order-details.html",
    "/procurement/request-a-quote.html",
    "/products/valve-series.html",
    "/products/vx-21.html",
    "/products/vx-22.html",
    "/products/vx-23.html",
    "/products/vx-24.html",
    "/products/vx-25.html",
    "/resources/certificates.html",
    "/resources/documents.html",
    "/resources/packaging.html",
)

QUESTIONS = (
    "What process-fluid details should a buyer provide when screening valve materials?",
    "Which pressure and temperature limits should a buyer compare across valve models?",
    "What connection standards should a buyer confirm before requesting a valve quote?",
    "How should a buyer compare the pressure and temperature limits stated for valve models?",
    "What maintenance details should a buyer review before choosing an industrial valve?",
    "Which spare parts should a buyer identify for planned valve maintenance?",
    "What packaging details should be confirmed for valves shipped overseas?",
    "Which lead-time details should a buyer confirm before placing a valve order?",
    "What information is needed to evaluate a valve sample for a new application?",
    "How can a buyer identify the correct actuator requirements for an automated valve?",
    "What installation clearances should a buyer verify for a selected valve assembly?",
    "Which documentation should accompany a valve for installation and maintenance?",
    "What questions should a buyer ask when comparing valve options for water treatment?",
    "How can a buyer check whether a proposed valve configuration matches the stated duty?",
    "Which body and trim materials should a buyer verify for corrosive service?",
    "What test records should a buyer request when evaluating a valve supplier?",
    "How should a buyer confirm that a selected valve fits the intended pipe size?",
    "What certificate details should a buyer check before approving a valve order?",
    "Which application details should be included in a valve request for quotation?",
    "What delivery and order details should a buyer record before a valve purchase?",
)

# Every fact is synthetic, current, confirmed, public, and traceable to a
# checked-in fixture path.  Stable series IDs make reruns detect the same rows.
FACT_SPECS = (
    {
        "series_id": "synthetic-demo-valve-family",
        "subject": "synthetic valve series",
        "predicate": "product_family",
        "value": "VX synthetic industrial valve series",
        "unit": None,
        "source_locator": "demo-site/products/valve-series.html#product-family",
    },
    {
        "series_id": "synthetic-demo-pressure-rating",
        "subject": "synthetic valve series",
        "predicate": "pressure_rating",
        "value": "10",
        "unit": "bar",
        "source_locator": "demo-site/engineering/pressure.html#pressure-rating",
    },
    {
        "series_id": "synthetic-demo-temperature-range",
        "subject": "synthetic valve series",
        "predicate": "temperature_range",
        "value": "-20 to 120",
        "unit": "deg C",
        "source_locator": "demo-site/engineering/pressure.html#temperature-range",
    },
    {
        "series_id": "synthetic-demo-body-material",
        "subject": "synthetic valve series",
        "predicate": "body_material",
        "value": "stainless steel",
        "unit": None,
        "source_locator": "demo-site/engineering/materials.html#body-material",
    },
    {
        "series_id": "synthetic-demo-connections",
        "subject": "synthetic valve series",
        "predicate": "connection",
        "value": "flanged",
        "unit": None,
        "source_locator": "demo-site/engineering/connections.html#connection",
    },
    {
        "series_id": "synthetic-demo-lead-time",
        "subject": "synthetic valve series",
        "predicate": "lead_time",
        "value": "2-4 weeks",
        "unit": None,
        "source_locator": "demo-site/procurement/order-details.html#lead-time",
    },
    {
        "series_id": "synthetic-demo-documentation",
        "subject": "synthetic valve series",
        "predicate": "documentation",
        "value": "installation and maintenance guide",
        "unit": None,
        "source_locator": "demo-site/resources/documents.html#documentation",
    },
)


class SeedRefused(RuntimeError):
    """Raised when the command would leave the local synthetic boundary."""


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.parts.append(data)

    @property
    def title(self) -> str | None:
        value = "".join(self.parts).strip()
        return value or None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _validate_sqlite_url(database_url: str) -> str:
    value = database_url.strip()
    try:
        parsed = make_url(value)
    except Exception as exc:  # pragma: no cover - SQLAlchemy owns parser details
        raise SeedRefused("--database-url must be a local SQLite SQLAlchemy URL") from exc
    if parsed.get_backend_name() != "sqlite":
        raise SeedRefused("demo seed refuses non-SQLite databases")
    if parsed.host not in {None, "", "localhost"}:
        raise SeedRefused("demo seed only accepts a local SQLite database")
    if parsed.username or parsed.password:
        raise SeedRefused("demo seed rejects credentials in a SQLite URL")
    return value


def _upgrade_schema(database_url: str) -> str:
    """Apply the checked-in migration head to a file-backed SQLite database."""

    migration_config = Config(str(ROOT / "backend" / "alembic.ini"))
    migration_config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    migration_config.set_main_option("sqlalchemy.url", database_url)
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(migration_config, "head")
        expected_head = ScriptDirectory.from_config(migration_config).get_current_head()
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                actual_head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        finally:
            engine.dispose()
        if actual_head != expected_head:
            raise SeedRefused(f"SQLite migration stopped at {actual_head!r}; expected {expected_head!r}")
        return str(actual_head)
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


def _fixture_files() -> list[tuple[str, Path, str, str | None]]:
    fixture_root = ROOT / "demo-site"
    entries: list[tuple[str, Path, str, str | None]] = []
    for path in DEMO_PAGE_PATHS:
        relative = Path("index.html") if path == "/" else Path(path.lstrip("/"))
        file_path = fixture_root / relative
        if not file_path.is_file():
            raise SeedRefused(f"checked-in demo page is missing: {relative.as_posix()}")
        raw = file_path.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SeedRefused(f"demo page is not UTF-8: {relative.as_posix()}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        parser = _TitleParser()
        parser.feed(content)
        entries.append((path, file_path, digest, parser.title))
    return entries


def _manifest_hash(entries: list[tuple[str, Path, str, str | None]]) -> str:
    manifest = "".join(f"{path}\0{digest}\n" for path, _file, digest, _title in entries)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def _ensure_workspace(db: Session) -> tuple[Workspace, bool]:
    workspace = db.scalar(select(Workspace).where(Workspace.external_id == WORKSPACE_EXTERNAL_ID))
    if workspace is not None:
        return workspace, False
    workspace = Workspace(
        external_id=WORKSPACE_EXTERNAL_ID,
        name="Synthetic demo workspace",
        created_at=SEED_CREATED_AT,
    )
    db.add(workspace)
    db.flush()
    return workspace, True


def _validate_loopback_origin(origin: str) -> None:
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 8765
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise SeedRefused("synthetic demo site must use the local fixture origin http://127.0.0.1:8765")


def _ensure_site(db: Session, workspace: Workspace) -> tuple[Site, bool]:
    _validate_loopback_origin(SITE_ORIGIN)
    site = db.scalar(
        select(Site).where(Site.workspace_id == workspace.id, Site.name == SITE_NAME)
    )
    if site is not None:
        if not site.is_synthetic:
            raise SeedRefused("refusing to seed a non-synthetic site with the demo site identity")
        if site.base_url.rstrip("/") != SITE_ORIGIN:
            raise SeedRefused("existing synthetic demo site has an unexpected origin")
        if json.loads(site.allowed_paths or "[]") != ["/"]:
            raise SeedRefused("existing synthetic demo site has an unexpected allowed path policy")
        return site, False

    conflicting_origin = db.scalar(
        select(Site).where(Site.workspace_id == workspace.id, Site.base_url == SITE_ORIGIN)
    )
    if conflicting_origin is not None:
        raise SeedRefused("refusing to reuse an existing site at the synthetic loopback origin")

    site = Site(
        workspace_id=workspace.id,
        name=SITE_NAME,
        base_url=SITE_ORIGIN,
        allowed_paths=json.dumps(["/"], separators=(",", ":")),
        is_synthetic=True,
        audit_policy_json=json.dumps(
            {"mode": "synthetic", "source": "checked-in demo-site"},
            sort_keys=True,
            separators=(",", ":"),
        ),
        audit_page_limit=len(DEMO_PAGE_PATHS),
        created_at=SEED_CREATED_AT,
    )
    db.add(site)
    db.flush()
    return site, True


def _ensure_pages_and_snapshots(
    db: Session,
    site: Site,
    entries: list[tuple[str, Path, str, str | None]],
) -> tuple[list[Page], list[PageSnapshot], bool, bool]:
    pages: list[Page] = []
    snapshots: list[PageSnapshot] = []
    pages_created = False
    snapshots_created = False
    for path, file_path, content_hash, title in entries:
        canonical_url = SITE_ORIGIN + ("/" if path == "/" else path)
        page = db.scalar(select(Page).where(Page.site_id == site.id, Page.canonical_url == canonical_url))
        if page is None:
            page = Page(site_id=site.id, canonical_url=canonical_url, created_at=SEED_CREATED_AT)
            db.add(page)
            db.flush()
            pages_created = True
        pages.append(page)

        existing_snapshot = db.scalar(
            select(PageSnapshot).where(
                PageSnapshot.page_id == page.id,
                PageSnapshot.content_hash == content_hash,
                PageSnapshot.is_synthetic.is_(True),
            )
        )
        if existing_snapshot is None:
            raw = file_path.read_bytes()
            existing_snapshot = PageSnapshot(
                page_id=page.id,
                url=canonical_url,
                status_code=200,
                title=title,
                content_hash=content_hash,
                content_type="text/html; charset=utf-8",
                content=raw.decode("utf-8"),
                headers_json=json.dumps(
                    {"content-type": "text/html; charset=utf-8"},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                is_synthetic=True,
                audit_rule_version="1.0.0",
                fetched_at=SEED_CREATED_AT,
            )
            db.add(existing_snapshot)
            db.flush()
            snapshots_created = True
        elif (
            existing_snapshot.url != canonical_url
            or existing_snapshot.content_hash != content_hash
            or not existing_snapshot.is_synthetic
        ):
            raise SeedRefused("existing synthetic page snapshot does not match the checked-in fixture")
        snapshots.append(existing_snapshot)
    return pages, snapshots, pages_created, snapshots_created


def _fact_matches(fact: Fact, spec: dict[str, str | None]) -> bool:
    return all(
        (
            getattr(fact, field)
            == value
        )
        for field, value in {
            "series_id": spec["series_id"],
            "subject": spec["subject"],
            "predicate": spec["predicate"],
            "value": spec["value"],
            "unit": spec["unit"],
            "source_id": SOURCE_ID,
            "source_locator": spec["source_locator"],
        }.items()
    ) and (
        fact.visibility == "public"
        and fact.status == "confirmed"
        and fact.valid_until is None
        and _iso(fact.valid_from) == _iso(SEED_CREATED_AT)
    )


def _ensure_facts(db: Session, workspace: Workspace) -> tuple[list[Fact], bool]:
    facts: list[Fact] = []
    created = False
    for spec in FACT_SPECS:
        fact = db.scalar(
            select(Fact).where(
                Fact.workspace_id == workspace.id,
                Fact.series_id == spec["series_id"],
                Fact.version == 1,
            )
        )
        if fact is None:
            fact = Fact(
                workspace_id=workspace.id,
                series_id=spec["series_id"],
                subject=spec["subject"],
                predicate=spec["predicate"],
                value=spec["value"],
                unit=spec["unit"],
                source_id=SOURCE_ID,
                source_locator=spec["source_locator"],
                visibility="public",
                status="confirmed",
                version=1,
                valid_from=SEED_CREATED_AT,
                valid_until=None,
                reviewer="synthetic-demo-seed",
                reviewed_at=SEED_CREATED_AT,
                created_at=SEED_CREATED_AT,
            )
            db.add(fact)
            db.flush()
            created = True
        elif not _fact_matches(fact, spec):
            raise SeedRefused(f"existing fact {spec['series_id']} does not match the synthetic seed")
        facts.append(fact)
    return facts, created


def _question_fields(position: int, question: str) -> dict[str, str | int]:
    return {
        "position": position,
        "question": question,
        "product": "industrial valve",
        "use_case": "synthetic valve supplier demo; buyer evaluating process requirements",
        "buyer_role": "procurement engineer",
        "purchase_stage": "supplier evaluation",
        "target_market": "To be confirmed",
        "language": "en",
    }


def _ensure_question_set(
    db: Session,
    workspace: Workspace,
    site: Site,
    pages: list[Page],
) -> tuple[ProcurementQuestionSet, ProcurementQuestionSetVersion, bool]:
    question_set = db.scalar(
        select(ProcurementQuestionSet).where(
            ProcurementQuestionSet.workspace_id == workspace.id,
            ProcurementQuestionSet.site_id == site.id,
            ProcurementQuestionSet.name == QUESTION_SET_NAME,
        )
    )
    created = False
    if question_set is None:
        question_set = ProcurementQuestionSet(
            workspace_id=workspace.id,
            site_id=site.id,
            name=QUESTION_SET_NAME,
            current_version=1,
            created_at=SEED_CREATED_AT,
        )
        db.add(question_set)
        db.flush()
        version = ProcurementQuestionSetVersion(
            question_set_id=question_set.id,
            version=1,
            edit_version=2,
            state="frozen",
            created_at=SEED_CREATED_AT,
            frozen_at=SEED_FROZEN_AT,
        )
        db.add(version)
        db.flush()
        for position, (question, page) in enumerate(zip(QUESTIONS, pages, strict=True), start=1):
            question_row = ProcurementQuestion(
                question_set_version_id=version.id,
                **_question_fields(position, question),
                created_at=SEED_CREATED_AT,
            )
            db.add(question_row)
            db.flush()
            db.add(
                ProcurementQuestionPageMapping(
                    question_id=question_row.id,
                    page_id=page.id,
                    position=1,
                    created_at=SEED_CREATED_AT,
                )
            )
        db.flush()
        created = True
    else:
        if question_set.current_version != 1:
            raise SeedRefused("existing synthetic question set has an unexpected current version")
        version = db.scalar(
            select(ProcurementQuestionSetVersion).where(
                ProcurementQuestionSetVersion.question_set_id == question_set.id,
                ProcurementQuestionSetVersion.version == 1,
            )
        )
        if version is None or version.state != "frozen":
            raise SeedRefused("existing synthetic question set version is not frozen")
        questions = list(
            db.scalars(
                select(ProcurementQuestion)
                .where(ProcurementQuestion.question_set_version_id == version.id)
                .order_by(ProcurementQuestion.position)
            )
        )
        if len(questions) != len(QUESTIONS):
            raise SeedRefused("existing synthetic question set must contain exactly 20 questions")
        for position, (question, page) in enumerate(zip(QUESTIONS, pages, strict=True), start=1):
            row = questions[position - 1]
            expected = _question_fields(position, question)
            if any(getattr(row, field) != value for field, value in expected.items()):
                raise SeedRefused(f"existing synthetic question {position} does not match the seed")
            mappings = list(
                db.scalars(
                    select(ProcurementQuestionPageMapping).where(
                        ProcurementQuestionPageMapping.question_id == row.id
                    )
                )
            )
            if len(mappings) != 1 or mappings[0].page_id != page.id or mappings[0].position != 1:
                raise SeedRefused(f"existing synthetic question {position} has an unexpected page mapping")
    return question_set, version, created


def _seed(db: Session, *, migration_head: str | None) -> dict[str, Any]:
    entries = _fixture_files()
    workspace, workspace_created = _ensure_workspace(db)
    site, site_created = _ensure_site(db, workspace)
    pages, snapshots, pages_created, snapshots_created = _ensure_pages_and_snapshots(db, site, entries)
    facts, facts_created = _ensure_facts(db, workspace)
    question_set, version, question_set_created = _ensure_question_set(db, workspace, site, pages)

    manifest_hash = _manifest_hash(entries)
    snapshot_rows = [
        {
            "id": snapshot.id,
            "page_id": snapshot.page_id,
            "url": snapshot.url,
            "content_hash": snapshot.content_hash,
            "is_synthetic": snapshot.is_synthetic,
            "fetched_at": _iso(snapshot.fetched_at),
        }
        for snapshot in snapshots
    ]
    fact_rows = [
        {
            "id": fact.id,
            "series_id": fact.series_id,
            "subject": fact.subject,
            "predicate": fact.predicate,
            "value": fact.value,
            "unit": fact.unit,
            "synthetic": True,
            "is_synthetic": True,
            "status": fact.status,
            "visibility": fact.visibility,
            "source_id": fact.source_id,
            "source_locator": fact.source_locator,
            "validity": {"valid_from": _iso(fact.valid_from), "valid_until": _iso(fact.valid_until)},
        }
        for fact in facts
    ]
    return {
        "ok": True,
        "dataset": DATASET,
        "dataset_version": DATASET_VERSION,
        "seed_version": SEED_VERSION,
        "mode": "synthetic_local_fixture",
        "database": {"backend": "sqlite", "local_only": True},
        "schema": {
            "migration_head": migration_head,
            "initialization": "alembic" if migration_head is not None else "in_memory_model_metadata",
        },
        "workspace": {
            "id": workspace.id,
            "external_id": workspace.external_id,
            "name": workspace.name,
            "created": workspace_created,
        },
        "site": {
            "id": site.id,
            "workspace_id": site.workspace_id,
            "name": site.name,
            "origin": site.base_url,
            "is_synthetic": site.is_synthetic,
            "allowed_paths": json.loads(site.allowed_paths),
            "created": site_created,
        },
        "fact_ids": [fact.id for fact in facts],
        "facts": fact_rows,
        "question_set": {
            "id": question_set.id,
            "name": question_set.name,
            "workspace_id": question_set.workspace_id,
            "site_id": question_set.site_id,
            "current_version": question_set.current_version,
        },
        "question_set_version": {
            "id": version.id,
            "question_set_id": version.question_set_id,
            "version": version.version,
            "state": version.state,
            "edit_version": version.edit_version,
            "frozen_at": _iso(version.frozen_at),
            "created": question_set_created,
        },
        "question_count": len(QUESTIONS),
        "page_ids": [page.id for page in pages],
        "snapshot_ids": [snapshot.id for snapshot in snapshots],
        "snapshot": {
            "kind": "checked_in_demo_site",
            "source_root": "demo-site",
            "manifest_version": "demo-site-v1",
            "manifest_sha256": manifest_hash,
            "page_count": len(entries),
            "pages": [path for path, _file, _digest, _title in entries],
            "rows": snapshot_rows,
            "created": snapshots_created,
        },
        "source_metadata": {
            "mode": "synthetic",
            "source_id": SOURCE_ID,
            "source_kind": "checked_in_local_fixture",
            "source_root": "demo-site",
            "network_access": False,
            "remote_writes": False,
            "provider_calls": False,
            "validity": {"valid_from": _iso(SEED_CREATED_AT), "valid_until": None},
        },
        "created": {
            "workspace": workspace_created,
            "site": site_created,
            "pages": pages_created,
            "snapshots": snapshots_created,
            "facts": facts_created,
            "question_set": question_set_created,
        },
        "limitations": [
            "Local synthetic demo evidence only; it is not customer data.",
            "The seed does not publish, call a CMS, push Git, call a remote provider, or represent an online release.",
        ],
    }


def seed_demo_data(database_url: str | None = None, *, synthetic: bool = False) -> dict[str, Any]:
    """Seed and return the machine-readable local synthetic evidence report."""

    if not synthetic:
        raise SeedRefused("synthetic mode is required; pass --synthetic explicitly")
    database_url = database_url or os.getenv("DATABASE_URL", "sqlite:///./backend.db")
    database_url = _validate_sqlite_url(database_url)
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    try:
        # File-backed databases use the repository migration head so the API
        # can start against the seeded file without replaying 0001.  SQLite
        # in-memory databases are kept as a lightweight test-only convenience
        # because Alembic closes its connection after upgrading them.
        if make_url(database_url).database == ":memory:":
            Base.metadata.create_all(engine)
            migration_head = None
        else:
            migration_head = _upgrade_schema(database_url)
        session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with session_factory() as db:
            report = _seed(db, migration_head=migration_head)
            db.commit()
        return report
    finally:
        engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite:///./backend.db"),
        help="local SQLite SQLAlchemy URL (defaults to DATABASE_URL or ./backend.db)",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="required explicit opt-in for the checked-in synthetic fixture",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/artifacts/phase1-demo-seed.json"),
        help="JSON evidence output path",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = seed_demo_data(args.database_url, synthetic=args.synthetic)
    except (SeedRefused, OSError, ValueError) as exc:
        error = {
            "ok": False,
            "dataset": DATASET,
            "dataset_version": DATASET_VERSION,
            "seed_version": SEED_VERSION,
            "error": str(exc),
        }
        print(json.dumps(error, ensure_ascii=True, sort_keys=True), file=sys.stderr)
        return 2
    encoded = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
