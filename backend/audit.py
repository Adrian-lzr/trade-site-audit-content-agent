from __future__ import annotations

from datetime import timedelta, timezone
from hashlib import sha256
import json
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .audit_rules import RULES, RULE_VERSION, evaluate_snapshot, parse_document
from .crawler import CrawlError, CrawlResult, FixtureCrawler, is_sitemap_url, normalize_path
from .document_parser import PARSER_VERSION
from .models import AuditFinding, AuditRuleResult, Job, JobStatus, Page, PageSnapshot, Site, utcnow


class JobAlreadyClaimed(RuntimeError):
    pass


class JobLeaseLost(RuntimeError):
    pass


DEFAULT_JOB_LEASE = timedelta(minutes=5)
MAX_CAPTURED_LINKS_PER_PAGE = 100
MAX_REDIRECT_EVIDENCE = 10


def _lease_is_active(expires_at) -> bool:
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > utcnow()


def claim_job(db: Session, job_id: int, lease_duration: timedelta = DEFAULT_JOB_LEASE) -> str:
    token = str(uuid4())
    now = utcnow()
    claim = db.execute(
        update(Job)
        .execution_options(synchronize_session=False)
        .where(Job.id == job_id, Job.status == JobStatus.queued.value)
        .values(
            status=JobStatus.running.value,
            started_at=now,
            finished_at=None,
            error=None,
            lease_token=token,
            lease_expires_at=now + lease_duration,
        )
    )
    if claim.rowcount != 1:
        db.rollback()
        if db.get(Job, job_id) is None:
            raise ValueError("job not found")
        raise JobAlreadyClaimed(f"job {job_id} is not queued")
    db.commit()
    return token


def _mark_failed(db: Session, job_id: int, lease_token: str, error: Exception) -> bool:
    now = utcnow()
    result = db.execute(
        update(Job)
        .execution_options(synchronize_session=False)
        .where(
            Job.id == job_id,
            Job.status == JobStatus.running.value,
            Job.lease_token == lease_token,
            Job.lease_expires_at > now,
        )
        .values(
            status=JobStatus.failed.value,
            error=str(error)[:2000],
            finished_at=now,
            lease_token=None,
            lease_expires_at=None,
        )
    )
    db.commit()
    return result.rowcount == 1


def _get_or_create_page(db: Session, site_id: int, canonical_url: str) -> Page:
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise RuntimeError(f"unsupported database dialect for page upsert: {dialect}")

    db.execute(
        insert(Page)
        .values(site_id=site_id, canonical_url=canonical_url)
        .on_conflict_do_nothing(index_elements=[Page.site_id, Page.canonical_url])
    )
    page = db.scalar(select(Page).where(Page.site_id == site_id, Page.canonical_url == canonical_url))
    if page is None:
        raise RuntimeError("page upsert completed without a matching page")
    return page


def _renew_lease(db: Session, job_id: int, lease_token: str) -> None:
    now = utcnow()
    result = db.execute(
        update(Job)
        .execution_options(synchronize_session=False)
        .where(Job.id == job_id, Job.status == JobStatus.running.value, Job.lease_token == lease_token, Job.lease_expires_at > now)
        .values(lease_expires_at=now + DEFAULT_JOB_LEASE)
    )
    db.commit()
    if result.rowcount != 1:
        raise JobLeaseLost(f"worker lease for job {job_id} expired before the next audit request")


