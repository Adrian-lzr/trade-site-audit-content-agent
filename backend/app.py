from __future__ import annotations

import csv
import ipaddress
import hashlib
import io
import json
import os
import socket
import subprocess
from uuid import uuid4
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .crawler import CrawlError, allowed_paths_json, normalize_path
from .database import get_db, init_db
from .authz import Permission, authorize_workspace, effective_actor
from .knowledge import curated_entries, filter_guidance
from .models import AuditEvent, ChangeApproval, ChangeRequest, ChangeRevision, ChangeState, ContentGenerationItem, ContentGenerationTask, Fact, FactStatus, FactVisibility, Job, JobStatus, OutboxEvent, Page, PageSnapshot, ProcurementQuestion, ProcurementQuestionPageMapping, ProcurementQuestionSet, ProcurementQuestionSetVersion, PublicationAttempt, Site, VisibilityRun, VisibilitySample, Workspace, utcnow
from .observability import request_context_middleware
from .schemas import AuditEventCreate, AuditEventOut, ChangeAction, ChangeApprovalCreate, ChangeApprovalOut, ChangeRequestCreate, ChangeRequestOut, ChangeRevisionCreate, ChangeRevisionOut, ContentGenerationItemCreate, ContentGenerationItemOut, ContentGenerationTaskCreate, ContentGenerationTaskOut, ContentGenerationTaskSummaryOut, DeploymentUpdate, FactCreate, FactOut, FactReview, FindingOut, JobOut, ProcurementQuestionCreate, ProcurementQuestionOut, ProcurementQuestionPageMappingOut, ProcurementQuestionSetCreate, ProcurementQuestionSetOut, ProcurementQuestionSetSummaryOut, ProcurementQuestionSetVersionCreate, ProcurementQuestionSetVersionOut, ProcurementQuestionSetVersionUpdate, PublicationAttemptOut, RollbackRequest, RuleResultOut, SiteCreate, SnapshotOut, VisibilityRunCreate, VisibilityRunOut, VisibilitySampleCapture, WorkspaceCreate, WorkspaceOut
from .visibility_provider import build_visibility_provider, redact_sensitive_text
from .visibility_worker import VisibilityWorker, calculate_visibility_metrics, calculate_workspace_daily_spend


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # Apply the configured evidence retention policy on every API startup.  The
    # Worker repeats the same bounded cleanup so long-running deployments do
    # not depend on an API restart for retention enforcement.
    VisibilityWorker().purge_expired_raw_evidence()
    yield


app = FastAPI(title="Site Audit API", version="0.1.0", lifespan=lifespan)
app.middleware("http")(request_context_middleware)


KNOWLEDGE_ENTRIES = [
    {
        "id": "google-seo-starter",
        "entry_type": "external_guidance",
        "claim_type": "general_content_quality",
        "title": "Google SEO 入门",
        "summary": "内容应独特、实用、面向读者；没有自动获得第一名的秘诀，字数没有理想排名上下限。",
        "scope": "Google 一般 SEO 指引；不保证抓取、收录、排名或询盘。",
        "market_scope": "Google 搜索；通用内容质量边界",
        "source_url": "https://developers.google.com/search/docs/fundamentals/seo-starter-guide",
        "source_date": "2025-12-18",
        "accessed_at": "2026-09-29",
        "valid_until": "revalidate-before-use",
        "license": "文档默认 CC BY 4.0；代码示例 Apache 2.0",
    },
    {
        "id": "google-ai-features",
        "entry_type": "external_guidance",
        "claim_type": "search_surface_boundary",
        "title": "Google AI 搜索功能",
        "summary": "AI 概览和 AI 模式沿用基础 SEO、搜索政策和内容质量要求，没有额外的 AI 专用 Schema。",
        "scope": "仅适用于 Google AI 搜索功能，不能外推到其他平台；满足条件也不保证展示。",
        "market_scope": "Google AI 搜索功能；不外推到其他平台",
        "source_url": "https://developers.google.com/search/docs/appearance/ai-features",
        "source_date": "2025-12-31",
        "accessed_at": "2026-09-29",
        "valid_until": "revalidate-before-use",
        "license": "文档默认 CC BY 4.0；代码示例 Apache 2.0",
    },
    {
        "id": "product-structured-data",
        "entry_type": "external_guidance",
        "claim_type": "structured_data_consistency",
        "title": "商品结构化数据",
        "summary": "结构化数据应与页面可见信息一致；商品展示取决于页面类型和资格条件。",
        "scope": "询价型 B2B 页面需逐项核对展示资格；加标记不等于排名提升。",
        "market_scope": "Google 商品展示；需核对 B2B 询价页资格",
        "source_url": "https://developers.google.com/search/docs/appearance/structured-data/product",
        "source_date": "2025-12-18",
        "accessed_at": "2026-09-29",
        "valid_until": "revalidate-before-use",
        "license": "Google 文档默认 CC BY 4.0；Schema.org 词汇按 CC BY-SA 3.0",
    },
    {
        "id": "w3c-prov-o",
        "entry_type": "external_guidance",
        "claim_type": "provenance_model",
        "title": "来源追踪（PROV-O）",
        "summary": "将资料视为 Entity，将供应商、认证机构和审核人视为 Agent，将采集、核验和修订视为 Activity。",
        "scope": "来源模型不证明来源真实或事实正确，仍需企业审核。",
        "market_scope": "来源建模；不限定市场",
        "source_url": "https://www.w3.org/TR/prov-o/",
        "source_date": "2013-04-30",
        "accessed_at": "2026-09-29",
        "valid_until": "revalidate-before-use",
        "license": "W3C 2023 Document License；复用时保留原文链接、版权和状态",
    },
    {
        "id": "ftc-ad-substantiation",
        "entry_type": "external_guidance",
        "claim_type": "advertising_substantiation",
        "title": "广告事实证据",
        "summary": "客观或隐含的产品声明应在发布前有合理依据；“经测试”“认证”等词会传达相应证据水平。",
        "scope": "FTC 美国消费者保护语境；这是 1984 年政策声明，不直接替代现行指南或其他市场法律判断。",
        "market_scope": "美国 FTC 广告证据语境；发布前复核现行规则",
        "source_url": "https://www.ftc.gov/legal-library/browse/ftc-policy-statement-regarding-advertising-substantiation",
        "source_date": "1984-11-23",
        "accessed_at": "2026-09-29",
        "valid_until": "revalidate-before-use",
        "license": "只保存摘要与链接；使用前重新核验最新规则与执法材料",
    },
    {
        "id": "rfq-page-content-checklist",
        "entry_type": "external_guidance",
        "claim_type": "procurement_completeness_heuristic",
        "title": "询价型 B2B 页面内容最低集",
        "summary": "检查型号与规格、数量/批量、配置与定制、目的地/交付点/日期、包装/文件、MOQ/交期/贸易术语，以及样品/备件/售后需求是否有对应资料；未知商业条件必须列为待补充。",
        "scope": "采购信息完整性启发式与字段映射；缺失字段必须进入资料补充，不得由模型补写，不证明转化、排名或询盘提升。MOQ、交期、运费和响应条件等商业值只能由企业资料支持。",
        "market_scope": "B2B 询价页；字段含义需结合企业事实和目标市场核验",
        "source_url": "https://schema.org/Product",
        "source_date": "continuously maintained",
        "accessed_at": "2026-09-29",
        "valid_until": "revalidate-before-use",
        "license": "Schema.org 词汇按其许可说明；仅保存字段映射与链接",
        "topic_tags": "b2b,rfq,quote,quotation,request-for-quote,product-page,procurement,checklist,quantity,batch,configuration,custom,customize,customization,destination,delivery-point,packaging,documents,sample,samples,spare-parts,spares,service,support,after-sales,lead-time,production,shipping,shipment,freight,moq,minimum-order,minimum-quantity,询价,报价,数量,批量,配置,定制,目的地,交付地点,包装,文件,样品,备件,售后,交期,运输,运费",
    },
    {
        "id": "product-eligibility-boundary",
        "entry_type": "external_guidance",
        "claim_type": "structured_data_eligibility",
        "title": "Product snippets 与 Merchant listings 资格边界",
        "summary": "Product/Offer 标记、Merchant Center feed、价格/库存/购买路径与纯 RFQ 页面资格不同；结构化数据必须与页面可见信息一致。",
        "scope": "只说明 Google 展示资格边界，不保证展示；询价页不得臆造 price、availability 或购买路径。",
        "market_scope": "Google Product snippets 与 Merchant listings",
        "source_url": "https://developers.google.com/search/docs/appearance/structured-data/merchant-listing",
        "source_date": "continuously updated",
        "accessed_at": "2026-09-29",
        "valid_until": "revalidate-before-use",
        "license": "Google 文档默认 CC BY 4.0；使用前核对当前资格要求",
    },
    {
        "id": "technical-signal-interpretation",
        "entry_type": "external_guidance",
        "claim_type": "technical_audit_interpretation",
        "title": "技术审计信号解释",
        "summary": "robots、noindex、canonical、sitemap 分别涉及抓取、索引指令、规范信号和发现入口；网络失败或权限限制应保留为 unknown/needs_review。",
        "scope": "诊断信号解释，不等于处罚、收录或排名结论；不要把 robots 阻断直接写成排名惩罚。",
        "market_scope": "Google 抓取与索引诊断；可迁移的信号解释",
        "source_url": "https://developers.google.com/search/docs/crawling-indexing/robots/intro",
        "source_date": "continuously updated",
        "accessed_at": "2026-09-29",
        "valid_until": "revalidate-before-use",
        "license": "Google 文档默认 CC BY 4.0；RFC 9309 另有其许可说明",
    },
    {
        "id": "internationalized-b2b-pages",
        "entry_type": "external_guidance",
        "claim_type": "internationalization",
        "title": "国际化 B2B 页面与 hreflang",
        "summary": "语言/区域版本应互指，canonical 与语言版本保持一致；目标市场和语言事实需要单独确认。",
        "scope": "hreflang 不能替代翻译、企业市场授权或本地法规判断，也不保证国际排名。",
        "market_scope": "Google 多区域搜索；语言和区域版本",
        "source_url": "https://developers.google.com/search/docs/specialty/international/localized-versions",
        "source_date": "continuously updated",
        "accessed_at": "2026-09-29",
        "valid_until": "revalidate-before-use",
        "license": "Google 文档默认 CC BY 4.0；使用前核对当前实现要求",
    },
    {
        "id": "accessibility-content-usability",
        "entry_type": "external_guidance",
        "claim_type": "accessibility_usability",
        "title": "可访问性与内容可用性检查",
        "summary": "标题层级、表单标签、键盘操作、对比度和错误提示影响询价流程可用性，应与自动审计结果分开记录。",
        "scope": "单次自动检查不能宣称 WCAG 合规，也不能自动推出 SEO 或询盘提升。",
        "market_scope": "Web 可访问性与询价流程可用性",
        "source_url": "https://www.w3.org/TR/WCAG22/",
        "source_date": "2023-10-05",
        "accessed_at": "2026-09-29",
        "valid_until": "revalidate-before-use",
        "license": "W3C 2023 Document License；复用时保留原文链接、版权和状态",
    },
    {
        "id": "source-freshness-fact-separation",
        "entry_type": "external_guidance",
        "claim_type": "source_governance",
        "title": "来源时效与企业事实分层",
        "summary": "外部规则应记录 source_date、accessed_at、valid_until、supersedes；规格、证书、交期和价格只能进工作区 Fact，并保留 reviewer、visibility 和版本。",
        "scope": "外部条目不能填充缺失企业事实；持续更新页面的日期必须重新核验，不能伪造为永久有效。",
        "market_scope": "工作区事实治理与外部来源维护",
        "source_url": "https://www.w3.org/TR/prov-o/",
        "source_date": "2013-04-30",
        "accessed_at": "2026-09-29",
        "valid_until": "revalidate-before-use",
        "license": "W3C 2023 Document License；来源模型不等于事实核验",
    },
]


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {"status": "ok", "mode": "http", "fixture": False, "fixture_available": _fixture_available(), "version": "0.1.0"}


