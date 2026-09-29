from __future__ import annotations

import ipaddress
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .crawler import CrawlError, allowed_paths_json, normalize_path
from .database import get_db, init_db
from .models import ChangeApproval, ChangeRequest, ChangeRevision, ChangeState, ContentGenerationItem, ContentGenerationTask, Fact, FactStatus, FactVisibility, Job, JobStatus, OutboxEvent, Page, PageSnapshot, ProcurementQuestion, ProcurementQuestionPageMapping, ProcurementQuestionSet, ProcurementQuestionSetVersion, PublicationAttempt, Site, Workspace, utcnow
from .schemas import ChangeAction, ChangeApprovalCreate, ChangeApprovalOut, ChangeRequestCreate, ChangeRequestOut, ChangeRevisionCreate, ChangeRevisionOut, ContentGenerationItemCreate, ContentGenerationItemOut, ContentGenerationTaskCreate, ContentGenerationTaskOut, ContentGenerationTaskSummaryOut, FactCreate, FactOut, FactReview, FindingOut, JobOut, ProcurementQuestionCreate, ProcurementQuestionOut, ProcurementQuestionPageMappingOut, ProcurementQuestionSetCreate, ProcurementQuestionSetOut, ProcurementQuestionSetSummaryOut, ProcurementQuestionSetVersionCreate, ProcurementQuestionSetVersionOut, ProcurementQuestionSetVersionUpdate, PublicationAttemptOut, RuleResultOut, SiteCreate, SnapshotOut, WorkspaceCreate, WorkspaceOut


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Site Audit API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {"status": "ok", "mode": "http", "fixture": False, "fixture_available": True, "version": "0.1.0"}


@app.get("/api/health")
def api_health() -> dict[str, str | bool]:
    return health()


