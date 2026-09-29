from __future__ import annotations

import json
from hashlib import sha256
from urllib.parse import urlsplit

from sqlalchemy import select

from backend.audit import execute_job
from backend.audit_rules import RULES, RULE_VERSION
from backend.crawler import CrawlResult
from backend.database import SessionLocal
from backend.models import AuditFinding, AuditRuleResult, Job, PageSnapshot, Site, Workspace


class ControlledCrawler:
    def __init__(self, base_url: str, *, robots_status: int = 200) -> None:
        self.base_url = base_url
        self.robots_status = robots_status
        self.calls: list[str] = []

    def fetch_control(self, url: str, *, base_url: str, allow_loopback: bool) -> CrawlResult:
        self.calls.append(url)
        path = urlsplit(url).path
        if path == "/robots.txt":
            content = f"User-agent: TradeVisibilityAuditBot\nAllow: /\nSitemap: {self.base_url}/sitemap.xml\n"
            return CrawlResult(url, self.robots_status, {"content-type": "text/plain"}, content if self.robots_status == 200 else "", None)
        assert path == "/sitemap.xml"
        content = f"<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\"><url><loc>{self.base_url}/</loc></url></urlset>"
        return CrawlResult(url, 200, {"content-type": "application/xml"}, content, None)

    def fetch(self, url: str, *, base_url: str, allowed_paths: list[str], allow_loopback: bool) -> CrawlResult:
        self.calls.append(url)
        if url.rstrip("/") == self.base_url.rstrip("/"):
            content = "<html><head></head><body><h1>Home</h1><a href='/ok'>ok</a></body></html>"
            return CrawlResult(url, 200, {"content-type": "text/html; charset=utf-8"}, content, None)
        content = "<html><head><title>OK</title><meta name='description' content='ok'></head><body><h1>OK</h1></body></html>"
        return CrawlResult(url, 200, {"content-type": "text/html; charset=utf-8"}, content, "OK")


def _create_job(*, base_url: str, audit_page_limit: int = 2) -> int:
    with SessionLocal() as db:
        workspace = Workspace(name="Phase 2 workspace", external_id=f"phase2-{base_url.rsplit(':', 1)[-1]}")
        db.add(workspace)
        db.flush()
        site = Site(
            workspace_id=workspace.id,
            name="Phase 2 site",
            base_url=base_url,
            allowed_paths='["/"]',
            is_synthetic=True,
            audit_page_limit=audit_page_limit,
            audit_policy_json=json.dumps({"expected_accessible": True, "expected_indexable": True}),
        )
        db.add(site)
        db.flush()
        job = Job(site_id=site.id, status="queued")
        db.add(job)
        db.commit()
        return job.id


def test_successful_job_freezes_probe_context_and_persists_all_rules():
    base_url = "http://127.0.0.1:9901"
    crawler = ControlledCrawler(base_url)
    job_id = _create_job(base_url=base_url)

    with SessionLocal() as db:
        job = execute_job(db, job_id, crawler=crawler)
        assert job.status == "succeeded"
        frozen = json.loads(job.audit_input_json)
        snapshots = db.scalars(select(PageSnapshot).where(PageSnapshot.job_id == job_id).order_by(PageSnapshot.id)).all()
        assert len(snapshots) == 2
        assert frozen["robots"]["status_code"] == 200
        assert frozen["robots"]["content_hash"] == sha256(crawler.fetch_control(base_url + "/robots.txt", base_url=base_url, allow_loopback=True).content.encode()).hexdigest()
        assert frozen["sitemap"]["status_code"] == 200
        assert frozen["sitemap"]["content"].startswith("<urlset")
        assert frozen["link_checks_by_page"]
        assert any(entry["url"].endswith("/ok") for values in frozen["link_checks_by_page"].values() for entry in values)
        for snapshot in snapshots:
            results = db.scalars(select(AuditRuleResult).where(AuditRuleResult.snapshot_id == snapshot.id)).all()
            assert len(results) == len(RULES) == 12
            assert {result.rule_id for result in results} == {rule[0] for rule in RULES}
            assert {result.version for result in results} == {RULE_VERSION}

        missing_title = snapshots[0]
        finding = db.scalar(select(AuditFinding).where(AuditFinding.snapshot_id == missing_title.id, AuditFinding.code == "TITLE_MISSING"))
        title_rule = db.scalar(select(AuditRuleResult).where(AuditRuleResult.snapshot_id == missing_title.id, AuditRuleResult.rule_id == "title_present"))
        assert finding is not None
        assert title_rule is not None and title_rule.status == "fail"


def test_unavailable_robots_produces_unknown_rule_without_fetching_page():
    base_url = "http://127.0.0.1:9902"
    crawler = ControlledCrawler(base_url, robots_status=500)
    job_id = _create_job(base_url=base_url, audit_page_limit=1)

    with SessionLocal() as db:
        execute_job(db, job_id, crawler=crawler)
        snapshot = db.scalar(select(PageSnapshot).where(PageSnapshot.job_id == job_id))
        assert snapshot is not None
        robots_rule = db.scalar(select(AuditRuleResult).where(AuditRuleResult.snapshot_id == snapshot.id, AuditRuleResult.rule_id == "robots_access"))
        assert robots_rule is not None and robots_rule.status == "unknown"
        assert not any(urlsplit(url).path == "/" for url in crawler.calls)
        assert db.scalar(select(AuditRuleResult.id).where(AuditRuleResult.snapshot_id == snapshot.id)) is not None
        assert len(db.scalars(select(AuditRuleResult).where(AuditRuleResult.snapshot_id == snapshot.id)).all()) == 12