def _fixture_available() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", settings.fixture_port), timeout=0.25):
            return True
    except OSError:
        return False


@app.get("/api/health")
def api_health() -> dict[str, str | bool]:
    return health()


@app.get("/api/knowledge")
def list_knowledge_entries(
    market: str | None = Query(default=None, min_length=1, max_length=120),
    claim_type: str | None = Query(default=None, min_length=1, max_length=120),
) -> list[dict[str, object]]:
    """Return curated guidance; these entries are never treated as enterprise facts."""

    return filter_guidance(curated_entries(KNOWLEDGE_ENTRIES), market=market, claim_type=claim_type)


@app.post("/api/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate, db: Session = Depends(get_db)):
    workspace = Workspace(name=payload.name)
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return workspace


@app.post("/api/workspaces/{workspace_key}/audit-events", response_model=AuditEventOut, status_code=status.HTTP_201_CREATED)
def append_audit_event(workspace_key: str, payload: AuditEventCreate, request: Request, db: Session = Depends(get_db)):
    """Append an event, binding the actor to the local identity when supplied."""
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    identity = authorize_workspace(request, db, workspace, Permission.operate)
    event = AuditEvent(
        workspace_id=workspace.id,
        actor=effective_actor(identity),
        action=payload.action,
        target_type=payload.target_type,
        target_id=payload.target_id,
        before_version_json=json.dumps(payload.before_version, sort_keys=True, separators=(",", ":")),
        after_version_json=json.dumps(payload.after_version, sort_keys=True, separators=(",", ":")),
        run_id=payload.run_id,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return {
        "id": event.id, "workspace_id": event.workspace_id, "actor": event.actor,
        "action": event.action, "target_type": event.target_type, "target_id": event.target_id,
        "before_version": json.loads(event.before_version_json), "after_version": json.loads(event.after_version_json),
        "run_id": event.run_id, "created_at": event.created_at,
    }


@app.get("/api/workspaces/{workspace_key}/audit-events", response_model=list[AuditEventOut])
def list_audit_events(workspace_key: str, request: Request, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.read)
    events = db.scalars(select(AuditEvent).where(AuditEvent.workspace_id == workspace.id).order_by(AuditEvent.id)).all()
    return [
        {"id": e.id, "workspace_id": e.workspace_id, "actor": e.actor, "action": e.action,
         "target_type": e.target_type, "target_id": e.target_id,
         "before_version": json.loads(e.before_version_json), "after_version": json.loads(e.after_version_json),
         "run_id": e.run_id, "created_at": e.created_at}
        for e in events
    ]


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


def _fact_workspace_or_404(db: Session, fact_id: int, workspace_key: str | int) -> Fact:
    fact = db.get(Fact, fact_id)
    if fact is None:
        raise HTTPException(404, "fact not found")
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None or fact.workspace_id != workspace.id:
        raise HTTPException(404, "fact not found")
    return fact


def _build_fact(db: Session, payload: FactCreate, workspace: Workspace) -> Fact:
    parent = None
    if payload.parent_id is not None:
        parent = db.get(Fact, payload.parent_id)
        if parent is None or parent.workspace_id != workspace.id:
            raise HTTPException(404, "parent fact not found")
    series_id = payload.series_id or (parent.series_id if parent else None)
    if series_id is None:
        series_id = uuid4().hex
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
    db.flush()
    return fact


def _import_fact(db: Session, payload: FactCreate, workspace: Workspace) -> Fact:
    try:
        fact = _build_fact(db, payload, workspace)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "fact version already exists") from exc
    db.refresh(fact)
    return fact


@app.post("/api/workspaces/{workspace_key}/facts", response_model=FactOut, status_code=status.HTTP_201_CREATED)
def import_workspace_fact(workspace_key: str, payload: FactCreate, request: Request, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.operate)
    if payload.workspace_id is not None and str(payload.workspace_id) != str(workspace.id) and str(payload.workspace_id) != (workspace.external_id or ""):
        raise HTTPException(403, "workspace mismatch")
    return _fact_response(_import_fact(db, payload, workspace))


@app.post("/api/workspaces/{workspace_key}/facts/import-csv", response_model=list[FactOut], status_code=status.HTTP_201_CREATED)
async def import_workspace_facts_csv(workspace_key: str, request: Request, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.operate)

    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 1_000_000:
            raise HTTPException(413, "CSV file must be 1 MB or smaller")
        raw.extend(chunk)
    try:
        csv_text = bytes(raw).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "CSV file must use UTF-8 encoding") from exc

    reader = csv.DictReader(io.StringIO(csv_text, newline=""))
    headers = [header.strip() if header else "" for header in (reader.fieldnames or [])]
    required_headers = {"subject", "predicate", "value", "source_id", "source_locator"}
    allowed_headers = required_headers | {"unit", "visibility", "valid_from", "valid_until"}
    if not headers or any(not header for header in headers) or len(set(headers)) != len(headers):
        raise HTTPException(422, "CSV header row is empty or contains duplicate columns")
    missing = sorted(required_headers - set(headers))
    unknown = sorted(set(headers) - allowed_headers)
    if missing or unknown:
        raise HTTPException(422, {"missing_columns": missing, "unknown_columns": unknown})
    reader.fieldnames = headers

    rows: list[tuple[int, FactCreate]] = []
    try:
        for row_number, row in enumerate(reader, start=2):
            if None in row and any((value or "").strip() for value in row[None]):
                raise HTTPException(422, {"row": row_number, "message": "row has more values than the header"})
            values = {key: (value or "").strip() for key, value in row.items() if isinstance(key, str)}
            if not any(values.values()):
                continue
            if len(rows) >= 200:
                raise HTTPException(422, {"row": row_number, "message": "CSV may contain at most 200 facts"})
            values["visibility"] = values.get("visibility") or FactVisibility.internal_only.value
            for optional_field in ("unit", "valid_from", "valid_until"):
                values[optional_field] = values.get(optional_field) or None
            try:
                payload = FactCreate(**values)
            except ValidationError as exc:
                errors = [
                    {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
                    for error in exc.errors()
                ]
                raise HTTPException(422, {"row": row_number, "errors": errors}) from exc
            rows.append((row_number, payload))
    except csv.Error as exc:
        raise HTTPException(422, f"invalid CSV format: {exc}") from exc

    if not rows:
        raise HTTPException(422, "CSV contains no fact rows")

    facts: list[Fact] = []
    try:
        for row_number, payload in rows:
            try:
                facts.append(_build_fact(db, payload, workspace))
            except HTTPException as exc:
                db.rollback()
                raise HTTPException(exc.status_code, {"row": row_number, "message": exc.detail}) from exc
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "fact batch conflicts with existing versions; no rows were imported") from exc
    except HTTPException:
        db.rollback()
        raise

    for fact in facts:
        db.refresh(fact)
    return [_fact_response(fact) for fact in facts]


