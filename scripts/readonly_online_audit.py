"""Run a bounded, read-only audit against explicitly supplied HTTPS origins.

The command uses the same crawler and rule engine as the API worker. It creates
an isolated temporary SQLite database and emits only an evidence report; it
does not call publication or CMS endpoints.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origins", nargs="+", help="HTTPS site origins to sample")
    parser.add_argument("--page-limit", type=int, default=3, help="bounded pages per origin (1-50)")
    parser.add_argument("--output", type=Path, required=True, help="JSON evidence report path")
    return parser.parse_args()


def _validate_origin(value: str) -> str:
    origin = value.rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("origins must be HTTPS URLs without credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("origins must be root URLs without query or fragment")
    return origin


def _decode_json(value: str | None, default: object) -> object:
    """Decode persisted evidence without letting a malformed row abort reporting."""
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def _audit(origin: str, *, page_limit: int) -> dict[str, object]:
    from sqlalchemy import select

    from backend.audit import execute_job
    from backend.crawler import FixtureCrawler
    from backend.database import SessionLocal, init_db
    from backend.models import AuditFinding, AuditRuleResult, Job, PageSnapshot, Site, Workspace

    direct_crawler = FixtureCrawler(request_interval=0)
    root_result = direct_crawler.fetch(origin, base_url=origin, allowed_paths=["/"], allow_loopback=False)
    robots_result = direct_crawler.fetch_control(f"{origin}/robots.txt", base_url=origin, allow_loopback=False)
    sitemap_url = f"{origin}/sitemap.xml"
    for line in str(robots_result.content or "").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() == "sitemap" and value.strip():
            sitemap_url = urljoin(f"{origin}/", value.strip())
            break
    sitemap_result = direct_crawler.fetch_control(sitemap_url, base_url=origin, allow_loopback=False)

    def direct_result(path: str, result) -> dict[str, object]:
        return {
            "path": path,
            "method": "GET",
            "status_code": result.status_code,
            "redirect_count": len(result.redirect_chain),
            "redirect_chain": list(result.redirect_chain),
            "content_bytes": len(result.content.encode("utf-8")),
            "title": result.title,
            "error": result.error,
            "final_url": result.url,
        }

    init_db()
    external_id = f"online-audit-{uuid4().hex}"
    with SessionLocal() as db:
        workspace = Workspace(name="Read-only online audit", external_id=external_id)
        db.add(workspace)
        db.flush()
        site = Site(
            workspace_id=workspace.id,
            name=origin,
            base_url=origin,
            allowed_paths='["/"]',
            is_synthetic=False,
            audit_policy_json=json.dumps(
                {"site_origin": origin, "preferred_origin": origin, "expected_accessible": True, "expected_indexable": True},
                sort_keys=True,
                separators=(",", ":"),
            ),
            audit_page_limit=page_limit,
        )
        db.add(site)
        db.flush()
        job = Job(site_id=site.id, status="queued")
        db.add(job)
        db.commit()
        job_id = job.id

        execute_job(db, job_id)
        db.expire_all()
        job = db.get(Job, job_id)
        if job is None:
            raise RuntimeError("audit job disappeared")
        snapshots = db.scalars(
            select(PageSnapshot).where(PageSnapshot.job_id == job_id).order_by(PageSnapshot.id)
        ).all()
        input_evidence = json.loads(job.audit_input_json or "{}")
        rule_rows = db.scalars(
            select(AuditRuleResult)
            .where(AuditRuleResult.snapshot_id.in_([item.id for item in snapshots]))
            .order_by(AuditRuleResult.id)
        ).all()
        finding_rows = db.scalars(
            select(AuditFinding)
            .where(AuditFinding.snapshot_id.in_([item.id for item in snapshots]))
            .order_by(AuditFinding.id)
        ).all()
        snapshots_by_id = {snapshot.id: snapshot for snapshot in snapshots}

        status_counts: dict[str, int] = {}
        for row in rule_rows:
            status_counts[row.status] = status_counts.get(row.status, 0) + 1
        return {
            "origin": origin,
            "direct_control_checks": [
                direct_result("/", root_result),
                direct_result("/robots.txt", robots_result),
                direct_result(sitemap_url.removeprefix(origin) or "/", sitemap_result),
            ],
            "job_id": job_id,
            "status": job.status,
            "page_limit": page_limit,
            "pages_sampled": len(snapshots),
            "findings": len(finding_rows),
            "finding_details": [
                {
                    "snapshot_url": snapshots_by_id[row.snapshot_id].url,
                    "code": row.code,
                    "severity": row.severity,
                    "message": row.message,
                    "evidence": _decode_json(row.evidence_json, {}),
                }
                for row in finding_rows
            ],
            "rule_result_count": len(rule_rows),
            "rule_status_counts": status_counts,
            "rule_results": [
                {
                    "snapshot_url": snapshots_by_id[row.snapshot_id].url,
                    "rule_id": row.rule_id,
                    "version": row.version,
                    "scope": row.scope,
                    "status": row.status,
                    "severity": row.severity,
                    "message": row.message,
                    "evidence": _decode_json(row.evidence_json, {}),
                    "remediation_hint": row.remediation_hint,
                }
                for row in rule_rows
            ],
            "robots": {
                "url": input_evidence.get("robots", {}).get("url"),
                "status_code": input_evidence.get("robots", {}).get("status_code"),
                "error": input_evidence.get("robots", {}).get("error"),
            },
            "sitemap": {
                "url": input_evidence.get("sitemap", {}).get("url"),
                "status_code": input_evidence.get("sitemap", {}).get("status_code"),
                "error": input_evidence.get("sitemap", {}).get("error"),
            },
            "pages": [
                {
                    "url": snapshot.url,
                    "status_code": snapshot.status_code,
                    "title": snapshot.title,
                    "content_hash": snapshot.content_hash,
                    "content_type": snapshot.content_type,
                }
                for snapshot in snapshots
            ],
        }


def main() -> None:
    args = _parse_args()
    if not 1 <= args.page_limit <= 50:
        raise SystemExit("--page-limit must be between 1 and 50")
    origins = [_validate_origin(item) for item in args.origins]

    with tempfile.NamedTemporaryFile(prefix="readonly-online-audit-", suffix=".sqlite", delete=False) as database_file:
        database_path = Path(database_file.name)
    os.environ["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    os.environ["ALLOW_LOOPBACK"] = "false"
    os.environ["CRAWLER_TIMEOUT"] = os.getenv("CRAWLER_TIMEOUT", "20")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    try:
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "live_https_readonly",
            "database": "temporary_sqlite_deleted_after_run",
            "origins": [_audit(origin, page_limit=args.page_limit) for origin in origins],
            "limitations": [
                "This is a bounded HTML/robots/sitemap sample, not a full-site crawl.",
                "The run does not measure search ranking, consumer AI citations, traffic, leads, or revenue.",
                "No publication, CMS write, PR, or deployment endpoint was called.",
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        try:
            from backend.database import engine

            engine.dispose()
        except (ImportError, RuntimeError):
            pass
        database_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
