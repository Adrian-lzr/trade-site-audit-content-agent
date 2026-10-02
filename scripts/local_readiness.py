"""Run the five-page local publication, deployment, and rollback rehearsal.

The rehearsal creates a temporary SQLite database and an isolated local Git
repository. It exercises the same API and publication worker used by the
application, but it never calls a remote Git, CMS, deployment, or website
endpoint. It confirms that local Git artifacts do not count as fresh target
page verification and that rollback remains blocked without that evidence.
Pass ``--workdir`` to retain generated artifacts for inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PAGE_PATHS = (
    "/products/vx-21.html",
    "/products/vx-22.html",
    "/products/vx-23.html",
    "/applications/food-processing.html",
    "/engineering/pressure.html",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON evidence path; the same report is always printed",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        help="new empty directory to retain the SQLite DB and local Git repository",
    )
    return parser.parse_args()


def _run_git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {detail[:300]}")
    return result.stdout.strip()


def _initialise_repository(repository: Path) -> str:
    repository.mkdir(parents=True, exist_ok=True)
    _run_git(repository, "init", "-q", "--initial-branch=main")
    (repository / "README.md").write_text("isolated local readiness rehearsal\n", encoding="utf-8")
    _run_git(repository, "add", "README.md")
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Readiness Harness",
            "-c",
            "user.email=readiness@example.invalid",
            "commit",
            "-q",
            "-m",
            "initial isolated repository",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return _run_git(repository, "rev-parse", "HEAD")


def _assert_response(response: Any, expected: int, action: str) -> dict[str, Any]:
    if response.status_code != expected:
        raise RuntimeError(f"{action} failed with HTTP {response.status_code}: {response.text[:500]}")
    return response.json()


def _build_five_page_fixture() -> tuple[int, int, list[tuple[int, int, str]]]:
    """Create five immutable snapshots and return workspace/site/page data."""

    from backend.database import SessionLocal
    from backend.models import Page, PageSnapshot, Site, Workspace

    with SessionLocal() as db:
        workspace = Workspace(name="Local readiness workspace")
        db.add(workspace)
        db.flush()
        site = Site(
            workspace_id=workspace.id,
            name="Five page isolated site",
            base_url="https://readiness.example.test",
            allowed_paths=json.dumps(["/"]),
            is_synthetic=True,
        )
        db.add(site)
        db.flush()
        rows: list[tuple[int, int, str]] = []
        for index, path in enumerate(PAGE_PATHS, start=1):
            url = f"{site.base_url}{path}"
            page = Page(site_id=site.id, canonical_url=url)
            db.add(page)
            db.flush()
            content = (
                f"<html><head><title>VX-{20 + index} baseline</title></head>"
                f"<body><h1>VX-{20 + index}</h1><p>Local baseline page {index}.</p></body></html>"
            )
            snapshot = PageSnapshot(
                page_id=page.id,
                url=url,
                status_code=200,
                title=f"VX-{20 + index} baseline",
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                headers_json="{}",
                is_synthetic=True,
                artifact_uri=f"fixture://readiness{path}",
                parser_version="local-readiness-v1",
            )
            db.add(snapshot)
            db.flush()
            rows.append((page.id, snapshot.id, snapshot.content_hash))
        db.commit()
        return workspace.id, site.id, rows


def _run_rehearsal(root: Path) -> dict[str, Any]:
    database_path = root / "readiness.sqlite"
    repository = root / "repository"
    os.environ["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    os.environ["GIT_PUBLISH_REPOSITORY"] = str(repository)
    os.environ["GIT_PUBLISH_TARGET"] = "readiness"
    os.environ["ALLOW_LOOPBACK"] = "false"
    sys.path.insert(0, str(ROOT))

    from fastapi.testclient import TestClient

    from backend.app import app
    from backend.database import SessionLocal, init_db
    from backend.models import OutboxEvent, PublicationAttempt
    from backend.publication_worker import PublicationWorker

    init_db()
    initial_commit = _initialise_repository(repository)
    workspace_id, site_id, pages = _build_five_page_fixture()
    changes: list[dict[str, Any]] = []

    with TestClient(app) as client:
        for index, (_, snapshot_id, snapshot_hash) in enumerate(pages, start=1):
            created = _assert_response(
                client.post(
                    f"/api/workspaces/{workspace_id}/sites/{site_id}/changes",
                    json={
                        "workspace_id": workspace_id,
                        "site_id": site_id,
                        "base_snapshot_id": snapshot_id,
                        "base_content_hash": snapshot_hash,
                        "field_diff": {"title": f"VX-{20 + index} procurement guide"},
                        "fact_versions": [],
                        "title": f"Readiness page {index}",
                    },
                ),
                201,
                f"create page {index} change",
            )
            _assert_response(
                client.post(
                    f"/api/changes/{created['id']}/submit-approval?workspace_id={workspace_id}",
                    json={"expected_version": 1},
                ),
                200,
                f"submit page {index} change",
            )
            approved = _assert_response(
                client.post(
                    f"/api/changes/{created['id']}/approval?workspace_id={workspace_id}",
                    json={
                        "reviewer": "local-readiness",
                        "decision": "approved",
                        "revision_id": created["revision"]["id"],
                        "revision_hash": created["revision"]["content_hash"],
                        "expected_version": 1,
                    },
                ),
                200,
                f"approve page {index} change",
            )
            if approved["state"] != "approved":
                raise RuntimeError(f"page {index} did not enter approved state")
            publication = _assert_response(
                client.post(
                    f"/api/changes/{created['id']}/publish?workspace_id={workspace_id}",
                    json={"expected_version": 1},
                ),
                200,
                f"queue page {index} publication",
            )
            duplicate = client.post(
                f"/api/changes/{created['id']}/publish?workspace_id={workspace_id}",
                json={"expected_version": 1},
            )
            # Once the first request moves the change to ``publishing``, a
            # second API request is rejected before it can enqueue anything.
            # The durable idempotency key is still checked below at the
            # outbox/worker boundary.
            if duplicate.status_code != 409:
                raise RuntimeError(
                    f"repeat page {index} publication should be rejected after queueing, "
                    f"got HTTP {duplicate.status_code}"
                )
            changes.append({"change": created, "publication": publication})

    worker = PublicationWorker()
    for index in range(1, len(changes) + 1):
        if not worker.run_once():
            raise RuntimeError(f"publication worker did not process page {index}")

    with SessionLocal() as db:
        attempts = [db.get(PublicationAttempt, item["publication"]["id"]) for item in changes]
        if any(attempt is None or attempt.status != "submitted" for attempt in attempts):
            raise RuntimeError("not all five local publications produced submitted attempts")
        commits = [attempt.commit_sha for attempt in attempts if attempt and attempt.commit_sha]
        branches = [attempt.branch for attempt in attempts if attempt and attempt.branch]
        if len(commits) != 5 or len(set(commits)) != 5 or len(branches) != 5:
            raise RuntimeError("local publication commits or branches were not unique")
        outbox_count = db.query(OutboxEvent).filter(OutboxEvent.event_type == "change.publish_requested").count()
        publication_count = db.query(PublicationAttempt).filter(PublicationAttempt.rollback_of_attempt_id.is_(None)).count()
        if outbox_count != 5 or publication_count != 5:
            raise RuntimeError("duplicate publication calls created extra durable records")
        attempt_payload = [
            {"id": attempt.id, "change_request_id": attempt.change_request_id, "branch": attempt.branch, "commit_sha": attempt.commit_sha}
            for attempt in attempts
            if attempt is not None
        ]

    with TestClient(app) as client:
        for index, item in enumerate(attempt_payload, start=1):
            _assert_response(
                client.post(
                    f"/api/publication-attempts/{item['id']}/deployment?workspace_id={workspace_id}",
                    json={"status": "deployed", "commit_sha": item["commit_sha"], "deployment_id": f"local-deploy-{index}"},
                ),
                200,
                f"confirm page {index} deployment",
            )
            verified = _assert_response(
                client.post(f"/api/publication-attempts/{item['id']}/verify?workspace_id={workspace_id}"),
                200,
                f"verify page {index} deployment",
            )
            if verified["deployment_status"] != "verification_unavailable":
                raise RuntimeError(f"page {index} local rehearsal claimed target verification")

    for item in attempt_payload:
        marker_path = f".trade-visibility/changes/{item['change_request_id']}/revision-1.json"
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{item['commit_sha']}:{marker_path}"],
            cwd=repository,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"published marker file is missing for change {item['change_request_id']}")

    first = attempt_payload[0]
    with TestClient(app) as client:
        rollback_response = client.post(
            f"/api/publication-attempts/{first['id']}/rollback?workspace_id={workspace_id}",
            json={"expected_current_sha": first["commit_sha"], "reason": "Local readiness rollback rehearsal"},
        )
        repeated_rollback_response = client.post(
            f"/api/publication-attempts/{first['id']}/rollback?workspace_id={workspace_id}",
            json={"expected_current_sha": first["commit_sha"], "reason": "Local readiness rollback rehearsal"},
        )
        if rollback_response.status_code != 409 or repeated_rollback_response.status_code != 409:
            raise RuntimeError("rollback must remain blocked without fresh target-page verification")

    return {
        "status": "passed",
        "database": "sqlite",
        "database_path": str(database_path),
        "repository": str(repository),
        "initial_commit": initial_commit,
        "pages": len(PAGE_PATHS),
        "published_attempts": len(attempt_payload),
        "deployment_callbacks": len(attempt_payload),
        "verified_deployments": 0,
        "verification_unavailable": len(attempt_payload),
        "rollback": {
            "source_attempt_id": first["id"],
            "source_commit": first["commit_sha"],
            "status": "blocked_external",
            "reason": "fresh target-page read is not configured",
        },
        "idempotency": {
            "publication_replay_rejected": True,
            "publication_outbox_unique": True,
            "rollback_rejected_without_page_evidence": True,
        },
        "external_side_effects": {
            "remote_git_push": False,
            "cms_write": False,
            "external_deployment": False,
            "deployment_callbacks_are_local_state": True,
        },
    }


def main() -> int:
    args = _parse_args()
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.workdir is None:
        temporary = tempfile.TemporaryDirectory(prefix="trade-visibility-readiness-")
        root = Path(temporary.name)
    else:
        root = args.workdir.expanduser().resolve()
        if root.exists():
            if any(root.iterdir()):
                raise SystemExit(f"--workdir must be a new empty directory: {root}")
        else:
            root.mkdir(parents=True)

    report: dict[str, Any]
    cleanup_error: str | None = None
    try:
        report = _run_rehearsal(root)
        report["workdir_retained"] = args.workdir is not None
    except Exception as exc:
        report = {"status": "failed", "error": str(exc), "workdir": str(root)}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 1
    finally:
        # SQLAlchemy's module-level engine can retain the SQLite file on
        # Windows after TestClient has closed. Dispose it before removing the
        # temporary rehearsal directory.
        try:
            from backend.database import engine

            engine.dispose()
        except Exception:
            pass
        if temporary is not None:
            try:
                temporary.cleanup()
            except Exception as exc:  # pragma: no cover - platform-specific cleanup
                cleanup_error = f"temporary readiness directory cleanup failed: {exc}"

    if cleanup_error:
        report = {"status": "failed", "error": cleanup_error, "workdir": str(root)}

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