@app.post("/api/facts", response_model=FactOut, status_code=status.HTTP_201_CREATED)
def import_fact(payload: FactCreate, request: Request, db: Session = Depends(get_db)):
    if payload.workspace_id is None:
        raise HTTPException(422, "workspace_id is required")
    workspace = _workspace_for_key(db, payload.workspace_id)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.operate)
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
def list_workspace_facts(workspace_key: str, request: Request, subject: str | None = None, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.read)
    return _list_facts(db, workspace, public=False, subject=subject)


@app.get("/api/facts", response_model=list[FactOut])
def list_facts(workspace_id: str, request: Request, subject: str | None = None, public: bool = False, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_id)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.read)
    return _list_facts(db, workspace, public=public, subject=subject)


@app.get("/api/workspaces/{workspace_key}/facts/public", response_model=list[FactOut])
def list_public_workspace_facts(workspace_key: str, request: Request, subject: str | None = None, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.read)
    return _list_facts(db, workspace, public=True, subject=subject)


@app.get("/api/facts/public", response_model=list[FactOut])
def list_public_facts(workspace_id: str, request: Request, subject: str | None = None, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_id)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.read)
    return _list_facts(db, workspace, public=True, subject=subject)


@app.get("/api/workspaces/{workspace_key}/facts/{fact_id}", response_model=FactOut)
def get_workspace_fact(workspace_key: str, fact_id: int, request: Request, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.read)
    fact = _fact_workspace_or_404(db, fact_id, workspace_key)
    return _fact_response(fact)


@app.get("/api/facts/{fact_id}", response_model=FactOut)
def get_fact(fact_id: int, workspace_id: str, request: Request, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_id)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.read)
    return _fact_response(_fact_workspace_or_404(db, fact_id, workspace_id))


def _review_fact(
    db: Session,
    fact: Fact,
    payload: FactReview,
    *,
    target_status: FactStatus,
    reviewer: str | None = None,
) -> Fact:
    if payload.expected_version is not None and payload.expected_version != fact.version:
        raise HTTPException(409, "fact version changed; refresh before reviewing")
    if fact.status != FactStatus.proposed.value:
        raise HTTPException(409, "only proposed facts can be reviewed")
    fact.status = target_status.value
    fact.reviewer = reviewer or payload.reviewer
    fact.reviewed_at = utcnow()
    db.commit()
    db.refresh(fact)
    return fact


@app.post("/api/workspaces/{workspace_key}/facts/{fact_id}/confirm", response_model=FactOut)
@app.post("/api/facts/{fact_id}/confirm", response_model=FactOut)
@app.post("/api/workspaces/{workspace_key}/facts/{fact_id}/approve", response_model=FactOut, include_in_schema=False)
@app.post("/api/facts/{fact_id}/approve", response_model=FactOut, include_in_schema=False)
def confirm_fact(
    fact_id: int,
    payload: FactReview,
    request: Request,
    workspace_key: str | None = None,
    workspace_id: str | None = None,
    db: Session = Depends(get_db),
):
    key = workspace_key or workspace_id
    if key is None:
        raise HTTPException(422, "workspace_id is required")
    workspace = _workspace_for_key(db, key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    identity = authorize_workspace(request, db, workspace, Permission.review)
    fact = _fact_workspace_or_404(db, fact_id, key)
    return _fact_response(
        _review_fact(
            db,
            fact,
            payload,
            target_status=FactStatus.confirmed,
            reviewer=effective_actor(identity),
        )
    )


@app.post("/api/workspaces/{workspace_key}/facts/{fact_id}/reject", response_model=FactOut)
@app.post("/api/facts/{fact_id}/reject", response_model=FactOut)
def reject_fact(
    fact_id: int,
    payload: FactReview,
    request: Request,
    workspace_key: str | None = None,
    workspace_id: str | None = None,
    db: Session = Depends(get_db),
):
    key = workspace_key or workspace_id
    if key is None:
        raise HTTPException(422, "workspace_id is required")
    workspace = _workspace_for_key(db, key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    identity = authorize_workspace(request, db, workspace, Permission.review)
    fact = _fact_workspace_or_404(db, fact_id, key)
    return _fact_response(
        _review_fact(
            db,
            fact,
            payload,
            target_status=FactStatus.rejected,
            reviewer=effective_actor(identity),
        )
    )


@app.post("/api/sites", status_code=status.HTTP_201_CREATED)
def register_site(payload: SiteCreate, request: Request, db: Session = Depends(get_db)):
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
    authorize_workspace(request, db, workspace, Permission.operate)
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
def get_site(site_id: int, workspace_id: str, request: Request, db: Session = Depends(get_db)):
    workspace, site = _workspace_site_or_404(db, workspace_id, site_id)
    authorize_workspace(request, db, workspace, Permission.read)
    return {"id": str(site.id), "workspace_id": site.workspace.external_id or str(site.workspace_id), "name": site.name, "origin": site.base_url, "base_url": site.base_url, "allowed_paths": json.loads(site.allowed_paths), "is_synthetic": site.is_synthetic, "status": "ready", "created_at": site.created_at}


@app.get("/api/sites")
def list_sites(workspace_id: str, request: Request, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_id)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.read)
    query = select(Site).join(Site.workspace)
    if workspace_id:
        if workspace_id.isdigit():
            query = query.where(Site.workspace_id == int(workspace_id))
        else:
            query = query.where(Workspace.external_id == workspace_id)
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
    request: Request,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.operate)
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
def list_procurement_question_sets(workspace_key: str, site_id: int, request: Request, db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.read)
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
    request: Request,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.read)
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
    request: Request,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.read)
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
    request: Request,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.operate)
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
    request: Request,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.operate)
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
    request: Request,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.operate)
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
    _, task_site = _workspace_site_or_404(db, str(task.workspace_id), task.site_id)
    version = db.get(ProcurementQuestionSetVersion, task.question_set_version_id)
    question_set = db.get(ProcurementQuestionSet, version.question_set_id) if version else None
    if version is None or version.state != "frozen" or question_set is None or question_set.workspace_id != task.workspace_id or question_set.site_id != task.site_id:
        raise HTTPException(409, "content task question set is outside its workspace and site")
    for item in task.items:
        question = db.get(ProcurementQuestion, item.question_id)
        page = db.get(Page, item.page_id)
        snapshot = db.get(PageSnapshot, item.snapshot_id)
        change = _change_or_404(db, item.change_request_id, task.workspace_id)
        if (
            question is None or question.question_set_version_id != version.id
            or page is None or page.site_id != task_site.id
            or snapshot is None or snapshot.page_id != page.id or snapshot.content_hash != item.snapshot_hash
            or change.site_id != task_site.id
        ):
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
    request: Request,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.operate)
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
def list_content_generation_tasks(workspace_key: str, site_id: int, request: Request, db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.read)
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
def get_content_generation_task(workspace_key: str, site_id: int, task_id: int, request: Request, db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.read)
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