def _site_origin_key(url: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        port = parsed.port
        if port == (80 if parsed.scheme == "http" else 443):
            port = None
        return parsed.scheme.lower(), parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower(), port
    except (ValueError, UnicodeError):
        return None


def _path_is_allowed(url: str, allowed_paths: list[str]) -> bool:
    try:
        path = normalize_path(urlsplit(url).path or "/")
        return any(path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/") or prefix == "/" for prefix in allowed_paths)
    except CrawlError:
        return False


def _robots_policy(robots: dict, url: str) -> tuple[bool | None, str | None]:
    code = robots.get("status_code")
    if robots.get("error") or code is None or code == 429 or code >= 500 or (400 <= code < 500 and code != 404):
        return None, None
    if code == 404:
        return True, None
    parser = RobotFileParser()
    parser.set_url(str(robots.get("url") or ""))
    parser.parse(str(robots.get("content") or "").splitlines())
    allowed = parser.can_fetch("TradeVisibilityAuditBot", url)
    directive = None
    if not allowed:
        for line in str(robots.get("content") or "").splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip().lower() == "disallow" and value.strip() and urlsplit(url).path.startswith(value.strip()):
                directive = f"Disallow: {value.strip()}"
                break
    return allowed, directive


def _control_response(crawler, url: str, *, base_url: str, allow_loopback: bool) -> CrawlResult:
    if hasattr(crawler, "fetch_control"):
        return crawler.fetch_control(url, base_url=base_url, allow_loopback=allow_loopback)
    # Crawler doubles used by tests can expose only fetch().
    return crawler.fetch(url, base_url=base_url, allowed_paths=["/"], allow_loopback=allow_loopback)


def _result_data(result: CrawlResult) -> dict:
    return {
        "url": result.url,
        "status_code": result.status_code,
        "headers": result.headers,
        "content": result.content,
        "title": result.title,
        "error": result.error,
        "redirect_chain": list(result.redirect_chain)[:MAX_REDIRECT_EVIDENCE],
    }


def map_rule_failures_to_findings(rule_results: list[dict]) -> list[dict]:
    """Project only definitive rule failures into the legacy findings collection."""
    findings = []
    for result in rule_results:
        if result.get("status") != "fail":
            continue
        rule_id = str(result.get("rule_id") or "unknown")
        evidence = dict(result.get("evidence") or {})
        evidence.update({
            "rule_id": rule_id,
            "rule_version": result.get("version"),
            "rule_status": "fail",
        })
        findings.append({
            "code": "TITLE_MISSING" if rule_id == "title_present" else f"RULE_{rule_id.upper()}",
            "severity": str(result.get("severity") or "warning"),
            "message": str(result.get("message") or "Audit rule failed."),
            "evidence": evidence,
        })
    return findings


def summarize_rule_results(rule_results: list[dict]) -> dict[str, int]:
    """Keep definitive failures, review, inconclusive, and NA totals distinct."""
    counts = {
        "problem_count": 0,
        "review_count": 0,
        "unknown_count": 0,
        "pass_count": 0,
        "not_applicable_count": 0,
    }
    status_keys = {
        "fail": "problem_count",
        "needs_review": "review_count",
        "unknown": "unknown_count",
        "pass": "pass_count",
        "not_applicable": "not_applicable_count",
    }
    for result in rule_results:
        key = status_keys.get(str(result.get("status")))
        if key:
            counts[key] += 1
    counts["attention_count"] = counts["problem_count"] + counts["review_count"] + counts["unknown_count"]
    return counts


def execute_claimed_job(db: Session, job_id: int, lease_token: str, crawler: FixtureCrawler | None = None) -> Job:
    job_state = db.execute(
        select(Job.site_id, Job.status, Job.lease_token, Job.lease_expires_at).where(Job.id == job_id)
    ).one_or_none()
    if job_state is None:
        raise ValueError("job not found")
    site_id, job_status, current_token, expires_at = job_state
    if job_status != JobStatus.running.value or current_token != lease_token or not _lease_is_active(expires_at):
        raise JobLeaseLost(f"worker lease for job {job_id} is no longer valid")
    site_state = db.execute(select(Site.base_url, Site.allowed_paths, Site.is_synthetic, Site.audit_policy_json, Site.audit_page_limit).where(Site.id == site_id)).one_or_none()
    db.rollback()
    try:
        if site_state is None:
            raise ValueError("site not found")
        base_url, allowed_paths_json_value, is_synthetic, audit_policy_json, audit_page_limit = site_state
        allowed_paths = json.loads(allowed_paths_json_value)
        policy = json.loads(audit_policy_json or "{}")
        policy.setdefault("site_origin", base_url)
        policy.setdefault("preferred_origin", base_url)
        policy.setdefault("expected_accessible", True)
        policy.setdefault("expected_indexable", True)
        policy["allowed_paths"] = allowed_paths
        policy["audit_page_limit"] = max(1, min(int(audit_page_limit or 20), 50))
        paths = allowed_paths
        target_path = "/" if "/" in paths else paths[0]
        target_url = urljoin(base_url.rstrip("/") + "/", target_path.lstrip("/"))
        active_crawler = crawler or FixtureCrawler()
        robots_url = urljoin(base_url.rstrip("/") + "/", "robots.txt")
        robots: dict = {"url": robots_url, "status_code": None, "content": "", "error": None}
        crawl_error = None
        robots_blocked = False
        robots_directive = None
        _renew_lease(db, job_id, lease_token)
        try:
            robots_result = _control_response(active_crawler, robots_url, base_url=base_url, allow_loopback=is_synthetic)
            robots.update({"url": robots_result.url, "status_code": robots_result.status_code, "content": robots_result.content, "error": robots_result.error})
        except (CrawlError, ValueError) as exc:
            robots["error"] = str(exc)

        sitemap_url = None
        for line in str(robots.get("content") or "").splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip().lower() == "sitemap" and value.strip():
                candidate = urljoin(robots_url, value.strip())
                if _site_origin_key(candidate) == _site_origin_key(base_url) and is_sitemap_url(candidate):
                    sitemap_url = candidate
                else:
                    sitemap_url = candidate
                break
        sitemap_url = sitemap_url or urljoin(base_url.rstrip("/") + "/", "sitemap.xml")
        sitemap: dict = {"url": sitemap_url, "status_code": None, "content": "", "error": None}
        if _site_origin_key(sitemap_url) != _site_origin_key(base_url) or not is_sitemap_url(sitemap_url):
            sitemap["error"] = "advertised sitemap URL is not a same-origin sitemap XML resource"
        else:
            _renew_lease(db, job_id, lease_token)
            try:
                sitemap_result = _control_response(active_crawler, sitemap_url, base_url=base_url, allow_loopback=is_synthetic)
                sitemap.update({"url": sitemap_result.url, "status_code": sitemap_result.status_code, "content": sitemap_result.content, "error": sitemap_result.error})
            except (CrawlError, ValueError) as exc:
                sitemap["error"] = str(exc)

        # The root page is never requested until robots.txt is known to permit it.
        robots_allowed, robots_directive = _robots_policy(robots, target_url)
        pending: list[tuple[str, str | None]] = [(target_url, None)]
        captured: list[dict] = []
        link_checks_by_page: dict[str, list[dict]] = {}
        parent_pages: dict[str, set[str]] = {}
        seen_targets: set[str] = set()
        observed_results: dict[str, dict] = {}
        rate_limited = False
        page_limit = policy["audit_page_limit"]

        while pending and len(captured) < page_limit and not rate_limited:
            requested_url, source_url = pending.pop(0)
            requested_url = urldefrag(requested_url)[0]
            if source_url:
                parent_pages.setdefault(requested_url, set()).add(source_url)
            if requested_url in observed_results:
                known = observed_results[requested_url]
                if source_url:
                    link_checks_by_page.setdefault(source_url, []).append({"url": requested_url, "in_scope": True, "status_code": known["status_code"], "error": known.get("error"), "redirect_chain": known.get("redirect_chain", [])})
                continue
            if requested_url in seen_targets:
                continue
            seen_targets.add(requested_url)

            if _site_origin_key(requested_url) != _site_origin_key(base_url) or not _path_is_allowed(requested_url, paths):
                if source_url:
                    link_checks_by_page.setdefault(source_url, []).append({"url": requested_url, "in_scope": False, "status_code": None, "error": None})
                continue
            allowed_by_robots, directive = _robots_policy(robots, requested_url)
            if allowed_by_robots is not True:
                blocked = allowed_by_robots is False
                if source_url:
                    link_checks_by_page.setdefault(source_url, []).append({"url": requested_url, "in_scope": True, "status_code": 0, "error": None if blocked else "robots policy unavailable", "blocked_by_robots": blocked})
                if not captured:
                    crawl_error = "robots.txt did not provide a reliable allow decision" if not blocked else None
                    robots_blocked = blocked
                    robots_directive = directive
                    captured.append({"url": requested_url, "requested_url": requested_url, "status_code": 0, "headers": {}, "content": "", "title": None, "error": crawl_error, "redirect_chain": [], "blocked_by_robots": blocked, "robots_directive": directive})
                    observed_results[requested_url] = {"status_code": 0, "error": crawl_error, "redirect_chain": []}
                continue

            _renew_lease(db, job_id, lease_token)
            try:
                if isinstance(active_crawler, FixtureCrawler):
                    result = active_crawler.fetch(requested_url, base_url=base_url, allowed_paths=paths, allow_loopback=is_synthetic, redirect_allowed=lambda target: _robots_policy(robots, target)[0] is True)
                else:
                    result = active_crawler.fetch(requested_url, base_url=base_url, allowed_paths=paths, allow_loopback=is_synthetic)
                data = _result_data(result)
            except (CrawlError, ValueError) as exc:
                data = {"url": requested_url, "status_code": 0, "headers": {}, "content": "", "title": None, "error": str(exc), "redirect_chain": []}
            data["requested_url"] = requested_url
            data["blocked_by_robots"] = "robots" in str(data.get("error") or "").lower() and "disallow" in str(data.get("error") or "").lower()
            data["robots_directive"] = None
            observed_results[requested_url] = {"status_code": data["status_code"], "error": data.get("error"), "redirect_chain": data.get("redirect_chain", [])}
            if source_url:
                for parent in parent_pages.get(requested_url, {source_url}):
                    link_checks_by_page.setdefault(parent, []).append({"url": requested_url, "in_scope": True, "status_code": data["status_code"], "error": data.get("error"), "redirect_chain": data.get("redirect_chain", [])})
            if data["status_code"] != 0:
                captured.append(data)
            elif not captured:
                captured.append(data)
            if data["status_code"] == 429:
                rate_limited = True
                continue
            if not (200 <= data["status_code"] < 300) or (data.get("headers", {}).get("content-type", "").split(";", 1)[0].lower() not in {"text/html", "application/xhtml+xml"}):
                continue

            parsed = parse_document(data["content"])
            hrefs = parsed.links
            if len(hrefs) > MAX_CAPTURED_LINKS_PER_PAGE:
                link_checks_by_page.setdefault(data["url"], []).append({"url": None, "in_scope": True, "status_code": 0, "error": "link extraction was bounded to the first 100 anchors"})
            for href in hrefs[:MAX_CAPTURED_LINKS_PER_PAGE]:
                absolute = urldefrag(urljoin(data["url"], href))[0]
                if _site_origin_key(absolute) != _site_origin_key(base_url) or not _path_is_allowed(absolute, paths):
                    link_checks_by_page.setdefault(data["url"], []).append({"url": absolute, "in_scope": False, "status_code": None, "error": None})
                    continue
                if absolute in observed_results:
                    observed = observed_results[absolute]
                    link_checks_by_page.setdefault(data["url"], []).append({"url": absolute, "in_scope": True, "status_code": observed["status_code"], "error": observed.get("error"), "redirect_chain": observed.get("redirect_chain", [])})
                    continue
                parent_pages.setdefault(absolute, set()).add(data["url"])
                if absolute not in seen_targets:
                    pending.append((absolute, data["url"]))

        for pending_url, _ in pending:
            for parent in parent_pages.get(pending_url, set()):
                link_checks_by_page.setdefault(parent, []).append({"url": pending_url, "in_scope": True, "status_code": 0, "error": "bounded audit page sample was reached"})

        snapshots: list[PageSnapshot] = []
        snapshot_data: list[dict] = []
        for data in captured:
            requested_url = data.get("requested_url") or data["url"] or target_url
            final_url = data["url"] or requested_url
            page = _get_or_create_page(db, site_id, final_url)
            content_hash = sha256(data["content"].encode("utf-8")).hexdigest()
            content_type = data["headers"].get("content-type")
            document = parse_document(data["content"], requested_url=requested_url, final_url=final_url)
            snapshot = PageSnapshot(page_id=page.id, job_id=job_id, url=final_url, requested_url=requested_url, final_url=final_url, status_code=data["status_code"], title=document.title, content_hash=content_hash, content_type=content_type, content=data["content"], headers_json=json.dumps(data["headers"], sort_keys=True), is_synthetic=is_synthetic, audit_rule_version=RULE_VERSION, parser_version=PARSER_VERSION, declared_canonical_json=json.dumps(document.canonical, ensure_ascii=True, separators=(",", ":")), normalized_canonical_json=json.dumps(document.normalized_canonicals, ensure_ascii=True, separators=(",", ":")), metadata_hash=document.metadata_hash, body_hash=document.body_hash)
            db.add(snapshot)
            db.flush()
            snapshots.append(snapshot)
            snapshot_data.append({**data, "url": final_url, "requested_url": requested_url, "final_url": final_url, "id": snapshot.id, "page_id": snapshot.page_id, "content_hash": content_hash, "content_type": content_type})

        sample_pages = [{"snapshot_id": data["id"], "url": data["url"], "requested_url": data["requested_url"], "final_url": data["final_url"], "status_code": data["status_code"], "title": data["title"], "content_hash": data["content_hash"]} for data in snapshot_data]
        frozen_input = {"policy": policy, "robots": {"url": robots.get("url"), "status_code": robots.get("status_code"), "error": robots.get("error"), "content_hash": sha256(str(robots.get("content") or "").encode("utf-8")).hexdigest()}, "sitemap": sitemap, "crawl": {data["url"]: {"requested_url": data["requested_url"], "final_url": data["final_url"], "error": data.get("error"), "redirect_chain": data.get("redirect_chain", []), "blocked_by_robots": data.get("blocked_by_robots", False), "robots_directive": data.get("robots_directive")} for data in snapshot_data}, "link_checks_by_page": link_checks_by_page, "sample_pages": sample_pages}
        job_record = db.get(Job, job_id)
        if job_record is None:
            raise ValueError("job not found")
        job_record.audit_input_json = json.dumps(frozen_input, sort_keys=True, separators=(",", ":"))
        for snapshot, data in zip(snapshots, snapshot_data):
            page_input = {
                "policy": policy,
                "requested_url": data["requested_url"],
                "robots": frozen_input["robots"],
                "sitemap": sitemap,
                "crawl": frozen_input["crawl"].get(data["url"], {}),
                "link_checks": link_checks_by_page.get(data["url"], []),
                "sample_pages": sample_pages,
                "headers": data["headers"],
            }
            rule_results = evaluate_snapshot(data, page_input)
            expected_rule_ids = {rule[0] for rule in RULES}
            if (
                len(rule_results) != len(RULES)
                or {result.get("rule_id") for result in rule_results} != expected_rule_ids
                or any(result.get("version") != RULE_VERSION for result in rule_results)
            ):
                raise RuntimeError(f"audit rule evaluation returned an incomplete rule set for snapshot {snapshot.id}")
            for result in rule_results:
                db.add(AuditRuleResult(snapshot_id=snapshot.id, rule_id=result["rule_id"], version=result["version"], scope=result["scope"], status=result["status"], severity=result["severity"], message=result["message"], evidence_json=json.dumps(result["evidence"], sort_keys=True, separators=(",", ":")), remediation_hint=result["remediation_hint"]))
            for finding in map_rule_failures_to_findings(rule_results):
                db.add(AuditFinding(snapshot_id=snapshot.id, code=finding["code"], severity=finding["severity"], message=finding["message"], evidence_json=json.dumps(finding["evidence"], sort_keys=True, separators=(",", ":"))))
        db.flush()
        now = utcnow()
        finish = db.execute(
            update(Job)
            .execution_options(synchronize_session=False)
            .where(
                Job.id == job_id,
                Job.status == JobStatus.running.value,
                Job.lease_token == lease_token,
                Job.lease_expires_at > now,
            )
            .values(
                status=JobStatus.succeeded.value,
                finished_at=now,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        if finish.rowcount != 1:
            db.rollback()
            raise JobLeaseLost(f"worker lease for job {job_id} expired before commit")
        db.commit()
    except JobLeaseLost:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        if not _mark_failed(db, job_id, lease_token, exc):
            raise JobLeaseLost(f"worker lease for job {job_id} was lost during failure handling") from exc
        raise
    job = db.get(Job, job_id)
    if job is None:
        raise ValueError("job not found")
    # Bulk status updates intentionally skip ORM identity-map synchronization to
    # avoid SQLite's naive/aware datetime evaluator. Refresh before returning so
    # callers receive the committed status and lease fields.
    db.refresh(job)
    return job


def execute_job(db: Session, job_id: int, crawler: FixtureCrawler | None = None) -> Job:
    lease_token = claim_job(db, job_id)
    return execute_claimed_job(db, job_id, lease_token, crawler=crawler)
