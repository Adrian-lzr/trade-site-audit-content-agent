"""Durable, at-least-once worker for visibility sampling runs."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable
from urllib.parse import urlsplit

from sqlalchemy import Numeric, String, cast, func, select, update

from .config import settings
from .database import SessionLocal
from .observability import log_event
from .models import (
    ProcurementQuestion,
    ProcurementQuestionSet,
    ProcurementQuestionSetVersion,
    Site,
    Workspace,
    VisibilityRun,
    VisibilityRunStatus,
    VisibilitySample,
    VisibilitySampleStatus,
    utcnow,
)
from .time_utils import as_utc
from .services.visibility_metrics import calculate_visibility_metrics as calculate_visibility_metrics_v2
from .visibility_provider import VisibilityProvider, VisibilityResponse, build_visibility_provider, redact_sensitive_text
from .worker_audit import append_worker_audit, initiator_for


VISIBILITY_LEASE = timedelta(minutes=5)
MAX_VISIBILITY_ATTEMPTS = 3
logger = logging.getLogger(__name__)


class VisibilityWorker:
    def __init__(
        self,
        provider_factory: Callable[[VisibilityRun], VisibilityProvider] | VisibilityProvider | None = None,
        lease_duration: timedelta = VISIBILITY_LEASE,
        raw_retention_days: int | None = None,
        raw_max_bytes: int | None = None,
    ):
        if provider_factory is not None and not callable(provider_factory):
            self.provider_factory = lambda _: provider_factory
        else:
            self.provider_factory = provider_factory or self._default_provider
        self.lease_duration = lease_duration
        self.raw_retention_days = settings.raw_evidence_retention_days if raw_retention_days is None else raw_retention_days
        self.raw_max_bytes = settings.raw_evidence_max_bytes if raw_max_bytes is None else raw_max_bytes

    @staticmethod
    def _default_provider(run: VisibilityRun) -> VisibilityProvider:
        return build_visibility_provider(run.provider, kind=run.provider_kind, model=run.provider_model)

    def recover_interrupted(self) -> int:
        now = utcnow()
        with SessionLocal() as db:
            result = db.execute(
                update(VisibilityRun)
                .execution_options(synchronize_session=False)
                .where(
                    VisibilityRun.status == VisibilityRunStatus.running.value,
                    VisibilityRun.lease_expires_at.is_not(None),
                    VisibilityRun.lease_expires_at <= now,
                )
                .values(
                    status=VisibilityRunStatus.queued.value,
                    lease_token=None,
                    lease_expires_at=None,
                    # Any reservation belongs to the abandoned in-flight
                    # sample. Releasing it lets the retry make a fresh,
                    # conditional reservation instead of leaking budget.
                    reserved_cost_usd="0.000000",
                    error="requeued after visibility worker lease expired",
                )
            )
            db.commit()
            return result.rowcount or 0

    def purge_expired_raw_evidence(self, *, now: datetime | None = None, batch_size: int = 1000) -> int:
        """Remove expired raw provider bodies while retaining parsed evidence.

        ``raw_response`` is intentionally the only field removed.  The sample
        status, answer, citations, request/model/cost metadata, and run audit
        history remain available for later review.  A zero retention value
        means raw bodies are eligible for immediate cleanup; the default is
        finite.
        """

        if self.raw_retention_days < 0:
            return 0
        current = now or utcnow()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = current - timedelta(days=self.raw_retention_days)
        with SessionLocal() as db:
            samples = list(
                db.scalars(
                    select(VisibilitySample)
                    .where(
                        VisibilitySample.raw_response.is_not(None),
                        VisibilitySample.created_at < cutoff,
                    )
                    .order_by(VisibilitySample.created_at, VisibilitySample.id)
                    .limit(max(1, batch_size))
                ).all()
            )
            for sample in samples:
                sample.raw_response = None
            if samples:
                db.commit()
            return len(samples)

    def _claim(self, run_id: int | None = None) -> tuple[int, str] | None:
        now = utcnow()
        token = str(uuid.uuid4())
        with SessionLocal() as db:
            claim_query = select(VisibilityRun).where(
                VisibilityRun.status == VisibilityRunStatus.queued.value,
                VisibilityRun.attempts < MAX_VISIBILITY_ATTEMPTS,
            )
            if run_id is not None:
                claim_query = claim_query.where(VisibilityRun.id == run_id)
            else:
                claim_query = claim_query.order_by(VisibilityRun.created_at, VisibilityRun.id)
            run = db.scalar(claim_query)
            if run is None:
                return None
            changed = db.execute(
                update(VisibilityRun)
                .execution_options(synchronize_session=False)
                .where(
                    VisibilityRun.id == run.id,
                    VisibilityRun.status == VisibilityRunStatus.queued.value,
                    VisibilityRun.attempts < MAX_VISIBILITY_ATTEMPTS,
                )
                .values(
                    status=VisibilityRunStatus.running.value,
                    attempts=VisibilityRun.attempts + 1,
                    lease_token=token,
                    lease_expires_at=now + self.lease_duration,
                    started_at=run.started_at or now,
                    error=None,
                )
            )
            if not changed.rowcount:
                db.rollback()
                return None
            initiator, _ = initiator_for(
                db, workspace_id=run.workspace_id, target_type="visibility_run",
                target_id=run.id, action="visibility_run.created",
            )
            append_worker_audit(
                db, workspace_id=run.workspace_id, worker="visibility", action="visibility_run.claimed",
                target_type="visibility_run", target_id=run.id, initiator=initiator,
                run_id=run.request_id, task_id=run.id, lease_token=token,
                attempt=run.attempts + 1, result="running",
            )
            db.commit()
            return run.id, token

    def _renew(self, db, run_id: int, token: str) -> None:
        now = utcnow()
        changed = db.execute(
            update(VisibilityRun)
            .execution_options(synchronize_session=False)
            .where(
                VisibilityRun.id == run_id,
                VisibilityRun.status == VisibilityRunStatus.running.value,
                VisibilityRun.lease_token == token,
                VisibilityRun.lease_expires_at.is_not(None),
                VisibilityRun.lease_expires_at > now,
            )
            .values(lease_expires_at=now + self.lease_duration)
        )
        if not changed.rowcount:
            raise RuntimeError("visibility run lease was lost")

    def _process(self, run_id: int, token: str) -> None:
        with SessionLocal() as db:
            run = db.get(VisibilityRun, run_id)
            if run is None or run.lease_token != token:
                return
            try:
                site = db.scalar(select(Site).where(Site.id == run.site_id, Site.workspace_id == run.workspace_id))
                version = db.get(ProcurementQuestionSetVersion, run.question_set_version_id)
                question_set = db.get(ProcurementQuestionSet, run.question_set_id)
                if (
                    site is None
                    or version is None
                    or question_set is None
                    or question_set.workspace_id != run.workspace_id
                    or question_set.site_id != run.site_id
                    or version.question_set_id != question_set.id
                    or version.state != "frozen"
                ):
                    self._finish_failure(db, run, token, "visibility run question set is outside its workspace/site or not frozen")
                    return
                questions = list(
                    db.scalars(
                        select(ProcurementQuestion)
                        .where(ProcurementQuestion.question_set_version_id == version.id)
                        .order_by(ProcurementQuestion.position, ProcurementQuestion.id)
                    ).all()
                )
                # The API creates the sample rows, but direct worker use remains
                # safe and deterministic by materializing any missing rows here.
                existing = {sample.question_id: sample for sample in run.samples}
                terms = _json_list(run.brand_terms_json)
                for question in questions[: run.planned_samples or None]:
                    if question.id not in existing:
                        sample = VisibilitySample(
                            run_id=run.id,
                            question_id=question.id,
                            position=question.position,
                            brand_query=_brand_query(question.question, terms),
                            status=VisibilitySampleStatus.failed.value,
                            error_code="not_started",
                            error_message="sample has not been attempted",
                            request_id=str(uuid.uuid4()),
                        )
                        db.add(sample)
                db.flush()
                samples = list(
                    db.scalars(select(VisibilitySample).where(VisibilitySample.run_id == run.id).order_by(VisibilitySample.position, VisibilitySample.id)).all()
                )
                provider = self.provider_factory(run)
                capabilities = provider.capabilities()
                if not isinstance(capabilities, dict):
                    capabilities = {}
                try:
                    previous_capabilities = json.loads(run.capability_json or "{}")
                except (TypeError, ValueError):
                    previous_capabilities = {}
                if isinstance(previous_capabilities, dict) and isinstance(previous_capabilities.get("budget_limits"), dict):
                    capabilities = {**capabilities, "budget_limits": previous_capabilities["budget_limits"]}
                run.capability_json = json.dumps(capabilities, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                pricing_basis = getattr(provider, "pricing_basis", None)
                if (not run.pricing_basis_json or run.pricing_basis_json == "{}") and callable(pricing_basis):
                    run.pricing_basis_json = json.dumps(pricing_basis(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                if not run.request_id:
                    run.request_id = str(uuid.uuid4())
                db.commit()
                target_domain = _site_domain(site.base_url)
                budget = _decimal(run.budget_usd)
                stored_limits = previous_capabilities.get("budget_limits") if isinstance(previous_capabilities, dict) else None
                per_run_budget = (
                    _decimal(stored_limits.get("per_run_usd"))
                    if isinstance(stored_limits, dict) and "per_run_usd" in stored_limits
                    else settings.visibility_max_run_budget_usd
                )
                daily_budget = (
                    _decimal(stored_limits.get("workspace_daily_usd"))
                    if isinstance(stored_limits, dict) and "workspace_daily_usd" in stored_limits
                    else settings.visibility_daily_budget_usd
                )
                if per_run_budget > 0 and budget > per_run_budget:
                    for sample in samples:
                        if sample.status != VisibilitySampleStatus.succeeded.value:
                            _set_failed(sample, "budget_exceeded", "sampling budget exceeds the configured per-run limit")
                    run.error = "sampling budget exceeds the configured per-run limit"
                    _finish_from_samples(run, db)
                    run.lease_token = None
                    run.lease_expires_at = None
                    run.completed_at = utcnow()
                    initiator, _ = initiator_for(
                        db, workspace_id=run.workspace_id, target_type="visibility_run",
                        target_id=run.id, action="visibility_run.created",
                    )
                    append_worker_audit(
                        db, workspace_id=run.workspace_id, worker="visibility", action="visibility_run.completed",
                        target_type="visibility_run", target_id=run.id, initiator=initiator,
                        run_id=run.request_id, task_id=run.id, lease_token=token,
                        attempt=run.attempts, result=run.status,
                    )
                    db.commit()
                    return
                for sample in samples:
                    if sample.status == VisibilitySampleStatus.succeeded.value:
                        continue
                    self._renew(db, run.id, token)
                    if not sample.request_id:
                        sample.request_id = str(uuid.uuid4())
                    estimate = _provider_estimate(provider, _question_text(db, sample.question_id))
                    sample.estimated_cost_usd = _money(estimate)
                    db.flush()
                    if not _reserve_sample_budget(db, run.id, sample.id, estimate, budget, token, daily_budget):
                        if daily_budget > 0 and calculate_workspace_daily_spend(db, run.workspace_id) + estimate > daily_budget:
                            _set_failed(sample, "workspace_daily_budget_exceeded", "workspace daily sampling budget was exhausted before this question")
                            run.error = "workspace daily sampling budget exhausted"
                        else:
                            _set_failed(sample, "budget_exceeded", "sampling budget was exhausted before this question")
                            run.error = "sampling budget exhausted"
                        db.commit()
                        continue
                    try:
                        response = _sample_provider(
                            provider,
                            _question_text(db, sample.question_id),
                            market=run.market,
                            language=run.language,
                            target_domain=target_domain,
                            request_id=sample.request_id,
                        )
                    except Exception as exc:  # provider failures become evidence, not lost jobs
                        response = VisibilityResponse(
                            status=VisibilitySampleStatus.failed.value,
                            error_code="provider_exception",
                            error_message=f"provider raised {type(exc).__name__}",
                        )
                    _apply_response(sample, response, max_raw_bytes=self.raw_max_bytes)
                    observed_cost = _decimal(response.cost_usd)
                    # SessionLocal deliberately disables autoflush. Flush the
                    # evidence before the SQL accounting update, then read the
                    # updated totals without refreshing the run relationship;
                    # refreshing it here can discard the in-memory sample result.
                    db.flush()
                    _settle_sample_cost(db, run.id, sample.id, observed_cost, estimate, token)
                    new_total = _decimal(
                        db.scalar(select(VisibilityRun.total_cost_usd).where(VisibilityRun.id == run.id))
                    )
                    new_reserved = _decimal(
                        db.scalar(select(VisibilityRun.reserved_cost_usd).where(VisibilityRun.id == run.id))
                    )
                    run.total_cost_usd = _money(new_total)
                    run.reserved_cost_usd = _money(new_reserved)
                    if budget > 0 and new_total > budget and sample.status == VisibilitySampleStatus.succeeded.value:
                        _set_failed(sample, "budget_exceeded", "provider response exceeded the sampling budget")
                        run.error = "sampling budget exceeded"
                    if daily_budget > 0 and calculate_workspace_daily_spend(db, run.workspace_id) > daily_budget and sample.status == VisibilitySampleStatus.succeeded.value:
                        _set_failed(sample, "workspace_daily_budget_exceeded", "provider response exceeded the workspace daily sampling budget")
                        run.error = "workspace daily sampling budget exceeded"
                    log_event(
                        logger,
                        "visibility_sample_completed",
                        sample_request_id=sample.request_id,
                        run_id=run.id,
                        site_id=run.site_id,
                        provider_request_id=sample.provider_request_id,
                        model=sample.model,
                        input_tokens=sample.input_tokens,
                        output_tokens=sample.output_tokens,
                        cost_usd=sample.cost_usd,
                    )
                    db.commit()
                self._renew(db, run.id, token)
                _finish_from_samples(run, db)
                run.lease_token = None
                run.lease_expires_at = None
                run.completed_at = utcnow()
                initiator, _ = initiator_for(
                    db, workspace_id=run.workspace_id, target_type="visibility_run",
                    target_id=run.id, action="visibility_run.created",
                )
                append_worker_audit(
                    db, workspace_id=run.workspace_id, worker="visibility", action="visibility_run.completed",
                    target_type="visibility_run", target_id=run.id, initiator=initiator,
                    run_id=run.request_id, task_id=run.id, lease_token=token,
                    attempt=run.attempts, result=run.status,
                    error_type="ProviderFailure" if run.status == VisibilityRunStatus.failed.value else None,
                )
                db.commit()
            except Exception:
                db.rollback()
                with SessionLocal() as failed_db:
                    current = failed_db.scalar(
                        select(VisibilityRun).where(VisibilityRun.id == run_id, VisibilityRun.lease_token == token)
                    )
                    if current is not None:
                        current.status = VisibilityRunStatus.failed.value
                        current.error = "visibility worker execution failed"
                        current.lease_token = None
                        current.lease_expires_at = None
                        current.completed_at = utcnow()
                        initiator, _ = initiator_for(
                            failed_db, workspace_id=current.workspace_id, target_type="visibility_run",
                            target_id=current.id, action="visibility_run.created",
                        )
                        append_worker_audit(
                            failed_db, workspace_id=current.workspace_id, worker="visibility", action="visibility_run.completed",
                            target_type="visibility_run", target_id=current.id, initiator=initiator,
                            run_id=current.request_id, task_id=current.id, lease_token=token,
                            attempt=current.attempts, result="failed", error_type="WorkerExecutionError",
                        )
                        failed_db.commit()
                raise

    @staticmethod
    def _finish_failure(db, run: VisibilityRun, token: str, message: str) -> None:
        if run.lease_token != token:
            return
        run.status = VisibilityRunStatus.failed.value
        run.error = message
        run.lease_token = None
        run.lease_expires_at = None
        run.completed_at = utcnow()
        initiator, _ = initiator_for(
            db, workspace_id=run.workspace_id, target_type="visibility_run",
            target_id=run.id, action="visibility_run.created",
        )
        append_worker_audit(
            db, workspace_id=run.workspace_id, worker="visibility", action="visibility_run.completed",
            target_type="visibility_run", target_id=run.id, initiator=initiator,
            run_id=run.request_id, task_id=run.id, lease_token=token,
            attempt=run.attempts, result="failed", error_type="InvalidRunConfiguration",
        )
        db.commit()

    def run_once(self, run_id: int | None = None) -> bool:
        self.purge_expired_raw_evidence()
        self.recover_interrupted()
        claimed = self._claim(run_id=run_id)
        if claimed is None:
            return False
        run_id, token = claimed
        try:
            self._process(run_id, token)
        except Exception:
            # _process records a guarded terminal state where it still owns the
            # lease. The worker loop must remain alive for the next run.
            pass
        return True

    def run_forever(self, poll_interval: float = 1.0, stop_event=None) -> None:
        from threading import Event

        stop_event = stop_event or Event()
        while not stop_event.is_set():
            if not self.run_once():
                stop_event.wait(poll_interval)


def _question_text(db, question_id: int) -> str:
    question = db.get(ProcurementQuestion, question_id)
    return question.question if question is not None else ""


def _sample_provider(
    provider: VisibilityProvider,
    question: str,
    *,
    market: str,
    language: str,
    target_domain: str,
    request_id: str | None,
) -> VisibilityResponse:
    """Call providers with correlation metadata while retaining old adapters.

    Third-party/demo adapters written against the original narrow protocol may
    not accept ``request_id`` yet.  Only an unexpected-keyword TypeError gets
    the compatibility retry; errors raised by provider code are propagated.
    """

    try:
        return provider.sample(
            question,
            market=market,
            language=language,
            target_domain=target_domain,
            request_id=request_id,
        )
    except TypeError as exc:
        if "request_id" not in str(exc):
            raise
        return provider.sample(question, market=market, language=language, target_domain=target_domain)


def calculate_workspace_daily_spend(db, workspace_id: int, *, at: datetime | None = None) -> Decimal:
    """Return total plus in-flight reservations for one UTC calendar day."""

    current = as_utc(at or utcnow())
    start = datetime.combine(current.date(), time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    total = Decimal("0")
    runs = db.scalars(select(VisibilityRun).where(VisibilityRun.workspace_id == workspace_id)).all()
    for run in runs:
        occurred = run.completed_at or run.started_at or run.created_at
        if occurred is None:
            continue
        occurred = as_utc(occurred)
        if start <= occurred < end:
            total += _decimal(run.total_cost_usd) + _decimal(run.reserved_cost_usd)
    return total.quantize(Decimal("0.000001"))


def _apply_response(sample: VisibilitySample, response: VisibilityResponse, *, max_raw_bytes: int | None = None) -> None:
    sample.status = response.status if response.status in {item.value for item in VisibilitySampleStatus} else VisibilitySampleStatus.failed.value
    sample.answered_question = response.answered_question if isinstance(response.answered_question, bool) else None
    sample.raw_response = redact_sensitive_text(response.raw_response, max_bytes=max_raw_bytes)
    sample.answer_text = redact_sensitive_text(response.answer_text, max_bytes=max_raw_bytes)
    sample.citations_json = json.dumps(
        [redact_sensitive_text(item, max_bytes=max_raw_bytes) or "" for item in (response.citations or [])],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    sample.mentioned_domains_json = json.dumps(
        [redact_sensitive_text(item, max_bytes=max_raw_bytes) or "" for item in (response.mentioned_domains or [])],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    sample.provider_request_id = redact_sensitive_text(response.provider_request_id, max_bytes=256)
    sample.model = redact_sensitive_text(response.model, max_bytes=200)
    sample.input_tokens = response.input_tokens
    sample.output_tokens = response.output_tokens
    sample.cost_usd = _money(_decimal(response.cost_usd)) if response.cost_usd is not None else None
    sample.error_code = response.error_code
    sample.error_message = redact_sensitive_text(response.error_message, max_bytes=max_raw_bytes)
    sample.completed_at = utcnow()


def _provider_estimate(provider: VisibilityProvider, question: str) -> Decimal:
    estimator = getattr(provider, "estimate_cost", None)
    if not callable(estimator):
        return Decimal("0")
    try:
        value = estimator(question)
    except Exception:
        return Decimal("0")
    return _decimal(value)


def _reserve_sample_budget(
    db,
    run_id: int,
    sample_id: int,
    estimate: Decimal,
    budget: Decimal,
    token: str,
    daily_budget: Decimal | None = None,
) -> bool:
    """Atomically reserve the estimate before an external provider call."""
    del sample_id  # The run lease serializes sample execution for this worker.
    estimate = _decimal(estimate).quantize(Decimal("0.000001"))
    daily_limit = settings.visibility_daily_budget_usd if daily_budget is None else _decimal(daily_budget)
    if daily_limit > 0:
        # PostgreSQL serializes all workspace reservations on this row. SQLite
        # has database-level write serialization, so the same check remains
        # transactional in the local test/runtime database.
        run = db.scalar(select(VisibilityRun).where(VisibilityRun.id == run_id).with_for_update())
        if run is None or run.lease_token != token or run.status != VisibilityRunStatus.running.value:
            return False
        workspace = db.scalar(select(Workspace).where(Workspace.id == run.workspace_id).with_for_update())
        if workspace is None:
            return False
        if calculate_workspace_daily_spend(db, workspace.id) + estimate > daily_limit:
            return False
    conditions = [
        VisibilityRun.id == run_id,
        VisibilityRun.status == VisibilityRunStatus.running.value,
        VisibilityRun.lease_token == token,
    ]
    if budget > 0:
        conditions.append(
            (cast(VisibilityRun.total_cost_usd, Numeric(30, 6))
             + cast(VisibilityRun.reserved_cost_usd, Numeric(30, 6))
             + estimate) <= budget
        )
    result = db.execute(
        update(VisibilityRun)
        .where(*conditions)
        .values(reserved_cost_usd=_sql_money_expression(db, VisibilityRun.reserved_cost_usd, estimate))
    )
    return bool(result.rowcount)


def _settle_sample_cost(db, run_id: int, sample_id: int, observed: Decimal, estimate: Decimal, token: str) -> None:
    """Release the reservation and atomically add the provider-reported cost."""
    del sample_id  # The sample row carries the detailed estimate and actual amount.
    observed = _decimal(observed).quantize(Decimal("0.000001"))
    estimate = _decimal(estimate).quantize(Decimal("0.000001"))
    db.execute(
        update(VisibilityRun)
        .where(VisibilityRun.id == run_id, VisibilityRun.lease_token == token)
        .values(
            total_cost_usd=_sql_money_expression(db, VisibilityRun.total_cost_usd, observed),
            reserved_cost_usd=_sql_money_expression(db, VisibilityRun.reserved_cost_usd, -estimate),
        )
    )


def _sql_money_expression(db, column, delta: Decimal):
    """Format an atomic decimal update back into the six-place string schema."""
    numeric = cast(column, Numeric(30, 6)) + delta
    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect == "sqlite":
        return func.printf("%.6f", numeric)
    if dialect == "postgresql":
        return func.to_char(numeric, "FM999999999990.000000")
    return cast(numeric, String)


def _set_failed(sample: VisibilitySample, code: str, message: str) -> None:
    sample.status = VisibilitySampleStatus.failed.value
    sample.error_code = code
    sample.error_message = message
    sample.completed_at = utcnow()


def _finish_from_samples(run: VisibilityRun, db) -> None:
    samples = list(db.scalars(select(VisibilitySample).where(VisibilitySample.run_id == run.id)).all())
    successes = sum(sample.status == VisibilitySampleStatus.succeeded.value for sample in samples)
    failures = len(samples) - successes
    run.successful_samples = successes
    run.failed_samples = failures
    run.planned_samples = max(run.planned_samples, len(samples))
    if not samples or successes == 0:
        run.status = VisibilityRunStatus.failed.value if failures else VisibilityRunStatus.partial.value
    elif failures:
        run.status = VisibilityRunStatus.partial.value
    else:
        run.status = VisibilityRunStatus.succeeded.value
    if failures and not run.error:
        run.error = "one or more visibility samples failed or were unavailable"


def calculate_visibility_metrics(run: VisibilityRun, site: Site) -> dict[str, object]:
    terms = _json_list(run.brand_terms_json)
    samples = list(run.samples)
    questions = [sample.question_id for sample in samples]
    metrics = calculate_visibility_metrics_v2(
        samples,
        site_url=site.base_url,
        brand_terms=terms,
        planned_samples=run.planned_samples or len(samples),
        expected_questions=questions,
        is_synthetic=bool(run.is_synthetic),
        provider_kind=run.provider_kind,
    )
    # Keep the established response aliases while making v2 the canonical
    # source of truth for all counting semantics.
    sample_success = metrics["sample_success"]
    citation = metrics["validated_site_citation"]
    brand = metrics["brand_mention"]
    domain = metrics["domain_mention"]
    successful = [sample for sample in samples if sample.status == VisibilitySampleStatus.succeeded.value]
    brand_samples = [sample for sample in successful if sample.brand_query]
    non_brand_samples = [sample for sample in successful if not sample.brand_query]
    metrics.update(
        {
            "sampling_success_rate": sample_success["rate"],
            "successful_samples": sample_success["numerator"],
            "failed_samples": metrics["sample_counts"]["observed"] - sample_success["numerator"],
            "failed_error_samples": sum(sample.status == VisibilitySampleStatus.failed.value for sample in samples),
            "unavailable_samples": sum(sample.status == VisibilitySampleStatus.unavailable.value for sample in samples),
            "planned_samples": sample_success["denominator"],
            "site_citation_rate": citation["rate"],
            "question_coverage_rate": metrics["answered_question"]["rate"],
            "covered_question_count": metrics["answered_question"]["numerator"],
            "successful_question_count": metrics["answered_question"]["denominator"],
            "brand_mention_rate": brand["rate"],
            "non_brand_mention_rate": domain["rate"],
            "brand": {
                "query_count": len(brand_samples),
                "mention_count": brand["numerator"],
                "mention_rate": brand["rate"],
            },
            "non_brand": {
                "query_count": len(non_brand_samples),
                "mention_count": domain["numerator"],
                "mention_rate": domain["rate"],
            },
        }
    )
    return metrics


def _sample_cites_site(sample: VisibilitySample, target_domain: str) -> bool:
    if not target_domain:
        return False
    for url in _json_list(sample.citations_json):
        host = urlsplit(url).hostname or url.split("/")[0]
        if _same_domain(host, target_domain):
            return True
    return any(_same_domain(domain, target_domain) for domain in _json_list(sample.mentioned_domains_json))


def _sample_mentions_brand(sample: VisibilitySample, terms: list[str], target_domain: str) -> bool:
    answer = (sample.answer_text or "").casefold()
    if terms:
        return any(term.casefold() in answer for term in terms if term.strip())
    return _sample_cites_site(sample, target_domain)


def _brand_query(question: str, terms: list[str]) -> bool:
    folded = question.casefold()
    return any(term.casefold() in folded for term in terms if term.strip())


def _site_domain(base_url: str) -> str:
    return (urlsplit(base_url).hostname or base_url).lower().strip(".")


def _same_domain(left: str, right: str) -> bool:
    if "://" in left:
        left = urlsplit(left).hostname or left
    if "://" in right:
        right = urlsplit(right).hostname or right
    left = left.lower().strip(".")
    right = right.lower().strip(".")
    return left == right or left.endswith("." + right)


def _json_list(value: str | list[str] | None) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed if str(item).strip()] if isinstance(parsed, list) else []


def _decimal(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    if parsed.is_nan() or parsed.is_infinite() or parsed < 0:
        return Decimal("0")
    return parsed


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.000001')):.6f}"


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0