def _json_array(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed if str(item).strip()] if isinstance(parsed, list) else []


def _json_object(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _visibility_sample_out(sample: VisibilitySample, question: ProcurementQuestion | None = None) -> dict[str, object]:
    question = question or sample.question
    return {
        "id": sample.id,
        "run_id": sample.run_id,
        "question_id": sample.question_id,
        "position": sample.position,
        "question": question.question if question is not None else "",
        "brand_query": sample.brand_query,
        "status": sample.status,
        "raw_response": sample.raw_response,
        "answer_text": sample.answer_text,
        "citations": _json_array(sample.citations_json),
        "mentioned_domains": _json_array(sample.mentioned_domains_json),
        "provider_request_id": sample.provider_request_id,
        "request_id": sample.request_id,
        "model": sample.model,
        "input_tokens": sample.input_tokens,
        "output_tokens": sample.output_tokens,
        "cost_usd": sample.cost_usd,
        "estimated_cost_usd": sample.estimated_cost_usd,
        "error_code": sample.error_code,
        "error_message": sample.error_message,
        "completed_at": sample.completed_at,
        "created_at": sample.created_at,
    }


def _visibility_run_out(db: Session, run: VisibilityRun, site: Site | None = None) -> VisibilityRunOut:
    site = site or db.scalar(select(Site).where(Site.id == run.site_id, Site.workspace_id == run.workspace_id))
    if site is None:
        raise HTTPException(409, "visibility run site is no longer available")
    question_ids = [sample.question_id for sample in run.samples]
    questions = {
        question.id: question
        for question in db.scalars(
            select(ProcurementQuestion).where(
                ProcurementQuestion.question_set_version_id == run.question_set_version_id,
                ProcurementQuestion.id.in_(question_ids or [-1]),
            )
        ).all()
    }
    try:
        capability = json.loads(run.capability_json or "{}")
    except (TypeError, ValueError):
        capability = {}
    return VisibilityRunOut(
        id=run.id,
        workspace_id=run.workspace_id,
        site_id=run.site_id,
        question_set_id=run.question_set_id,
        question_set_version_id=run.question_set_version_id,
        provider=run.provider,
        provider_kind=run.provider_kind,
        provider_model=run.provider_model,
        provider_config_version=run.provider_config_version,
        prompt_version=run.prompt_version,
        pricing_basis=_json_object(run.pricing_basis_json),
        request_id=run.request_id,
        status=run.status,
        is_synthetic=run.is_synthetic,
        market=run.market,
        language=run.language,
        capability=capability if isinstance(capability, dict) else {},
        idempotency_key=run.idempotency_key,
        brand_terms=_json_array(run.brand_terms_json),
        budget_usd=run.budget_usd,
        planned_samples=run.planned_samples,
        successful_samples=run.successful_samples,
        failed_samples=run.failed_samples,
        total_cost_usd=run.total_cost_usd,
        reserved_cost_usd=run.reserved_cost_usd,
        attempts=run.attempts,
        error=run.error,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        metrics=calculate_visibility_metrics(run, site),
        samples=[_visibility_sample_out(sample, questions.get(sample.question_id)) for sample in run.samples],
    )


def _visibility_run_for_site(db: Session, workspace_key: str, site_id: int, run_id: int) -> tuple[Workspace, Site, VisibilityRun]:
    workspace = _change_workspace(db, workspace_key)
    site = _site_in_workspace(db, site_id, workspace)
    run = db.scalar(
        select(VisibilityRun).where(
            VisibilityRun.id == run_id,
            VisibilityRun.workspace_id == workspace.id,
            VisibilityRun.site_id == site.id,
        )
    )
    if run is None:
        raise HTTPException(404, "visibility run not found")
    return workspace, site, run


def _visibility_question_set_for_payload(db: Session, workspace: Workspace, site: Site, payload: VisibilityRunCreate) -> tuple[ProcurementQuestionSet, ProcurementQuestionSetVersion]:
    if payload.question_set_version_id is not None:
        version = db.get(ProcurementQuestionSetVersion, payload.question_set_version_id)
        question_set = db.get(ProcurementQuestionSet, version.question_set_id) if version else None
        if version is None or question_set is None or question_set.workspace_id != workspace.id or question_set.site_id != site.id:
            raise HTTPException(404, "question set version not found")
        if payload.question_set_id is not None and payload.question_set_id != question_set.id:
            raise HTTPException(422, "question_set_id does not match question_set_version_id")
        if payload.question_set_version is not None and payload.question_set_version != version.version:
            raise HTTPException(422, "question_set_version does not match question_set_version_id")
        return question_set, version
    if payload.question_set_id is not None:
        question_set = db.get(ProcurementQuestionSet, payload.question_set_id)
        if question_set is None or question_set.workspace_id != workspace.id or question_set.site_id != site.id:
            raise HTTPException(404, "question set not found")
        version = _question_set_version_for_site(db, workspace, site, question_set.id, payload.version_number or 0)
        return question_set, version
    matches = db.scalars(
        select(ProcurementQuestionSetVersion)
        .join(ProcurementQuestionSet)
        .where(
            ProcurementQuestionSet.workspace_id == workspace.id,
            ProcurementQuestionSet.site_id == site.id,
            ProcurementQuestionSetVersion.version == payload.version_number,
        )
    ).all()
    if len(matches) != 1:
        raise HTTPException(422, "question_set_id is required when the version number is ambiguous or missing")
    version = matches[0]
    question_set = db.get(ProcurementQuestionSet, version.question_set_id)
    if question_set is None:
        raise HTTPException(404, "question set not found")
    return question_set, version


@app.post(
    "/api/workspaces/{workspace_key}/sites/{site_id}/visibility-runs",
    response_model=VisibilityRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_visibility_run(
    workspace_key: str,
    site_id: int,
    payload: VisibilityRunCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.operate)
    site = _site_in_workspace(db, site_id, workspace)
    question_set, version = _visibility_question_set_for_payload(db, workspace, site, payload)
    if version.state != "frozen":
        raise HTTPException(409, "visibility runs require a frozen question set version")
    questions = list(
        db.scalars(
            select(ProcurementQuestion)
            .where(ProcurementQuestion.question_set_version_id == version.id)
            .order_by(ProcurementQuestion.position, ProcurementQuestion.id)
        ).all()
    )
    if not questions:
        raise HTTPException(422, "visibility runs require at least one procurement question")
    selected_questions = questions[: payload.max_samples] if payload.max_samples else questions
    if payload.idempotency_key:
        existing = db.scalar(select(VisibilityRun).where(VisibilityRun.idempotency_key == payload.idempotency_key))
        if existing is not None:
            if existing.workspace_id != workspace.id or existing.site_id != site.id:
                raise HTTPException(409, "visibility run idempotency key belongs to another workspace or site")
            return _visibility_run_out(db, existing, site)
    if settings.visibility_max_run_budget_usd > 0 and payload.budget_usd > settings.visibility_max_run_budget_usd:
        raise HTTPException(422, "visibility run budget exceeds the configured per-run limit")
    if settings.visibility_daily_budget_usd > 0:
        if payload.budget_usd > settings.visibility_daily_budget_usd:
            raise HTTPException(422, "visibility run budget exceeds the configured workspace daily limit")
        if calculate_workspace_daily_spend(db, workspace.id) >= settings.visibility_daily_budget_usd:
            raise HTTPException(429, "workspace daily visibility budget is exhausted")
    try:
        provider = build_visibility_provider(payload.provider, kind=payload.provider_kind, model=payload.provider_model)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    provider_capabilities = provider.capabilities()
    if isinstance(provider_capabilities, dict):
        provider_capabilities = {
            **provider_capabilities,
            "budget_limits": {
                "per_run_usd": str(settings.visibility_max_run_budget_usd),
                "workspace_daily_usd": str(settings.visibility_daily_budget_usd),
            },
        }
    run = VisibilityRun(
        workspace_id=workspace.id,
        site_id=site.id,
        question_set_id=question_set.id,
        question_set_version_id=version.id,
        provider=provider.name,
        provider_kind=provider.kind,
        provider_model=payload.provider_model or provider.model,
        provider_config_version=(payload.provider_config_version or os.getenv("VISIBILITY_PROVIDER_CONFIG_VERSION", "visibility-provider-v1")).strip(),
        prompt_version=(payload.prompt_version or os.getenv("VISIBILITY_PROMPT_VERSION", "visibility-prompt-v1")).strip(),
        pricing_basis_json=json.dumps(
            (provider.pricing_basis() if callable(getattr(provider, "pricing_basis", None)) else {}),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        request_id=str(uuid4()),
        status="queued",
        is_synthetic=provider.is_synthetic,
        market=payload.market.strip(),
        language=payload.language.strip(),
        capability_json=json.dumps(provider_capabilities, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        idempotency_key=payload.idempotency_key,
        brand_terms_json=json.dumps(payload.brand_terms, ensure_ascii=True, separators=(",", ":")),
        budget_usd=f"{payload.budget_usd:.6f}",
        planned_samples=len(selected_questions),
        total_cost_usd="0.000000",
    )
    run.samples = [
        VisibilitySample(
            question_id=question.id,
            position=question.position,
            brand_query=any(term.casefold() in question.question.casefold() for term in payload.brand_terms),
            status="failed",
            error_code="queued",
            error_message="sample has not been attempted",
            request_id=str(uuid4()),
        )
        for question in selected_questions
    ]
    db.add(run)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if payload.idempotency_key:
            existing = db.scalar(select(VisibilityRun).where(VisibilityRun.idempotency_key == payload.idempotency_key))
            if existing is not None and existing.workspace_id == workspace.id and existing.site_id == site.id:
                return _visibility_run_out(db, existing, site)
        raise HTTPException(409, "visibility run conflicts with an existing idempotency key") from exc
    db.refresh(run)
    return _visibility_run_out(db, run, site)


@app.get(
    "/api/workspaces/{workspace_key}/sites/{site_id}/visibility-runs",
    response_model=list[VisibilityRunOut],
)
def list_visibility_runs(
    workspace_key: str,
    site_id: int,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.read)
    site = _site_in_workspace(db, site_id, workspace)
    runs = db.scalars(
        select(VisibilityRun)
        .where(VisibilityRun.workspace_id == workspace.id, VisibilityRun.site_id == site.id)
        .order_by(VisibilityRun.created_at.desc(), VisibilityRun.id.desc())
        .limit(limit)
    ).all()
    return [_visibility_run_out(db, run, site) for run in runs]


@app.get(
    "/api/workspaces/{workspace_key}/sites/{site_id}/visibility-runs/{run_id}",
    response_model=VisibilityRunOut,
)
def get_visibility_run(workspace_key: str, site_id: int, run_id: int, request: Request, db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.read)
    _, site, run = _visibility_run_for_site(db, workspace_key, site_id, run_id)
    return _visibility_run_out(db, run, site)


@app.post(
    "/api/workspaces/{workspace_key}/sites/{site_id}/visibility-runs/{run_id}/execute",
    response_model=VisibilityRunOut,
)
def execute_visibility_run(workspace_key: str, site_id: int, run_id: int, request: Request, db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.operate)
    _, site, run = _visibility_run_for_site(db, workspace_key, site_id, run_id)
    if run.status == "running":
        raise HTTPException(409, "visibility run is already running")
    if run.status in {"succeeded", "partial"} and run.attempts >= 3:
        return _visibility_run_out(db, run, site)
    if run.status in {"failed", "partial"}:
        run.status = "queued"
        run.error = None
        db.commit()
    worker = VisibilityWorker()
    worker.run_once(run_id=run.id)
    db.expire_all()
    run = db.get(VisibilityRun, run.id)
    return _visibility_run_out(db, run, site)


@app.post(
    "/api/workspaces/{workspace_key}/sites/{site_id}/visibility-runs/{run_id}/samples/{question_id}/capture",
    response_model=VisibilityRunOut,
)
def capture_visibility_sample(
    workspace_key: str,
    site_id: int,
    run_id: int,
    question_id: int,
    payload: VisibilitySampleCapture,
    request: Request,
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.operate)
    _, site, run = _visibility_run_for_site(db, workspace_key, site_id, run_id)
    if run.provider_kind != "manual_capture":
        raise HTTPException(409, "manual captures require a manual_capture provider")
    if payload.question_id != question_id:
        raise HTTPException(422, "question_id in path and body must match")
    sample = db.scalar(select(VisibilitySample).where(VisibilitySample.run_id == run.id, VisibilitySample.question_id == question_id))
    if sample is None:
        raise HTTPException(404, "visibility question sample not found")
    question = db.get(ProcurementQuestion, question_id)
    if question is None or question.question_set_version_id != run.question_set_version_id:
        raise HTTPException(404, "visibility question not found")
    sample.status = "succeeded"
    sample.raw_response = redact_sensitive_text(
        payload.raw_response or payload.answer_text,
        max_bytes=settings.raw_evidence_max_bytes,
    )
    sample.answer_text = redact_sensitive_text(payload.answer_text.strip(), max_bytes=settings.raw_evidence_max_bytes)
    sample.citations_json = json.dumps(
        [redact_sensitive_text(item, max_bytes=settings.raw_evidence_max_bytes) or "" for item in payload.citations],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    sample.mentioned_domains_json = json.dumps(
        [redact_sensitive_text(item, max_bytes=settings.raw_evidence_max_bytes) or "" for item in payload.mentioned_domains],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    if not sample.request_id:
        sample.request_id = str(uuid4())
    sample.model = redact_sensitive_text(payload.model or run.provider_model, max_bytes=200)
    sample.provider_request_id = redact_sensitive_text(payload.provider_request_id, max_bytes=256)
    sample.error_code = None
    sample.error_message = None
    sample.completed_at = utcnow()
    samples = list(db.scalars(select(VisibilitySample).where(VisibilitySample.run_id == run.id)).all())
    run.successful_samples = sum(item.status == "succeeded" for item in samples)
    run.failed_samples = len(samples) - run.successful_samples
    run.status = "succeeded" if run.failed_samples == 0 else ("partial" if run.successful_samples else "failed")
    run.completed_at = utcnow() if run.failed_samples == 0 else None
    db.commit()
    db.refresh(run)
    return _visibility_run_out(db, run, site)


def _job_response(job: Job) -> JobOut:
    """Expose progress using the configured crawl budget and actual snapshots.

    A crawl may capture fewer pages than its budget (for example when the
    site has no more in-scope links). Once the worker reaches a terminal state,
    the captured snapshot count is therefore the meaningful total. During
    execution the configured site limit gives clients a stable denominator.
    """
    terminal = job.status in {JobStatus.succeeded.value, JobStatus.failed.value}
    captured_pages = len(job.snapshots)
    configured_limit = getattr(getattr(job, "site", None), "audit_page_limit", None) or 1
    configured_limit = max(1, min(int(configured_limit), 50))
    pages_total = captured_pages if terminal else configured_limit
    pages_completed = captured_pages if terminal else min(captured_pages, pages_total)
    progress = 100 if terminal else (round(pages_completed * 100 / pages_total) if pages_total else 0)
    return JobOut(
        id=str(job.id),
        site_id=str(job.site_id),
        status=job.status,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        progress=progress,
        pages_total=pages_total,
        pages_completed=pages_completed,
        findings_count=sum(len(snapshot.findings) for snapshot in job.snapshots),
        completed_at=job.finished_at,
    )


def _workspace_site_or_404(db: Session, workspace_key: str, site_id: int) -> tuple[Workspace, Site]:
    """Resolve a site only when it belongs to the requested workspace."""
    workspace = _workspace_for_key(db, workspace_key)
    site = db.get(Site, site_id)
    if workspace is None or site is None or site.workspace_id != workspace.id:
        raise HTTPException(404, "site not found")
    return workspace, site


def _workspace_job_or_404(db: Session, workspace_key: str, job_id: int) -> Job:
    workspace = _workspace_for_key(db, workspace_key)
    job = db.get(Job, job_id)
    if workspace is None or job is None:
        raise HTTPException(404, "job not found")
    site = db.get(Site, job.site_id)
    if site is None or site.workspace_id != workspace.id:
        raise HTTPException(404, "job not found")
    return job


def _workspace_snapshot_or_404(db: Session, workspace_key: str, snapshot_id: int) -> PageSnapshot:
    workspace = _workspace_for_key(db, workspace_key)
    snapshot = db.get(PageSnapshot, snapshot_id)
    if workspace is None or snapshot is None:
        raise HTTPException(404, "snapshot not found")
    page = db.get(Page, snapshot.page_id)
    site = db.get(Site, page.site_id) if page is not None else None
    if site is None or site.workspace_id != workspace.id:
        raise HTTPException(404, "snapshot not found")
    return snapshot


def _job_for_site(site: Site, db: Session) -> Job:
    job = Job(site_id=site.id, status=JobStatus.queued.value)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@app.post("/api/sites/{site_id}/audits", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
@app.post("/api/sites/{site_id}/audit", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
def create_audit(site_id: int, workspace_id: str, request: Request, db: Session = Depends(get_db)):
    workspace, site = _workspace_site_or_404(db, workspace_id, site_id)
    authorize_workspace(request, db, workspace, Permission.operate)
    return _job_response(_job_for_site(site, db))


@app.post("/api/sites/{site_id}/audit-runs", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def create_audit_run(site_id: str, workspace_id: str, request: Request, db: Session = Depends(get_db)):
    if not site_id.isdigit():
        raise HTTPException(404, "site not found")
    return create_audit(int(site_id), workspace_id, request, db)


@app.post("/api/workspaces/{workspace_key}/sites/{site_id}/audits", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
@app.post("/api/workspaces/{workspace_key}/sites/{site_id}/audit-runs", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def create_workspace_audit_run(workspace_key: str, site_id: int, request: Request, db: Session = Depends(get_db)):
    workspace, site = _workspace_site_or_404(db, workspace_key, site_id)
    authorize_workspace(request, db, workspace, Permission.operate)
    return _job_response(_job_for_site(site, db))


@app.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, workspace_id: str, request: Request, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_id)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.read)
    job = _workspace_job_or_404(db, workspace_id, job_id)
    return _job_response(job)


@app.get("/api/audit-runs/{job_id}", response_model=JobOut)
def get_audit_run(job_id: str, workspace_id: str, request: Request, db: Session = Depends(get_db)):
    if not job_id.isdigit():
        raise HTTPException(404, "audit run not found")
    return get_job(int(job_id), workspace_id, request, db)


@app.get("/api/workspaces/{workspace_key}/audit-runs/{job_id}", response_model=JobOut)
def get_workspace_audit_run(workspace_key: str, job_id: int, request: Request, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.read)
    return _job_response(_workspace_job_or_404(db, workspace_key, job_id))


@app.get("/api/sites/{site_id}/jobs", response_model=list[JobOut])
def list_jobs(site_id: int, workspace_id: str, request: Request, db: Session = Depends(get_db)):
    workspace, site = _workspace_site_or_404(db, workspace_id, site_id)
    authorize_workspace(request, db, workspace, Permission.read)
    return [_job_response(job) for job in db.scalars(select(Job).where(Job.site_id == site_id).order_by(Job.created_at.desc())).all()]


@app.get("/api/workspaces/{workspace_key}/sites/{site_id}/jobs", response_model=list[JobOut])
def list_workspace_jobs(workspace_key: str, site_id: int, request: Request, db: Session = Depends(get_db)):
    workspace, site = _workspace_site_or_404(db, workspace_key, site_id)
    authorize_workspace(request, db, workspace, Permission.read)
    return [_job_response(job) for job in db.scalars(select(Job).where(Job.site_id == site.id).order_by(Job.created_at.desc())).all()]


def _snapshot_response(snapshot: PageSnapshot) -> SnapshotOut:
    findings = [FindingOut(id=f.id, code=f.code, severity=f.severity, message=f.message, evidence=json.loads(f.evidence_json or "{}")) for f in snapshot.findings]
    rule_results = [
        RuleResultOut(id=item.id, rule_id=item.rule_id, version=item.version, scope=item.scope, status=item.status, severity=item.severity, message=item.message, evidence=json.loads(item.evidence_json or "{}"), remediation_hint=item.remediation_hint)
        for item in sorted(snapshot.rule_results, key=lambda result: result.rule_id)
    ]
    return SnapshotOut(id=snapshot.id, page_id=snapshot.page_id, job_id=snapshot.job_id, url=snapshot.url, status_code=snapshot.status_code, title=snapshot.title, content_hash=snapshot.content_hash, content_type=snapshot.content_type, artifact_uri=snapshot.artifact_uri, parser_version=snapshot.parser_version, fetched_at=snapshot.fetched_at, is_synthetic=snapshot.is_synthetic, findings=findings, rule_set_version=snapshot.audit_rule_version, rule_results=rule_results)


@app.get("/api/snapshots/{snapshot_id}", response_model=SnapshotOut)
def get_snapshot(snapshot_id: int, workspace_id: str, request: Request, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_id)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.read)
    snapshot = _workspace_snapshot_or_404(db, workspace_id, snapshot_id)
    return _snapshot_response(snapshot)


@app.get("/api/workspaces/{workspace_key}/snapshots/{snapshot_id}", response_model=SnapshotOut)
def get_workspace_snapshot(workspace_key: str, snapshot_id: int, request: Request, db: Session = Depends(get_db)):
    workspace = _workspace_for_key(db, workspace_key)
    if workspace is None:
        raise HTTPException(404, "workspace not found")
    authorize_workspace(request, db, workspace, Permission.read)
    return _snapshot_response(_workspace_snapshot_or_404(db, workspace_key, snapshot_id))


@app.get("/api/sites/{site_id}/snapshots", response_model=list[SnapshotOut])
def list_snapshots(site_id: int, workspace_id: str, request: Request, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    workspace, site = _workspace_site_or_404(db, workspace_id, site_id)
    authorize_workspace(request, db, workspace, Permission.read)
    snapshots = db.scalars(select(PageSnapshot).where(PageSnapshot.page.has(Page.site_id == site_id)).order_by(PageSnapshot.fetched_at.desc()).limit(limit)).all()
    return [_snapshot_response(snapshot) for snapshot in snapshots]


@app.get("/api/workspaces/{workspace_key}/sites/{site_id}/snapshots", response_model=list[SnapshotOut])
def list_workspace_snapshots(workspace_key: str, site_id: int, request: Request, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    workspace, site = _workspace_site_or_404(db, workspace_key, site_id)
    authorize_workspace(request, db, workspace, Permission.read)
    snapshots = db.scalars(
        select(PageSnapshot)
        .where(PageSnapshot.page.has(Page.site_id == site.id))
        .order_by(PageSnapshot.fetched_at.desc())
        .limit(limit)
    ).all()
    return [_snapshot_response(snapshot) for snapshot in snapshots]


@app.get("/api/sites/{site_id}/pages")
def list_pages(site_id: str, workspace_id: str, request: Request, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    if not site_id.isdigit():
        raise HTTPException(404, "site not found")
    site_pk = int(site_id)
    workspace, _ = _workspace_site_or_404(db, workspace_id, site_pk)
    authorize_workspace(request, db, workspace, Permission.read)
    snapshots = db.scalars(select(PageSnapshot).where(PageSnapshot.page.has(Page.site_id == site_pk)).order_by(PageSnapshot.fetched_at.desc()).limit(limit)).all()
    return _page_listing(snapshots)


def _page_listing(snapshots: list[PageSnapshot]) -> list[dict[str, object]]:
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


@app.get("/api/workspaces/{workspace_key}/sites/{site_id}/pages")
def list_workspace_pages(workspace_key: str, site_id: int, request: Request, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    workspace, site = _workspace_site_or_404(db, workspace_key, site_id)
    authorize_workspace(request, db, workspace, Permission.read)
    snapshots = db.scalars(
        select(PageSnapshot)
        .where(PageSnapshot.page.has(Page.site_id == site.id))
        .order_by(PageSnapshot.fetched_at.desc())
        .limit(limit)
    ).all()
    return _page_listing(snapshots)


@app.get("/api/sites/{site_id}/model")
def model_status(site_id: int, workspace_id: str, request: Request, db: Session = Depends(get_db)):
    workspace, _ = _workspace_site_or_404(db, workspace_id, site_id)
    authorize_workspace(request, db, workspace, Permission.read)
    return {"site_id": site_id, "status": "not_configured", "provider": None}


@app.post("/api/sites/{site_id}/publish")
def publish_site(site_id: int, workspace_id: str, request: Request, db: Session = Depends(get_db)):
    workspace, _ = _workspace_site_or_404(db, workspace_id, site_id)
    authorize_workspace(request, db, workspace, Permission.operate)
    return {"site_id": site_id, "status": "not_configured", "published": False}


@app.get("/api/sites/{site_id}/monitor")
def monitor_site(site_id: int, workspace_id: str, request: Request, db: Session = Depends(get_db)):
    workspace, _ = _workspace_site_or_404(db, workspace_id, site_id)
    authorize_workspace(request, db, workspace, Permission.read)
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


def _change_workspace(db: Session, key: str | int) -> Workspace:
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


def _change_or_404(db: Session, change_id: int, workspace_key: str | int) -> ChangeRequest:
    change = db.get(ChangeRequest, change_id)
    if change is None:
        raise HTTPException(404, "change request not found")
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
def create_change(workspace_key: str, site_id: int, payload: ChangeRequestCreate, request: Request, db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.operate)
    if payload.workspace_id is not None and str(payload.workspace_id) not in {str(workspace.id), workspace.external_id or ""}:
        raise HTTPException(403, "workspace mismatch")
    site = _site_in_workspace(db, site_id, workspace)
    return _change_out(_create_change(db, workspace, site, payload))


@app.post("/api/sites/{site_id}/changes", response_model=ChangeRequestOut, status_code=status.HTTP_201_CREATED)
def create_site_change(site_id: int, payload: ChangeRequestCreate, request: Request, db: Session = Depends(get_db)):
    if payload.workspace_id is None:
        raise HTTPException(422, "workspace_id is required")
    site = db.get(Site, site_id)
    if site is None:
        raise HTTPException(404, "site not found")
    workspace = _change_workspace(db, payload.workspace_id or site.workspace_id)
    authorize_workspace(request, db, workspace, Permission.operate)
    if site.workspace_id != workspace.id:
        raise HTTPException(404, "site not found")
    return _change_out(_create_change(db, workspace, site, payload))


@app.get("/api/changes/{change_id}", response_model=ChangeRequestOut)
def get_change(change_id: int, workspace_id: str, request: Request, db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_id)
    authorize_workspace(request, db, workspace, Permission.read)
    return _change_out(_change_or_404(db, change_id, workspace_id))


@app.get("/api/workspaces/{workspace_key}/changes/{change_id}", response_model=ChangeRequestOut)
def get_workspace_change(workspace_key: str, change_id: int, request: Request, db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_key)
    authorize_workspace(request, db, workspace, Permission.read)
    return _change_out(_change_or_404(db, change_id, workspace_key))


@app.post("/api/changes/{change_id}/revisions", response_model=ChangeRequestOut)
def add_change_revision(change_id: int, payload: ChangeRevisionCreate, workspace_id: str, request: Request, if_match: str | None = Header(default=None, alias="If-Match"), db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_id)
    authorize_workspace(request, db, workspace, Permission.operate)
    change = _change_or_404(db, change_id, workspace_id)
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
def submit_change(change_id: int, request: Request, payload: ChangeAction | None = None, expected_version: int | None = None, workspace_id: str = Query(...), if_match: str | None = Header(default=None, alias="If-Match"), db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_id)
    authorize_workspace(request, db, workspace, Permission.operate)
    requested_version = payload.expected_version if payload and payload.expected_version is not None else expected_version
    return _change_out(_submit_change(db, _change_or_404(db, change_id, workspace_id), requested_version, if_match))


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
def decide_change(
    change_id: int,
    payload: ChangeApprovalCreate,
    workspace_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_id)
    identity = authorize_workspace(request, db, workspace, Permission.review)
    payload = payload.model_copy(update={"reviewer": effective_actor(identity)})
    return _change_out(_decide_change(db, _change_or_404(db, change_id, workspace_id), payload, if_match))


@app.post("/api/changes/{change_id}/reject", response_model=ChangeRequestOut, include_in_schema=False)
def reject_change(
    change_id: int,
    payload: ChangeApprovalCreate,
    workspace_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
):
    workspace = _change_workspace(db, workspace_id)
    identity = authorize_workspace(request, db, workspace, Permission.review)
    payload = payload.model_copy(update={"decision": "rejected", "reviewer": effective_actor(identity)})
    return _change_out(_decide_change(db, _change_or_404(db, change_id, workspace_id), payload, if_match))


def _publication_attempt_out(attempt: PublicationAttempt) -> PublicationAttemptOut:
    return PublicationAttemptOut(
        id=attempt.id,
        change_request_id=attempt.change_request_id,
        revision_id=attempt.revision_id,
        status=attempt.status,
        target=attempt.target,
        branch=attempt.branch,
        commit_sha=attempt.commit_sha,
        external_id=attempt.external_id,
        error=attempt.error,
        deployment_status=attempt.deployment_status,
        deployment_id=attempt.deployment_id,
        deployed_commit_sha=attempt.deployed_commit_sha,
        deployed_at=attempt.deployed_at,
        verified_at=attempt.verified_at,
        rollback_of_attempt_id=attempt.rollback_of_attempt_id,
        expected_current_sha=attempt.expected_current_sha,
        rollback_reason=attempt.rollback_reason,
        created_at=attempt.created_at,
        updated_at=attempt.updated_at,
    )


@app.post("/api/changes/{change_id}/publish", response_model=PublicationAttemptOut)
def publish_change(change_id: int, request: Request, payload: ChangeAction | None = None, expected_version: int | None = None, workspace_id: str = Query(...), if_match: str | None = Header(default=None, alias="If-Match"), db: Session = Depends(get_db)):
    workspace = _change_workspace(db, workspace_id)
    authorize_workspace(request, db, workspace, Permission.operate)
    change = _change_or_404(db, change_id, workspace_id)
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
    target = os.getenv("GIT_PUBLISH_TARGET", "demo").strip() or "demo"
    idempotency_key = f"change:{change.id}:revision:{revision.id}:target:{target}"
    attempt = db.scalar(select(PublicationAttempt).where(PublicationAttempt.idempotency_key == idempotency_key))
    if attempt is not None:
        return _publication_attempt_out(attempt)
    attempt = PublicationAttempt(
        change_request_id=change.id,
        revision_id=revision.id,
        idempotency_key=idempotency_key,
        target=target,
        status="queued",
    )
    db.add(attempt)
    change.state = ChangeState.publishing.value
    _enqueue_outbox(
        db,
        event_type="change.publish_requested",
        aggregate_id=change.id,
        idempotency_key=idempotency_key,
        payload={"change_request_id": change.id, "revision_id": revision.id, "site_id": change.site_id, "target": target},
    )
    db.commit()
    db.refresh(attempt)
    return _publication_attempt_out(attempt)


@app.post("/api/publication-attempts/{attempt_id}/deployment", response_model=PublicationAttemptOut)
def record_deployment(attempt_id: int, request: Request, payload: DeploymentUpdate, workspace_id: str = Query(...), db: Session = Depends(get_db)):
    attempt = db.get(PublicationAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "publication attempt not found")
    change = db.get(ChangeRequest, attempt.change_request_id)
    workspace = _workspace_for_key(db, workspace_id)
    if change is None or workspace is None or change.workspace_id != workspace.id:
        raise HTTPException(404, "publication attempt not found")
    authorize_workspace(request, db, workspace, Permission.operate)
    if attempt.status not in {"submitted", "deployed", "verified"}:
        raise HTTPException(409, "only a submitted publication can receive a deployment callback")
    if payload.status == "deployed":
        if not attempt.commit_sha or payload.commit_sha != attempt.commit_sha:
            raise HTTPException(409, "deployment commit does not match the submitted commit")
        if attempt.deployment_status in {"deployed", "verified"}:
            if attempt.deployment_id != payload.deployment_id and payload.deployment_id is not None:
                raise HTTPException(409, "deployment callback conflicts with the recorded deployment")
            return _publication_attempt_out(attempt)
        attempt.status = "deployed"
        attempt.deployment_status = "deployed"
        attempt.deployment_id = payload.deployment_id
        attempt.deployed_commit_sha = payload.commit_sha
        attempt.deployed_at = utcnow()
        change.state = ChangeState.published.value
    else:
        if attempt.deployment_status == "verified":
            raise HTTPException(409, "a verified deployment cannot be marked failed")
        attempt.status = "failed"
        attempt.deployment_status = "failed"
        attempt.error = "deployment reported failed"
        change.state = ChangeState.failed.value
    db.commit()
    db.refresh(attempt)
    return _publication_attempt_out(attempt)


@app.post("/api/publication-attempts/{attempt_id}/verify", response_model=PublicationAttemptOut)
def verify_deployment(attempt_id: int, request: Request, workspace_id: str = Query(...), db: Session = Depends(get_db)):
    attempt = db.get(PublicationAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "publication attempt not found")
    workspace = _workspace_for_key(db, workspace_id)
    change = db.get(ChangeRequest, attempt.change_request_id)
    if workspace is None or change is None or change.workspace_id != workspace.id:
        raise HTTPException(404, "publication attempt not found")
    authorize_workspace(request, db, workspace, Permission.operate)
    if attempt.deployment_status == "verified":
        return _publication_attempt_out(attempt)
    if attempt.deployment_status != "deployed" or not attempt.deployed_commit_sha:
        raise HTTPException(409, "deployment must be confirmed before verification")
    repository = os.getenv("GIT_PUBLISH_REPOSITORY", "").strip()
    if not repository:
        attempt.status = "failed"
        attempt.deployment_status = "verify_failed"
        attempt.error = "GIT_PUBLISH_REPOSITORY is not configured; live verification is unavailable"
        db.commit()
        db.refresh(attempt)
        return _publication_attempt_out(attempt)
    if any(character not in "0123456789abcdefABCDEF" for character in attempt.deployed_commit_sha) or len(attempt.deployed_commit_sha) not in {40, 64}:
        raise HTTPException(409, "recorded commit is not a valid Git object id")
    result = subprocess.run(
        ["git", "-C", repository, "cat-file", "-e", f"{attempt.deployed_commit_sha}^{{commit}}"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        attempt.status = "failed"
        attempt.deployment_status = "verify_failed"
        attempt.error = "deployed commit could not be found in the configured local repository"
        db.commit()
        db.refresh(attempt)
        return _publication_attempt_out(attempt)
    attempt.status = "verified"
    attempt.deployment_status = "verified"
    attempt.verified_at = utcnow()
    attempt.error = None
    if attempt.rollback_of_attempt_id is not None:
        source = db.get(PublicationAttempt, attempt.rollback_of_attempt_id)
        if source is None or source.deployment_status not in {"rollback_pending", "verified"}:
            raise HTTPException(409, "rollback source is no longer in a rollback-compatible state")
        source.status = "rolled_back"
        source.deployment_status = "rolled_back"
        source.error = None
        change.state = ChangeState.rolled_back.value
    db.commit()
    db.refresh(attempt)
    return _publication_attempt_out(attempt)


@app.post("/api/publication-attempts/{attempt_id}/rollback", response_model=PublicationAttemptOut)
def propose_rollback(attempt_id: int, request: Request, payload: RollbackRequest, workspace_id: str = Query(...), db: Session = Depends(get_db)):
    source = db.get(PublicationAttempt, attempt_id)
    if source is None:
        raise HTTPException(404, "publication attempt not found")
    workspace = _workspace_for_key(db, workspace_id)
    change = db.get(ChangeRequest, source.change_request_id)
    if workspace is None or change is None or change.workspace_id != workspace.id:
        raise HTTPException(404, "publication attempt not found")
    authorize_workspace(request, db, workspace, Permission.operate)
    if source.deployment_status not in {"verified", "rollback_pending"} or not source.deployed_commit_sha:
        raise HTTPException(409, "only a verified deployment can be rolled back")
    if payload.expected_current_sha != source.deployed_commit_sha:
        raise HTTPException(409, "current deployment changed; refresh before proposing rollback")
    target = source.target or (os.getenv("GIT_PUBLISH_TARGET", "demo").strip() or "demo")
    key = f"rollback:{source.id}:sha:{payload.expected_current_sha}"
    existing = db.scalar(select(PublicationAttempt).where(PublicationAttempt.idempotency_key == key))
    if existing is not None:
        return _publication_attempt_out(existing)
    rollback = PublicationAttempt(
        change_request_id=source.change_request_id,
        revision_id=source.revision_id,
        idempotency_key=key,
        target=target,
        status="queued",
        rollback_of_attempt_id=source.id,
        expected_current_sha=payload.expected_current_sha,
        rollback_reason=payload.reason,
    )
    db.add(rollback)
    db.flush()
    source.deployment_status = "rollback_pending"
    _enqueue_outbox(
        db,
        event_type="change.rollback_requested",
        aggregate_id=change.id,
        idempotency_key=key,
        payload={
            "change_request_id": change.id,
            "revision_id": source.revision_id,
            "site_id": change.site_id,
            "target": target,
            "rollback_attempt_id": rollback.id,
            "source_attempt_id": source.id,
            "expected_current_sha": payload.expected_current_sha,
        },
    )
    db.commit()
    db.refresh(rollback)
    return _publication_attempt_out(rollback)