@app.post("/api/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate, db: Session = Depends(get_db)):
    workspace = Workspace(name=payload.name)
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return workspace


def _workspace_for_key(db: Session, workspace_key: str | int | None) -> Workspace | None:
    if workspace_key is None:
        return None
    key = str(workspace_key)
    if key.isdigit():
        return db.get(Workspace, int(key))
    return db.scalar(select(Workspace).where(Workspace.external_id == key))


def _fact_expired(fact: Fact, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    valid_until = fact.valid_until
    if valid_until is None:
        return False
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
    return valid_until <= now


def _fact_current(fact: Fact, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    valid_from = fact.valid_from
    if valid_from.tzinfo is None:
        valid_from = valid_from.replace(tzinfo=timezone.utc)
    if valid_from > now or _fact_expired(fact, now):
        return False
    return fact.status == FactStatus.confirmed.value


def _fact_response(fact: Fact, *, public: bool = False, now: datetime | None = None) -> FactOut:
    now = now or datetime.now(timezone.utc)
    response_status = fact.status
    if fact.status == FactStatus.confirmed.value and _fact_expired(fact, now):
        response_status = FactStatus.expired.value
    # Public responses are built only from confirmed/current rows. Keep this
    # guard here as a second line of defense for future callers.
    is_public_current = fact.visibility == FactVisibility.public.value and _fact_current(fact, now)
    value = fact.value if not public or is_public_current else None
    return FactOut(
        id=fact.id,
        workspace_id=fact.workspace_id,
        series_id=fact.series_id,
        parent_id=fact.parent_id,
        subject=fact.subject,
        predicate=fact.predicate,
        value=value,
        unit=fact.unit,
        source_id=fact.source_id,
        source_locator=fact.source_locator,
        visibility=fact.visibility,
        status=response_status,
        version=fact.version,
        valid_from=fact.valid_from,
        valid_until=fact.valid_until,
        reviewer=fact.reviewer,
        reviewed_at=fact.reviewed_at,
        created_at=fact.created_at,
    )


def _fact_workspace_or_404(db: Session, fact_id: int, workspace_key: str | int | None) -> Fact:
    fact = db.get(Fact, fact_id)
    if fact is None:
        raise HTTPException(404, "fact not found")
    if workspace_key is not None:
        workspace = _workspace_for_key(db, workspace_key)
        if workspace is None or fact.workspace_id != workspace.id:
            raise HTTPException(404, "fact not found")
    return fact


def _import_fact(db: Session, payload: FactCreate, workspace: Workspace) -> Fact:
    parent = None
    if payload.parent_id is not None:
        parent = db.get(Fact, payload.parent_id)
        if parent is None or parent.workspace_id != workspace.id:
            raise HTTPException(404, "parent fact not found")
    series_id = payload.series_id or (parent.series_id if parent else None)
    if series_id is None:
        series_id = __import__("uuid").uuid4().hex
    max_version = db.scalar(select(func.max(Fact.version)).where(Fact.workspace_id == workspace.id, Fact.series_id == series_id)) or 0
    requested_version = payload.version
    version = requested_version or (max_version + 1)
    if version != max_version + 1:
        raise HTTPException(409, "fact version must append the existing history")
    valid_from = payload.valid_from or utcnow()
    fact = Fact(
        workspace_id=workspace.id,
        series_id=series_id,
        parent_id=parent.id if parent else None,
        subject=payload.subject,
        predicate=payload.predicate,
        value=payload.value,
        unit=payload.unit,
        source_id=payload.source_id,
        source_locator=payload.source_locator,
        visibility=payload.visibility,
        status=FactStatus.proposed.value,
        version=version,
        valid_from=valid_from,
        valid_until=payload.valid_until,
    )
    db.add(fact)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "fact version already exists") from exc
    db.refresh(fact)
    return fact


@app.post("/api/workspaces/{workspace_key}/facts", response_model=FactOut, status_code=status.HTTP_201_CREATED)
def import_workspace_fact(workspace_key: str, payload: FactCreate, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    if payload.workspace_id is not None and str(payload.workspace_id) != str(workspace.id) and str(payload.workspace_id) != (workspace.external_id or ""):
        raise HTTPException(403, "workspace mismatch")
    return _fact_response(_import_fact(db, payload, workspace))


@app.post("/api/facts", response_model=FactOut, status_code=status.HTTP_201_CREATED)
def import_fact(payload: FactCreate, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, payload.workspace_id)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    return _fact_response(_import_fact(db, payload, workspace))


def _list_facts(db: Session, workspace: Workspace, *, public: bool, subject: str | None = None) -> list[FactOut]:
    query = select(Fact).where(Fact.workspace_id == workspace.id)
    if public:
        now = datetime.now(timezone.utc)
        query = query.where(
            Fact.visibility == FactVisibility.public.value,
            Fact.status == FactStatus.confirmed.value,
            Fact.valid_from <= now,
        ).where((Fact.valid_until.is_(None)) | (Fact.valid_until > now))
    if subject:
        query = query.where(Fact.subject == subject)
    facts = db.scalars(query.order_by(Fact.subject, Fact.predicate, Fact.version.desc())).all()
    return [_fact_response(fact, public=public) for fact in facts]


@app.get("/api/workspaces/{workspace_key}/facts", response_model=list[FactOut])
def list_workspace_facts(workspace_key: str, subject: str | None = None, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    return _list_facts(db, workspace, public=False, subject=subject)


@app.get("/api/facts", response_model=list[FactOut])
def list_facts(workspace_id: str, subject: str | None = None, public: bool = False, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_id)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    return _list_facts(db, workspace, public=public, subject=subject)


@app.get("/api/workspaces/{workspace_key}/facts/public", response_model=list[FactOut])
def list_public_workspace_facts(workspace_key: str, subject: str | None = None, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    return _list_facts(db, workspace, public=True, subject=subject)


@app.get("/api/facts/public", response_model=list[FactOut])
def list_public_facts(workspace_id: str, subject: str | None = None, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_id)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    return _list_facts(db, workspace, public=True, subject=subject)


@app.get("/api/workspaces/{workspace_key}/facts/{fact_id}", response_model=FactOut)
def get_workspace_fact(workspace_key: str, fact_id: int, db: Session = Depends(get_db)):
    fact = _fact_workspace_or_404(db, fact_id, workspace_key)
    return _fact_response(fact)


@app.get("/api/facts/{fact_id}", response_model=FactOut)
def get_fact(fact_id: int, workspace_id: str | None = None, db: Session = Depends(get_db)):
    return _fact_response(_fact_workspace_or_404(db, fact_id, workspace_id))


def _review_fact(db: Session, fact: Fact, payload: FactReview, *, target_status: FactStatus) -> Fact:
    if payload.expected_version is not None and payload.expected_version != fact.version:
        raise HTTPException(409, "fact version changed; refresh before reviewing")
    if fact.status != FactStatus.proposed.value:
        raise HTTPException(409, "only proposed facts can be reviewed")
    fact.status = target_status.value
    fact.reviewer = payload.reviewer
    fact.reviewed_at = utcnow()
    db.commit()
    db.refresh(fact)
    return fact


@app.post("/api/workspaces/{workspace_key}/facts/{fact_id}/confirm", response_model=FactOut)
@app.post("/api/facts/{fact_id}/confirm", response_model=FactOut)
@app.post("/api/workspaces/{workspace_key}/facts/{fact_id}/approve", response_model=FactOut, include_in_schema=False)
@app.post("/api/facts/{fact_id}/approve", response_model=FactOut, include_in_schema=False)
def confirm_fact(fact_id: int, payload: FactReview, workspace_key: str | None = None, db: Session = Depends(get_db)):
    fact = _fact_workspace_or_404(db, fact_id, workspace_key)
    return _fact_response(_review_fact(db, fact, payload, target_status=FactStatus.confirmed))


@app.post("/api/workspaces/{workspace_key}/facts/{fact_id}/reject", response_model=FactOut)
@app.post("/api/facts/{fact_id}/reject", response_model=FactOut)
def reject_fact(fact_id: int, payload: FactReview, workspace_key: str | None = None, db: Session = Depends(get_db)):
    fact = _fact_workspace_or_404(db, fact_id, workspace_key)
    return _fact_response(_review_fact(db, fact, payload, target_status=FactStatus.rejected))


@app.post("/api/sites", status_code=status.HTTP_201_CREATED)
def register_site(payload: SiteCreate, db: Session = Depends(get_db)):
    origin = str(payload.site_origin).rstrip("/")
    parsed_origin = urlsplit(str(payload.site_origin))
    if parsed_origin.path not in {"", "/"} or parsed_origin.query or parsed_origin.fragment or parsed_origin.username or parsed_origin.password:
        raise HTTPException(422, "origin must be a root URL without credentials, query, or fragment")
    preferred_origin = str(payload.preferred_origin).rstrip("/") if payload.preferred_origin else origin
    preferred = urlsplit(preferred_origin)
    if preferred.path not in {"", "/"} or preferred.query or preferred.fragment or preferred.username or preferred.password:
        raise HTTPException(422, "preferred_origin must be a root URL without credentials, query, or fragment")
    try:
        address = ipaddress.ip_address(parsed_origin.hostname or "")
    except ValueError:
        address = None
    literal_loopback = address is not None and address.is_loopback
    if payload.is_synthetic:
        if not settings.allow_loopback or not literal_loopback:
            raise HTTPException(422, "synthetic sites require ALLOW_LOOPBACK=true and a literal loopback IP origin")
    elif literal_loopback:
        raise HTTPException(422, "loopback origins must be registered as synthetic sites")
    workspace_key = str(payload.workspace_id)
    workspace = db.get(Workspace, int(workspace_key)) if workspace_key.isdigit() else db.scalar(select(Workspace).where(Workspace.external_id == workspace_key))
    if workspace is None and not workspace_key.isdigit():
        workspace = Workspace(name="演示工作区" if workspace_key == "demo-workspace" else workspace_key, external_id=workspace_key)
        db.add(workspace)
        db.flush()
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    paths = payload.allowed_paths or ["/"]
    try:
        if any(not path.startswith("/") for path in paths):
            raise CrawlError("allowed_paths must be absolute")
        paths = [normalize_path(path) for path in paths]
    except CrawlError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit_policy = {"expected_accessible": payload.expected_accessible, "expected_indexable": payload.expected_indexable, "preferred_origin": preferred_origin, "site_origin": origin}
    site = Site(workspace_id=workspace.id, name=payload.name, base_url=origin, allowed_paths=allowed_paths_json(paths), is_synthetic=payload.is_synthetic, audit_policy_json=json.dumps(audit_policy, sort_keys=True, separators=(",", ":")), audit_page_limit=payload.audit_page_limit)
    db.add(site)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(409, "site already exists") from exc
    db.refresh(site)
    return {"id": str(site.id), "workspace_id": workspace.external_id or str(site.workspace_id), "name": site.name, "origin": site.base_url, "base_url": site.base_url, "allowed_paths": paths, "is_synthetic": site.is_synthetic, "status": "ready", "created_at": site.created_at}


@app.get("/api/sites/{site_id}")
def get_site(site_id: int, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(404, "site not found")
    return {"id": str(site.id), "workspace_id": site.workspace.external_id or str(site.workspace_id), "name": site.name, "origin": site.base_url, "base_url": site.base_url, "allowed_paths": json.loads(site.allowed_paths), "is_synthetic": site.is_synthetic, "status": "ready", "created_at": site.created_at}


@app.get("/api/sites")
def list_sites(workspace_id: str | None = None, db: Session = Depends(get_db)):
    query = select(Site).join(Site.workspace)
    if workspace_id:
        if workspace_id.isdigit():
            query = query.where(Site.workspace_id == int(workspace_id))
        else:
            query = query.where(Workspace.external_id == workspace_id)
    else:
        query = query.where(Workspace.external_id == "demo-workspace")
    sites = db.scalars(query.order_by(Site.created_at)).all()
    return [{"id": str(site.id), "workspace_id": site.workspace.external_id or str(site.workspace_id), "name": site.name, "origin": site.base_url, "base_url": site.base_url, "allowed_paths": json.loads(site.allowed_paths), "is_synthetic": site.is_synthetic, "status": "ready", "created_at": site.created_at} for site in sites]


def _question_set_for_site(db: Session, workspace: Workspace, site: Site, query_set_id: int) -> ProcurementQuestionSet:
    query_set = db.scalar(
        select(ProcurementQuestionSet).where(
            ProcurementQuestionSet.id == query_set_id,
            ProcurementQuestionSet.workspace_id == workspace.id,
            ProcurementQuestionSet.site_id == site.id,
        )
    )
    if query_set is None:
        raise HTTPException(404, "question set not found")
    return query_set


def _question_set_version_for_site(
    db: Session,
    workspace: Workspace,
    site: Site,
    query_set_id: int,
    version_number: int,
) -> ProcurementQuestionSetVersion:
    version = db.scalar(
        select(ProcurementQuestionSetVersion)
        .join(ProcurementQuestionSet)
        .where(
            ProcurementQuestionSet.id == query_set_id,
            ProcurementQuestionSet.workspace_id == workspace.id,
            ProcurementQuestionSet.site_id == site.id,
            ProcurementQuestionSetVersion.version == version_number,
        )
    )
    if version is None:
        raise HTTPException(404, "question set version not found")
    return version


def _validate_question_pages(db: Session, site: Site, questions: list[ProcurementQuestionCreate]) -> None:
    requested_ids = {page_id for question in questions for page_id in question.page_ids}
    if not requested_ids:
        return
    pages = db.scalars(select(Page).where(Page.site_id == site.id, Page.id.in_(requested_ids))).all()
    if {page.id for page in pages} != requested_ids:
        raise HTTPException(422, "page mappings must reference pages on this site")


def _new_procurement_question(payload: ProcurementQuestionCreate, position: int) -> ProcurementQuestion:
    question = ProcurementQuestion(
        position=position,
        question=payload.question.strip(),
        product=payload.product.strip(),
        use_case=payload.use_case.strip(),
        buyer_role=payload.buyer_role.strip(),
        purchase_stage=payload.purchase_stage.strip(),
        target_market=payload.target_market.strip(),
        language=payload.language.strip(),
    )
    question.page_mappings = [
        ProcurementQuestionPageMapping(page_id=page_id, position=mapping_position)
        for mapping_position, page_id in enumerate(payload.page_ids, start=1)
    ]
    return question


def _procurement_question_out(question: ProcurementQuestion) -> ProcurementQuestionOut:
    return ProcurementQuestionOut(
        id=question.id,
        position=question.position,
        question=question.question,
        product=question.product,
        use_case=question.use_case,
        buyer_role=question.buyer_role,
        purchase_stage=question.purchase_stage,
        target_market=question.target_market,
        language=question.language,
        page_mappings=[
            ProcurementQuestionPageMappingOut(
                page_id=mapping.page_id,
                position=mapping.position,
                canonical_url=mapping.page.canonical_url,
            )
            for mapping in question.page_mappings
        ],
    )


def _question_set_version_out(version: ProcurementQuestionSetVersion) -> ProcurementQuestionSetVersionOut:
    return ProcurementQuestionSetVersionOut(
        id=version.id,
        question_set_id=version.question_set_id,
        version=version.version,
        edit_version=version.edit_version,
        state=version.state,
        created_at=version.created_at,
        frozen_at=version.frozen_at,
        questions=[_procurement_question_out(question) for question in version.questions],
    )


def _question_set_summary_out(query_set: ProcurementQuestionSet) -> ProcurementQuestionSetSummaryOut:
    current = next((version for version in query_set.versions if version.version == query_set.current_version), None)
    return ProcurementQuestionSetSummaryOut(
        id=query_set.id,
        workspace_id=query_set.workspace_id,
        site_id=query_set.site_id,
        name=query_set.name,
        current_version=query_set.current_version,
        current_state=current.state if current is not None else "draft",
        created_at=query_set.created_at,
    )


def _question_set_out(query_set: ProcurementQuestionSet) -> ProcurementQuestionSetOut:
    summary = _question_set_summary_out(query_set)
    return ProcurementQuestionSetOut(
        **summary.model_dump(),
        versions=[_question_set_version_out(version) for version in query_set.versions],
    )


def _add_question_payloads(
    db: Session,
    version: ProcurementQuestionSetVersion,
    questions: list[ProcurementQuestionCreate],
) -> None:
    version.questions = [
        _new_procurement_question(question, position)
        for position, question in enumerate(questions, start=1)
    ]
    db.flush()


@app.post(
    "/api/workspaces/{workspace_key}/sites/{site_id}/query-sets",
    response_model=ProcurementQuestionSetOut,
    status_code=status.HTTP_201_CREATED,
)
def create_procurement_question_set(
    workspace_key: str,
    site_id: int,
    payload: ProcurementQuestionSetCreate,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    site = _site_in_workspace(db, site_id, workspace)
    _validate_question_pages(db, site, payload.questions)
    query_set = ProcurementQuestionSet(
        workspace_id=workspace.id,
        site_id=site.id,
        name=payload.name.strip(),
        current_version=1,
    )
    version = ProcurementQuestionSetVersion(version=1, edit_version=1, state="draft")
    query_set.versions = [version]
    db.add(query_set)
    try:
        _add_question_payloads(db, version, payload.questions)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "question set name already exists on this site") from exc
    db.refresh(query_set)
    return _question_set_out(query_set)


@app.get(
    "/api/workspaces/{workspace_key}/sites/{site_id}/query-sets",
    response_model=list[ProcurementQuestionSetSummaryOut],
)
def list_procurement_question_sets(workspace_key: str, site_id: int, db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_key)
    site = _site_in_workspace(db, site_id, workspace)
    query_sets = db.scalars(
        select(ProcurementQuestionSet)
        .where(ProcurementQuestionSet.workspace_id == workspace.id, ProcurementQuestionSet.site_id == site.id)
        .order_by(ProcurementQuestionSet.created_at, ProcurementQuestionSet.id)
    ).all()
    return [_question_set_summary_out(query_set) for query_set in query_sets]


@app.get(
    "/api/workspaces/{workspace_key}/sites/{site_id}/query-sets/{query_set_id}",
    response_model=ProcurementQuestionSetOut,
)
def get_procurement_question_set(
    workspace_key: str,
    site_id: int,
    query_set_id: int,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    site = _site_in_workspace(db, site_id, workspace)
    query_set = _question_set_for_site(db, workspace, site, query_set_id)
    return _question_set_out(query_set)


@app.get(
    "/api/workspaces/{workspace_key}/sites/{site_id}/query-sets/{query_set_id}/versions/{version_number}",
    response_model=ProcurementQuestionSetVersionOut,
)
def get_procurement_question_set_version(
    workspace_key: str,
    site_id: int,
    query_set_id: int,
    version_number: int,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    site = _site_in_workspace(db, site_id, workspace)
    version = _question_set_version_for_site(db, workspace, site, query_set_id, version_number)
    return _question_set_version_out(version)


@app.post(
    "/api/workspaces/{workspace_key}/sites/{site_id}/query-sets/{query_set_id}/versions",
    response_model=ProcurementQuestionSetVersionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_procurement_question_set_version(
    workspace_key: str,
    site_id: int,
    query_set_id: int,
    payload: ProcurementQuestionSetVersionCreate,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    site = _site_in_workspace(db, site_id, workspace)
    query_set = _question_set_for_site(db, workspace, site, query_set_id)
    if payload.expected_version != query_set.current_version:
        raise HTTPException(409, "question set version changed; refresh before creating a version")
    latest = db.scalar(
        select(ProcurementQuestionSetVersion)
        .where(ProcurementQuestionSetVersion.question_set_id == query_set.id)
        .order_by(ProcurementQuestionSetVersion.version.desc())
    )
    if latest is None or latest.version != query_set.current_version or latest.state != "frozen":
        raise HTTPException(409, "only a frozen current version can be copied")
    updated = db.execute(
        update(ProcurementQuestionSet)
        .where(
            ProcurementQuestionSet.id == query_set.id,
            ProcurementQuestionSet.current_version == payload.expected_version,
        )
        .values(current_version=payload.expected_version + 1)
    )
    if updated.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "question set version changed; refresh before creating a version")
    next_version = ProcurementQuestionSetVersion(
        version=payload.expected_version + 1,
        edit_version=1,
        state="draft",
    )
    query_set.versions.append(next_version)
    next_version.questions = [
        ProcurementQuestion(
            position=question.position,
            question=question.question,
            product=question.product,
            use_case=question.use_case,
            buyer_role=question.buyer_role,
            purchase_stage=question.purchase_stage,
            target_market=question.target_market,
            language=question.language,
            page_mappings=[
                ProcurementQuestionPageMapping(page_id=mapping.page_id, position=mapping.position)
                for mapping in question.page_mappings
            ],
        )
        for question in latest.questions
    ]
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "question set version changed; refresh before creating a version") from exc
    db.refresh(next_version)
    return _question_set_version_out(next_version)


@app.put(
    "/api/workspaces/{workspace_key}/sites/{site_id}/query-sets/{query_set_id}/versions/{version_number}",
    response_model=ProcurementQuestionSetVersionOut,
)
def update_procurement_question_set_version(
    workspace_key: str,
    site_id: int,
    query_set_id: int,
    version_number: int,
    payload: ProcurementQuestionSetVersionUpdate,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    site = _site_in_workspace(db, site_id, workspace)
    query_set = _question_set_for_site(db, workspace, site, query_set_id)
    version = _question_set_version_for_site(db, workspace, site, query_set_id, version_number)
    if version_number != query_set.current_version or version.state != "draft":
        raise HTTPException(409, "only the current draft version can be edited")
    if version.edit_version != payload.expected_version:
        raise HTTPException(409, "question set draft changed; refresh before editing")
    _validate_question_pages(db, site, payload.questions)
    updated = db.execute(
        update(ProcurementQuestionSetVersion)
        .where(
            ProcurementQuestionSetVersion.id == version.id,
            ProcurementQuestionSetVersion.edit_version == payload.expected_version,
            ProcurementQuestionSetVersion.state == "draft",
        )
        .values(edit_version=payload.expected_version + 1)
    )
    if updated.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "question set draft changed; refresh before editing")
    version.questions.clear()
    db.flush()
    _add_question_payloads(db, version, payload.questions)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "question set draft could not be updated") from exc
    db.refresh(version)
    return _question_set_version_out(version)


@app.post(
    "/api/workspaces/{workspace_key}/sites/{site_id}/query-sets/{query_set_id}/versions/{version_number}/freeze",
    response_model=ProcurementQuestionSetVersionOut,
)
def freeze_procurement_question_set_version(
    workspace_key: str,
    site_id: int,
    query_set_id: int,
    version_number: int,
    payload: ProcurementQuestionSetVersionCreate,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    site = _site_in_workspace(db, site_id, workspace)
    query_set = _question_set_for_site(db, workspace, site, query_set_id)
    version = _question_set_version_for_site(db, workspace, site, query_set_id, version_number)
    if version_number != query_set.current_version or version.state != "draft":
        raise HTTPException(409, "only the current draft version can be frozen")
    if version.edit_version != payload.expected_version:
        raise HTTPException(409, "question set draft changed; refresh before freezing")
    if len(version.questions) != 20:
        raise HTTPException(409, "a frozen procurement question set must contain exactly 20 questions")
    if any(not question.page_mappings for question in version.questions):
        raise HTTPException(409, "every procurement question must map to at least one page before freezing")
    frozen_at = utcnow()
    updated = db.execute(
        update(ProcurementQuestionSetVersion)
        .where(
            ProcurementQuestionSetVersion.id == version.id,
            ProcurementQuestionSetVersion.edit_version == payload.expected_version,
            ProcurementQuestionSetVersion.state == "draft",
        )
        .values(state="frozen", frozen_at=frozen_at, edit_version=payload.expected_version + 1)
    )
    if updated.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "question set draft changed; refresh before freezing")
    db.commit()
    db.refresh(version)
    return _question_set_version_out(version)


def _content_generation_task_summary_out(task: ContentGenerationTask) -> ContentGenerationTaskSummaryOut:
    return ContentGenerationTaskSummaryOut(
        id=task.id,
        workspace_id=task.workspace_id,
        site_id=task.site_id,
        question_set_version_id=task.question_set_version_id,
        status=task.status,
        generation_source=task.generation_source,
        attempts=task.attempts,
        last_error=task.last_error,
        created_at=task.created_at,
    )


def _content_generation_task_out(db: Session, task: ContentGenerationTask) -> ContentGenerationTaskOut:
    items: list[ContentGenerationItemOut] = []
    for item in task.items:
        question = db.get(ProcurementQuestion, item.question_id)
        page = db.get(Page, item.page_id)
        change = _change_or_404(db, item.change_request_id)
        if question is None or page is None:
            raise HTTPException(409, "content task source data is no longer available")
        items.append(
            ContentGenerationItemOut(
                id=item.id,
                task_id=item.task_id,
                question_id=item.question_id,
                question=question.question,
                page_id=item.page_id,
                canonical_url=page.canonical_url,
                change_request_id=item.change_request_id,
                snapshot_id=item.snapshot_id,
                snapshot_hash=item.snapshot_hash,
                request_summary=item.request_summary,
                required_fact_ids=json.loads(item.required_fact_ids_json),
                status=item.status,
                thread_id=item.thread_id,
                created_at=item.created_at,
                change_request=_change_out(change),
            )
        )
    return ContentGenerationTaskOut(**_content_generation_task_summary_out(task).model_dump(), items=items)


@app.post(
    "/api/workspaces/{workspace_key}/sites/{site_id}/query-sets/{query_set_id}/versions/{version_number}/content-tasks",
    response_model=ContentGenerationTaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_content_generation_task(
    workspace_key: str,
    site_id: int,
    query_set_id: int,
    version_number: int,
    payload: ContentGenerationTaskCreate,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    site = _site_in_workspace(db, site_id, workspace)
    query_set = _question_set_for_site(db, workspace, site, query_set_id)
    version = _question_set_version_for_site(db, workspace, site, query_set_id, version_number)
    if version_number != query_set.current_version or version.state != "frozen":
        raise HTTPException(409, "content tasks require the current frozen question set version")

    question_ids = {item.question_id for item in payload.items}
    questions = {
        question.id: question
        for question in db.scalars(
            select(ProcurementQuestion).where(
                ProcurementQuestion.question_set_version_id == version.id,
                ProcurementQuestion.id.in_(question_ids),
            )
        ).all()
    }
    if questions.keys() != question_ids:
        raise HTTPException(422, "each question must belong to the selected frozen version")

    prepared: list[tuple[ContentGenerationItemCreate, ProcurementQuestion, Page, PageSnapshot, list[dict[str, object]]]] = []
    for item_payload in payload.items:
        question = questions[item_payload.question_id]
        mapping_id = db.scalar(
            select(ProcurementQuestionPageMapping.id).where(
                ProcurementQuestionPageMapping.question_id == question.id,
                ProcurementQuestionPageMapping.page_id == item_payload.page_id,
            )
        )
        if mapping_id is None:
            raise HTTPException(422, "each selected page must be mapped to its procurement question")

        page = db.scalar(
            select(Page)
            .join(Site, Page.site_id == Site.id)
            .where(Page.id == item_payload.page_id, Site.id == site.id, Site.workspace_id == workspace.id)
        )
        if page is None:
            raise HTTPException(422, "each selected page must belong to the requested workspace and site")
        snapshot = db.scalar(
            select(PageSnapshot)
            .where(PageSnapshot.page_id == page.id)
            .order_by(PageSnapshot.fetched_at.desc(), PageSnapshot.id.desc())
            .limit(1)
        )
        if snapshot is None:
            raise HTTPException(409, "selected page has no current snapshot")
        if (
            item_payload.expected_snapshot_id != snapshot.id
            or item_payload.expected_snapshot_hash != snapshot.content_hash
        ):
            raise HTTPException(409, "selected page snapshot changed; refresh before creating content tasks")

        facts = _validate_facts(
            db,
            workspace,
            [{"fact_id": fact_id} for fact_id in item_payload.required_fact_ids],
        )
        prepared.append((item_payload, question, page, snapshot, facts))

    task = ContentGenerationTask(
        workspace_id=workspace.id,
        site_id=site.id,
        question_set_version_id=version.id,
        status="queued",
        attempts=0,
    )
    db.add(task)
    db.flush()
    for position, (item_payload, question, page, snapshot, facts) in enumerate(prepared, start=1):
        change = ChangeRequest(
            workspace_id=workspace.id,
            site_id=site.id,
            title=f"Content draft for procurement question {question.position}",
            state=ChangeState.draft.value,
            version=1,
        )
        db.add(change)
        db.flush()
        revision = _create_revision(
            db,
            change,
            ChangeRevisionCreate(
                base_snapshot_id=snapshot.id,
                base_content_hash=snapshot.content_hash,
                field_diff={},
                fact_versions=facts,
                expected_version=1,
            ),
            site=site,
        )
        change.revisions.append(revision)
        task.items.append(
            ContentGenerationItem(
                question_id=question.id,
                page_id=page.id,
                change_request_id=change.id,
                snapshot_id=snapshot.id,
                snapshot_hash=snapshot.content_hash,
                request_summary=item_payload.request_summary.strip(),
                required_fact_ids_json=json.dumps(sorted(item_payload.required_fact_ids), separators=(",", ":")),
                status="queued",
                thread_id=f"content-task-{task.id}-item-{position}",
            )
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "content task changed while it was being created") from exc
    task_id = task.id
    db.expire_all()
    task = db.get(ContentGenerationTask, task_id)
    if task is None:
        raise HTTPException(500, "created content task could not be loaded")
    return _content_generation_task_out(db, task)


@app.get(
    "/api/workspaces/{workspace_key}/sites/{site_id}/content-tasks",
    response_model=list[ContentGenerationTaskSummaryOut],
)
def list_content_generation_tasks(workspace_key: str, site_id: int, db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_key)
    site = _site_in_workspace(db, site_id, workspace)
    tasks = db.scalars(
        select(ContentGenerationTask)
        .where(ContentGenerationTask.workspace_id == workspace.id, ContentGenerationTask.site_id == site.id)
        .order_by(ContentGenerationTask.created_at.desc(), ContentGenerationTask.id.desc())
    ).all()
    return [_content_generation_task_summary_out(task) for task in tasks]


@app.get(
    "/api/workspaces/{workspace_key}/sites/{site_id}/content-tasks/{task_id}",
    response_model=ContentGenerationTaskOut,
)
def get_content_generation_task(workspace_key: str, site_id: int, task_id: int, db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_key)
    site = _site_in_workspace(db, site_id, workspace)
    task = db.scalar(
        select(ContentGenerationTask).where(
            ContentGenerationTask.id == task_id,
            ContentGenerationTask.workspace_id == workspace.id,
            ContentGenerationTask.site_id == site.id,
        )
    )
    if task is None:
        raise HTTPException(404, "content task not found")
    return _content_generation_task_out(db, task)


def _job_response(job: Job) -> JobOut:
    return JobOut(id=str(job.id), site_id=str(job.site_id), status=job.status, error=job.error, created_at=job.created_at, started_at=job.started_at, finished_at=job.finished_at, progress=100 if job.status in {JobStatus.succeeded.value, JobStatus.failed.value} else 0, pages_total=1, pages_completed=1 if job.status in {JobStatus.succeeded.value, JobStatus.failed.value} else 0, findings_count=sum(len(snapshot.findings) for snapshot in job.snapshots), completed_at=job.finished_at)


@app.post("/api/sites/{site_id}/audits", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
@app.post("/api/sites/{site_id}/audit", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
def create_audit(site_id: int, db: Session = Depends(get_db)):
    if db.get(Site, site_id) is None:
        raise HTTPException(404, "site not found")
    job = Job(site_id=site_id, status=JobStatus.queued.value)
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_response(job)


@app.post("/api/sites/{site_id}/audit-runs", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def create_audit_run(site_id: str, db: Session = Depends(get_db)):
    if not site_id.isdigit():
        raise HTTPException(404, "site not found")
    return create_audit(int(site_id), db)


@app.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return _job_response(job)


@app.get("/api/audit-runs/{job_id}", response_model=JobOut)
def get_audit_run(job_id: str, db: Session = Depends(get_db)):
    if not job_id.isdigit():
        raise HTTPException(404, "audit run not found")
    return get_job(int(job_id), db)


@app.get("/api/sites/{site_id}/jobs", response_model=list[JobOut])
def list_jobs(site_id: int, db: Session = Depends(get_db)):
    if db.get(Site, site_id) is None:
        raise HTTPException(404, "site not found")
    return [_job_response(job) for job in db.scalars(select(Job).where(Job.site_id == site_id).order_by(Job.created_at.desc())).all()]


def _snapshot_response(snapshot: PageSnapshot) -> SnapshotOut:
    findings = [FindingOut(id=f.id, code=f.code, severity=f.severity, message=f.message, evidence=json.loads(f.evidence_json or "{}")) for f in snapshot.findings]
    rule_results = [
        RuleResultOut(id=item.id, rule_id=item.rule_id, version=item.version, scope=item.scope, status=item.status, severity=item.severity, message=item.message, evidence=json.loads(item.evidence_json or "{}"), remediation_hint=item.remediation_hint)
        for item in sorted(snapshot.rule_results, key=lambda result: result.rule_id)
    ]
    return SnapshotOut(id=snapshot.id, page_id=snapshot.page_id, job_id=snapshot.job_id, url=snapshot.url, status_code=snapshot.status_code, title=snapshot.title, content_hash=snapshot.content_hash, content_type=snapshot.content_type, fetched_at=snapshot.fetched_at, is_synthetic=snapshot.is_synthetic, findings=findings, rule_set_version=snapshot.audit_rule_version, rule_results=rule_results)


@app.get("/api/snapshots/{snapshot_id}", response_model=SnapshotOut)
def get_snapshot(snapshot_id: int, db: Session = Depends(get_db)):
    snapshot = db.get(PageSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(404, "snapshot not found")
    return _snapshot_response(snapshot)


@app.get("/api/sites/{site_id}/snapshots", response_model=list[SnapshotOut])
def list_snapshots(site_id: int, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    if db.get(Site, site_id) is None:
        raise HTTPException(404, "site not found")
    snapshots = db.scalars(select(PageSnapshot).where(PageSnapshot.page.has(Page.site_id == site_id)).order_by(PageSnapshot.fetched_at.desc()).limit(limit)).all()
    return [_snapshot_response(snapshot) for snapshot in snapshots]


@app.get("/api/sites/{site_id}/pages")
def list_pages(site_id: str, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    if not site_id.isdigit() or db.get(Site, int(site_id)) is None:
        raise HTTPException(404, "site not found")
    site_pk = int(site_id)
    snapshots = db.scalars(select(PageSnapshot).where(PageSnapshot.page.has(Page.site_id == site_pk)).order_by(PageSnapshot.fetched_at.desc()).limit(limit)).all()
    return [
        {
            "id": str(snapshot.id),
            "page_id": snapshot.page_id,
            "url": snapshot.url,
            "final_url": snapshot.url,
            "title": snapshot.title,
            "status_code": snapshot.status_code,
            "status": "ok" if snapshot.status_code < 400 else "error",
            "content_hash": snapshot.content_hash,
            "fetched_at": snapshot.fetched_at,
            "is_synthetic": snapshot.is_synthetic,
            "channel": "HTTP",
            "finding_count": len(snapshot.findings),
            "rule_count": len(snapshot.rule_results),
            "rule_problem_count": sum(result.status == "fail" for result in snapshot.rule_results),
            "rule_review_count": sum(result.status == "needs_review" for result in snapshot.rule_results),
            "rule_unknown_count": sum(result.status == "unknown" for result in snapshot.rule_results),
        }
        for snapshot in snapshots
    ]


@app.get("/api/sites/{site_id}/model")
def model_status(site_id: int, db: Session = Depends(get_db)):
    if db.get(Site, site_id) is None:
        raise HTTPException(404, "site not found")
    return {"site_id": site_id, "status": "not_configured", "provider": None}


@app.post("/api/sites/{site_id}/publish")
def publish_site(site_id: int, db: Session = Depends(get_db)):
    if db.get(Site, site_id) is None:
        raise HTTPException(404, "site not found")
    return {"site_id": site_id, "status": "not_configured", "published": False}


@app.get("/api/sites/{site_id}/monitor")
def monitor_site(site_id: int, db: Session = Depends(get_db)):
    if db.get(Site, site_id) is None:
        raise HTTPException(404, "site not found")
    return {"site_id": site_id, "status": "not_configured", "last_job_id": None}


# Change management is deliberately small and provider agnostic.  A future
# publisher can consume the outbox without changing the approval contract.
ALLOWED_CHANGE_FIELDS = frozenset({
    "title", "meta_description", "body", "content", "body_blocks", "faq", "internal_links",
})


def _if_match_version(if_match: str | None) -> int | None:
    if if_match is None:
        return None
    value = if_match.strip()
    if value.startswith("W/"):
        value = value[2:].strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(400, "If-Match must contain a numeric change version") from exc


def _assert_expected_change_version(change: ChangeRequest, expected: int | None, if_match: str | None) -> None:
    header_version = _if_match_version(if_match)
    if expected is not None and expected != change.version:
        raise HTTPException(409, "change version changed; refresh before editing")
    if header_version is not None and header_version != change.version:
        raise HTTPException(409, "change version changed; refresh before editing")


def _require_expected_change_version(expected: int | None, if_match: str | None) -> None:
    if expected is None and if_match is None:
        raise HTTPException(409, "expected_version or If-Match is required")


def _change_workspace(db: Session, key: str | int | None) -> Workspace:
    workspace = _workspace_for_key(db, key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    return workspace


def _site_in_workspace(db: Session, site_id: int, workspace: Workspace) -> Site:
    site = db.scalar(select(Site).where(Site.id == site_id, Site.workspace_id == workspace.id))
    if site is None:
        raise HTTPException(404, "site not found")
    return site


def _validate_field_diff(field_diff: dict[str, object]) -> None:
    unknown = sorted(set(field_diff) - ALLOWED_CHANGE_FIELDS)
    if unknown:
        raise HTTPException(422, f"field diff contains non-whitelisted fields: {', '.join(unknown)}")


def _validate_facts(db: Session, workspace: Workspace, versions: list[dict[str, object]]) -> list[dict[str, object]]:
    summary: list[dict[str, object]] = []
    for item in versions:
        if not isinstance(item, dict):
            raise HTTPException(422, "fact_versions entries must be objects")
        fact_id = item.get("fact_id", item.get("id"))
        series_id = item.get("series_id")
        version = item.get("version")
        fact = None
        if fact_id is not None:
            try:
                fact = db.get(Fact, int(fact_id))
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, "fact_id must be an integer") from exc
        elif series_id is not None and version is not None:
            fact = db.scalar(select(Fact).where(Fact.workspace_id == workspace.id, Fact.series_id == str(series_id), Fact.version == int(version)))
        else:
            raise HTTPException(422, "each fact version needs fact_id or series_id and version")
        if fact is None or fact.workspace_id != workspace.id:
            raise HTTPException(404, "fact not found in workspace")
        if version is not None and int(version) != fact.version:
            raise HTTPException(409, "fact version changed; refresh before editing")
        if fact.visibility != FactVisibility.public.value or not _fact_current(fact):
            raise HTTPException(409, "fact is not a current confirmed public fact")
        summary.append({"fact_id": fact.id, "series_id": fact.series_id, "version": fact.version})
    return sorted(summary, key=lambda item: (int(item["fact_id"]), int(item["version"])))


def _snapshot_binding(db: Session, site: Site, snapshot_id: int | None, content_hash: str | None) -> tuple[int | None, str | None]:
    if snapshot_id is None:
        return None, content_hash
    snapshot = db.scalar(select(PageSnapshot).join(Page).where(PageSnapshot.id == snapshot_id, Page.site_id == site.id))
    if snapshot is None:
        raise HTTPException(404, "base snapshot not found for site")
    if content_hash is not None and content_hash != snapshot.content_hash:
        raise HTTPException(409, "base snapshot content hash changed")
    return snapshot.id, snapshot.content_hash


def _revision_hash(base_snapshot_id: int | None, base_content_hash: str | None, field_diff: dict[str, object], facts: list[dict[str, object]]) -> str:
    encoded = json.dumps({"base_snapshot_id": base_snapshot_id, "base_content_hash": base_content_hash, "field_diff": field_diff, "fact_versions": facts}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _revision_out(revision: ChangeRevision) -> ChangeRevisionOut:
    return ChangeRevisionOut(id=revision.id, change_request_id=revision.change_request_id, revision=revision.revision, state=revision.state, base_snapshot_id=revision.base_snapshot_id, base_content_hash=revision.base_content_hash, field_diff=json.loads(revision.field_diff_json or "{}"), fact_versions=json.loads(revision.fact_versions_json or "[]"), content_hash=revision.content_hash, created_at=revision.created_at)


def _approval_out(approval: ChangeApproval) -> ChangeApprovalOut:
    return ChangeApprovalOut(id=approval.id, change_request_id=approval.change_request_id, revision_id=approval.revision_id, revision_hash=approval.revision_hash, reviewer=approval.reviewer, decision=approval.decision, comment=approval.comment, created_at=approval.created_at)


def _change_out(change: ChangeRequest) -> ChangeRequestOut:
    current = next((item for item in change.revisions if item.id == change.current_revision_id), None)
    approvals = sorted(change.approvals, key=lambda item: item.created_at)
    return ChangeRequestOut(id=change.id, workspace_id=change.workspace_id, site_id=change.site_id, state=change.state, version=change.version, current_revision_id=change.current_revision_id, title=change.title, created_at=change.created_at, updated_at=change.updated_at, revision=_revision_out(current) if current else None, approvals=[_approval_out(item) for item in approvals])


def _enqueue_outbox(db: Session, *, event_type: str, aggregate_id: int | str, idempotency_key: str, payload: dict[str, object]) -> OutboxEvent:
    existing = db.scalar(select(OutboxEvent).where(OutboxEvent.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    event = OutboxEvent(event_type=event_type, aggregate_type="change_request", aggregate_id=str(aggregate_id), idempotency_key=idempotency_key, payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")))
    db.add(event)
    db.flush()
    return event


def _create_revision(db: Session, change: ChangeRequest, payload: ChangeRevisionCreate, *, site: Site) -> ChangeRevision:
    _assert_expected_change_version(change, payload.expected_version, None)
    _validate_field_diff(payload.field_diff)
    snapshot_id, base_hash = _snapshot_binding(db, site, payload.base_snapshot_id, payload.base_content_hash)
    facts = _validate_facts(db, change.workspace, payload.fact_versions)
    computed_hash = _revision_hash(snapshot_id, base_hash, payload.field_diff, facts)
    if payload.content_hash is not None and payload.content_hash != computed_hash:
        raise HTTPException(409, "content_hash does not match the canonical change revision")
    revision_number = change.version if not change.revisions else max(item.revision for item in change.revisions) + 1
    revision = ChangeRevision(change_request_id=change.id, revision=revision_number, state=ChangeState.draft.value, base_snapshot_id=snapshot_id, base_content_hash=base_hash, field_diff_json=json.dumps(payload.field_diff, sort_keys=True, separators=(",", ":")), fact_versions_json=json.dumps(facts, sort_keys=True, separators=(",", ":")), content_hash=computed_hash)
    db.add(revision)
    change.version = revision_number
    change.current_revision_id = None
    change.state = ChangeState.draft.value
    db.flush()
    change.current_revision_id = revision.id
    return revision


def _change_or_404(db: Session, change_id: int, workspace_key: str | int | None = None) -> ChangeRequest:
    change = db.get(ChangeRequest, change_id)
    if change is None:
        raise HTTPException(404, "change request not found")
    if workspace_key is not None:
        workspace = _workspace_for_key(db, workspace_key)
        if workspace is None or change.workspace_id != workspace.id:
            raise HTTPException(404, "change request not found")
    return change


def _create_change(db: Session, workspace: Workspace, site: Site, payload: ChangeRequestCreate) -> ChangeRequest:
    if payload.site_id is not None and payload.site_id != site.id:
        raise HTTPException(409, "site does not match change request route")
    change = ChangeRequest(workspace_id=workspace.id, site_id=site.id, title=payload.title, state=ChangeState.draft.value, version=1)
    db.add(change)
    db.flush()
    _create_revision(db, change, payload, site=site)
    db.commit()
    db.refresh(change)
    return change


@app.post("/api/workspaces/{workspace_key}/sites/{site_id}/changes", response_model=ChangeRequestOut, status_code=status.HTTP_201_CREATED)
def create_change(workspace_key: str, site_id: int, payload: ChangeRequestCreate, db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_key)
    if payload.workspace_id is not None and str(payload.workspace_id) not in {str(workspace.id), workspace.external_id or ""}:
        raise HTTPException(403, "workspace mismatch")
    site = _site_in_workspace(db, site_id, workspace)
    return _change_out(_create_change(db, workspace, site, payload))


@app.post("/api/sites/{site_id}/changes", response_model=ChangeRequestOut, status_code=status.HTTP_201_CREATED)
def create_site_change(site_id: int, payload: ChangeRequestCreate, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(404, "site not found")
    workspace = _change_workspace(db, payload.workspace_id or site.workspace_id)
    if site.workspace_id != workspace.id:
        raise HTTPException(404, "site not found")
    return _change_out(_create_change(db, workspace, site, payload))


@app.get("/api/changes/{change_id}", response_model=ChangeRequestOut)
def get_change(change_id: int, workspace_id: str | None = None, db: Session = Depends(get_db)):
    return _change_out(_change_or_404(db, change_id, workspace_id))


@app.get("/api/workspaces/{workspace_key}/changes/{change_id}", response_model=ChangeRequestOut)
def get_workspace_change(workspace_key: str, change_id: int, db: Session = Depends(get_db)):
    return _change_out(_change_or_404(db, change_id, workspace_key))


@app.post("/api/changes/{change_id}/revisions", response_model=ChangeRequestOut)
def add_change_revision(change_id: int, payload: ChangeRevisionCreate, if_match: str | None = Header(default=None, alias="If-Match"), db: Session = Depends(get_db)):
    change = _change_or_404(db, change_id)
    _require_expected_change_version(payload.expected_version, if_match)
    _assert_expected_change_version(change, payload.expected_version, if_match)
    site = _site_in_workspace(db, change.site_id, change.workspace)
    _create_revision(db, change, payload, site=site)
    db.commit()
    db.refresh(change)
    return _change_out(change)


def _submit_change(db: Session, change: ChangeRequest, expected_version: int | None, if_match: str | None) -> ChangeRequest:
    _require_expected_change_version(expected_version, if_match)
    _assert_expected_change_version(change, expected_version, if_match)
    if change.current_revision_id is None:
        raise HTTPException(409, "change request has no revision")
    if change.state == ChangeState.pending_approval.value:
        revision = db.get(ChangeRevision, change.current_revision_id)
        _enqueue_outbox(db, event_type="change.pending_approval", aggregate_id=change.id, idempotency_key=f"change:{change.id}:revision:{revision.revision}:pending_approval", payload={"change_request_id": change.id, "revision_id": revision.id, "revision_hash": revision.content_hash})
        db.commit()
        db.refresh(change)
        return change
    if change.state != ChangeState.draft.value:
        raise HTTPException(409, "only draft changes can be submitted")
    change.state = ChangeState.pending_approval.value
    revision = db.get(ChangeRevision, change.current_revision_id)
    revision.state = ChangeState.pending_approval.value
    _enqueue_outbox(db, event_type="change.pending_approval", aggregate_id=change.id, idempotency_key=f"change:{change.id}:revision:{revision.revision}:pending_approval", payload={"change_request_id": change.id, "revision_id": revision.id, "revision_hash": revision.content_hash})
    db.commit()
    db.refresh(change)
    return change


@app.post("/api/changes/{change_id}/submit-approval", response_model=ChangeRequestOut)
@app.post("/api/changes/{change_id}/submit", response_model=ChangeRequestOut, include_in_schema=False)
def submit_change(change_id: int, payload: ChangeAction | None = None, expected_version: int | None = None, if_match: str | None = Header(default=None, alias="If-Match"), db: Session = Depends(get_db)):
    requested_version = payload.expected_version if payload and payload.expected_version is not None else expected_version
    return _change_out(_submit_change(db, _change_or_404(db, change_id), requested_version, if_match))


def _decide_change(db: Session, change: ChangeRequest, payload: ChangeApprovalCreate, if_match: str | None) -> ChangeRequest:
    _require_expected_change_version(payload.expected_version, if_match)
    _assert_expected_change_version(change, payload.expected_version, if_match)
    revision = db.get(ChangeRevision, change.current_revision_id)
    if revision is None:
        raise HTTPException(409, "change request has no current revision")
    if payload.revision_id is not None and payload.revision_id != revision.id:
        raise HTTPException(409, "approval revision is stale")
    if payload.revision_hash is not None and payload.revision_hash != revision.content_hash:
        raise HTTPException(409, "approval revision hash is stale")
    existing = db.scalar(select(ChangeApproval).where(ChangeApproval.change_request_id == change.id, ChangeApproval.revision_id == revision.id, ChangeApproval.reviewer == payload.reviewer, ChangeApproval.decision == payload.decision))
    if change.state == payload.decision and existing is not None:
        _enqueue_outbox(db, event_type=f"change.{payload.decision}", aggregate_id=change.id, idempotency_key=f"change:{change.id}:revision:{revision.revision}:{payload.decision}", payload={"change_request_id": change.id, "revision_id": revision.id, "revision_hash": revision.content_hash, "reviewer": payload.reviewer})
        db.commit()
        db.refresh(change)
        return change
    if change.state != ChangeState.pending_approval.value:
        raise HTTPException(409, "change request is not pending approval")
    if existing is None:
        db.add(ChangeApproval(change_request_id=change.id, revision_id=revision.id, revision_hash=revision.content_hash, reviewer=payload.reviewer, decision=payload.decision, comment=payload.comment))
    change.state = payload.decision
    revision.state = payload.decision
    event_type = f"change.{payload.decision}"
    _enqueue_outbox(db, event_type=event_type, aggregate_id=change.id, idempotency_key=f"change:{change.id}:revision:{revision.revision}:{payload.decision}", payload={"change_request_id": change.id, "revision_id": revision.id, "revision_hash": revision.content_hash, "reviewer": payload.reviewer})
    db.commit()
    db.refresh(change)
    return change


@app.post("/api/changes/{change_id}/approval", response_model=ChangeRequestOut)
@app.post("/api/changes/{change_id}/approve", response_model=ChangeRequestOut, include_in_schema=False)
def decide_change(change_id: int, payload: ChangeApprovalCreate, if_match: str | None = Header(default=None, alias="If-Match"), db: Session = Depends(get_db)):
    return _change_out(_decide_change(db, _change_or_404(db, change_id), payload, if_match))


@app.post("/api/changes/{change_id}/reject", response_model=ChangeRequestOut, include_in_schema=False)
def reject_change(change_id: int, payload: ChangeApprovalCreate, if_match: str | None = Header(default=None, alias="If-Match"), db: Session = Depends(get_db)):
    payload.decision = "rejected"
    return _change_out(_decide_change(db, _change_or_404(db, change_id), payload, if_match))


@app.post("/api/changes/{change_id}/publish", response_model=PublicationAttemptOut)
def publish_change(change_id: int, payload: ChangeAction | None = None, expected_version: int | None = None, if_match: str | None = Header(default=None, alias="If-Match"), db: Session = Depends(get_db)):
    change = _change_or_404(db, change_id)
    requested_version = payload.expected_version if payload and payload.expected_version is not None else expected_version
    _require_expected_change_version(requested_version, if_match)
    _assert_expected_change_version(change, requested_version, if_match)
    if change.state != ChangeState.approved.value or change.current_revision_id is None:
        raise HTTPException(409, "only an approved change can be published")
    revision = db.get(ChangeRevision, change.current_revision_id)
    if revision.base_snapshot_id is None or revision.base_content_hash is None:
        raise HTTPException(409, "base snapshot binding is required before publishing")
    base_snapshot = db.get(PageSnapshot, revision.base_snapshot_id)
    if base_snapshot is None or base_snapshot.content_hash != revision.base_content_hash:
        raise HTTPException(409, "base snapshot no longer exists or changed")
    latest_snapshot = db.scalar(select(PageSnapshot).join(Page).where(PageSnapshot.page_id == base_snapshot.page_id).order_by(PageSnapshot.fetched_at.desc(), PageSnapshot.id.desc()))
    if latest_snapshot is None or latest_snapshot.id != base_snapshot.id or latest_snapshot.content_hash != revision.base_content_hash:
        raise HTTPException(409, "base snapshot is no longer the current site snapshot")
    for item in json.loads(revision.fact_versions_json or "[]"):
        fact = db.get(Fact, int(item["fact_id"]))
        if fact is None or fact.workspace_id != change.workspace_id or fact.version != int(item["version"]) or fact.visibility != FactVisibility.public.value or not _fact_current(fact):
            raise HTTPException(409, "bound fact is no longer current, confirmed, and public")
    attempt = PublicationAttempt(change_request_id=change.id, revision_id=revision.id, status="not_configured", error="GitPublisher is not configured")
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return PublicationAttemptOut(id=attempt.id, change_request_id=attempt.change_request_id, revision_id=attempt.revision_id, status=attempt.status, error=attempt.error, created_at=attempt.created_at)
