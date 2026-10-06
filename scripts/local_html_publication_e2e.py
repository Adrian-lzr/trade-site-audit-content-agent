"""Run a local-only constrained HTML publication and deployment rehearsal.

This fixture is intentionally independent from the API publication worker's
Git marker flow.  It exercises the page adapter contract itself:

``approved revision -> constrained HTML apply -> fresh local observation ->
verified -> guarded rollback``

The target is an isolated temporary/local Git repository.  No HTTP request,
CMS endpoint, remote Git push, deployment service, or supplied production
site is contacted.  The emitted report is therefore ``fixture_verified`` and
must never be treated as production deployment evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Scripts are also invoked directly by the local readiness commands, where
# Python does not automatically put the repository root on ``sys.path``.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.publishers import (
    PrepareRequest,
    RollbackConflictError,
    StaticHtmlAdapter,
)
from backend.publishers.base import content_hash
from backend.services.deployment import (
    TargetObservation,
    callback_signature,
    configure_target_observer,
    fetch_target_observation,
    verify_callback_signature,
    verify_deployment,
)


HTML = """<!doctype html>
<html><head>
  <title>Fixture baseline</title>
  <meta name="description" content="Baseline buyer description" data-owner="fixture">
</head><body>
  <svg aria-hidden="true"><title>Icon title stays untouched</title></svg>
  <main data-publisher-field="body"><p>Baseline <strong>buyer information</strong>.</p></main>
  <section data-publisher-field="faq"><div class="faq-item"><h3>Old question</h3><p>Old answer</p></div></section>
  <footer data-keep="yes">Unrelated footer remains byte-for-byte.</footer>
</body></html>
"""


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {detail[:300]}")
    return result.stdout.strip()


def _commit(repository: Path, message: str) -> str:
    subprocess.run(["git", "add", "index.html"], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Local Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-q",
            "-m",
            message,
        ],
        cwd=repository,
        check=True,
    )
    return _git(repository, "rev-parse", "HEAD")


def _init_repository(root: Path) -> tuple[Path, Path, str, str]:
    repository = root / "site-repository"
    repository.mkdir(parents=True, exist_ok=True)
    page = repository / "index.html"
    page.write_text(HTML, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "--initial-branch=main"], cwd=repository, check=True)
    subprocess.run(["git", "add", "index.html"], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Local Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-q",
            "-m",
            "baseline fixture page",
        ],
        cwd=repository,
        check=True,
    )
    return repository, page, _git(repository, "rev-parse", "HEAD"), content_hash(page.read_bytes())


def _adapter(repository: Path) -> StaticHtmlAdapter:
    return StaticHtmlAdapter(
        {
            "sites": {
                "fixture": {
                    "repository": str(repository),
                    "pages": {
                        "home": {
                            "file": "index.html",
                            "fields": {
                                "title": {"kind": "title"},
                                "meta_description": {"kind": "meta", "name": "description"},
                                "body": {"kind": "marker", "marker": "body", "tag": "main"},
                                "faq": {"kind": "marker", "marker": "faq", "tag": "section"},
                            },
                        }
                    },
                }
            }
        }
    )


def run_rehearsal(root: Path) -> dict[str, Any]:
    """Run the complete local adapter/observer flow under ``root``."""

    repository, page, base_commit, base_content_hash = _init_repository(root)
    adapter = _adapter(repository)
    target = "fixture:home"
    revision_hash = hashlib.sha256(b"fixture-approved-revision-1").hexdigest()
    current_facts = [{"fact_id": 101, "version": 1, "predicate": "minimum_order_quantity"}]
    request = PrepareRequest(
        site="fixture",
        page="home",
        fields={
            "title": "Fixture procurement guide",
            "meta_description": "Approved buyer description",
            "body": '<p>Approved <a href="/quote">buyer details</a>.</p>',
            "faq": [
                {
                    "question": "What is the MOQ?",
                    "answer": "The approved answer is <strong>20 pieces</strong>.",
                }
            ],
        },
        expected_base_content_hash=base_content_hash,
        expected_base_commit=base_commit,
        revision_hash=revision_hash,
        current_facts=current_facts,
        idempotency_key="fixture-home-revision-1",
        provenance={"source": "local-fixture", "is_synthetic": True},
    )

    before = page.read_text(encoding="utf-8")
    prepared = adapter.prepare(request)
    if page.read_text(encoding="utf-8") != before:
        raise RuntimeError("prepare unexpectedly changed the target HTML")
    approval = prepared.approval(approver="fixture-reviewer", approval_id="fixture-approval-1")
    applied = adapter.apply(prepared, approval)
    applied_html = page.read_text(encoding="utf-8")
    if "Fixture procurement guide" not in applied_html or "Approved buyer description" not in applied_html:
        raise RuntimeError("approved fields were not applied to the fixture HTML")
    if "Icon title stays untouched" not in applied_html or "Unrelated footer remains byte-for-byte." not in applied_html:
        raise RuntimeError("unrelated HTML content was not preserved")
    deployed_commit = _commit(repository, "apply approved fixture HTML revision")

    def observe(observed_target: str, *, attempt: Any | None = None) -> TargetObservation:
        if observed_target != target:
            raise RuntimeError("fixture observer received an unexpected target")
        payload = page.read_bytes()
        return TargetObservation(
            target=observed_target,
            commit=_git(repository, "rev-parse", "HEAD"),
            revision_hash=prepared.revision_hash,
            content_hash=content_hash(payload),
            evidence={
                "source": "local-fixture-target-file",
                "is_synthetic": True,
                "fresh_read": True,
                "representation": "exact UTF-8 index.html bytes",
                "attempt_id": getattr(attempt, "id", None),
            },
        )

    configure_target_observer(observe)
    try:
        callback = {
            "attempt_id": 1,
            "status": "deployed",
            "commit_sha": deployed_commit,
            "deployment_id": "fixture-deployment-1",
            "nonce": "fixture-nonce-1",
            "revision_hash": prepared.revision_hash,
            "target": target,
        }
        signature = callback_signature(secret="fixture-callback-secret", **callback)
        if not verify_callback_signature(secret="fixture-callback-secret", signature=signature, **callback):
            raise RuntimeError("fixture callback signature did not verify")
        observation = fetch_target_observation(target, attempt=callback)
        verification = verify_deployment(
            expected_commit=deployed_commit,
            observed_commit=observation.commit,
            expected_revision_hash=prepared.revision_hash,
            observed_revision_hash=observation.revision_hash,
            expected_content_hash=applied.applied_content_hash,
            observed_content_hash=observation.content_hash,
        )
        if not verification.verified:
            raise RuntimeError(f"fresh fixture observation did not verify: {verification}")

        # A target-side manual edit must make rollback fail closed and leave
        # that edit untouched.  This is the guarded-rollback safety case.
        manual_html = applied_html.replace("Approved buyer description", "Manual target edit")
        page.write_text(manual_html, encoding="utf-8")
        try:
            adapter.rollback(applied, expected_current_content_hash=applied.applied_content_hash)
        except RollbackConflictError:
            rollback_conflict = True
        else:  # pragma: no cover - the guard is the assertion under test
            rollback_conflict = False
        if not rollback_conflict or page.read_text(encoding="utf-8") != manual_html:
            raise RuntimeError("rollback did not fail closed after a target-side edit")

        # Restore the observed approved bytes to model a target that is still
        # at the verified deployment, then perform the guarded rollback.
        page.write_text(applied_html, encoding="utf-8")
        rolled_back = adapter.rollback(
            applied,
            expected_current_content_hash=applied.applied_content_hash,
            reason="fixture rollback rehearsal",
        )
        if rolled_back.status != "rolled_back" or page.read_text(encoding="utf-8") != before:
            raise RuntimeError("guarded fixture rollback did not restore the exact baseline")
        rollback_replay = adapter.rollback(
            applied,
            expected_current_content_hash=applied.applied_content_hash,
            idempotency_key=rolled_back.idempotency_key,
        )
        if not rollback_replay.replayed or rollback_replay.changed:
            raise RuntimeError("rollback replay was not idempotent")
    finally:
        configure_target_observer(None)

    return {
        "status": "passed",
        "verification": "fixture_verified",
        "source": "local synthetic fixture only",
        "target": target,
        "repository": str(repository),
        "page": str(page),
        "stages": {
            "approved_revision": {
                "state": "approved",
                "revision_hash": prepared.revision_hash,
                "approval_id": approval.approval_id,
                "approver": approval.approver,
                "base_content_hash": base_content_hash,
                "base_commit": base_commit,
                "fact_set_hash": prepared.current_fact_set_hash,
                "is_synthetic": True,
            },
            "constrained_html_apply": {
                "status": applied.status,
                "changed": applied.changed,
                "replayed": applied.replayed,
                "applied_content_hash": applied.applied_content_hash,
                "unrelated_content_preserved": True,
            },
            "fresh_target_observation": {
                "target": observation.target,
                "commit": observation.commit,
                "revision_hash": observation.revision_hash,
                "content_hash": observation.content_hash,
                "deployment_commit": deployed_commit,
                "evidence": observation.evidence,
            },
            "verified": {
                "status": verification.status,
                "commit_matches": verification.commit_matches,
                "revision_matches": verification.revision_matches,
                "content_matches": verification.content_matches,
                "callback_signature_verified": True,
            },
            "guarded_rollback": {
                "manual_edit_conflict_rejected": rollback_conflict,
                "status": rolled_back.status,
                "restored_content_hash": rolled_back.restored_content_hash,
                "replay_rejected_as_new_write": rollback_replay.replayed and not rollback_replay.changed,
            },
        },
        "external_side_effects": {
            "http_requests": 0,
            "remote_git_push": False,
            "cms_write": False,
            "production_site_write": False,
            "is_synthetic": True,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, help="retain the local fixture repository")
    parser.add_argument("--output", type=Path, help="write JSON evidence to this path")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.workdir is None:
        temporary = tempfile.TemporaryDirectory(prefix="trade-visibility-html-e2e-")
        root = Path(temporary.name)
    else:
        root = args.workdir.expanduser().resolve()
        if root.exists() and any(root.iterdir()):
            raise SystemExit(f"--workdir must be a new empty directory: {root}")
        root.mkdir(parents=True, exist_ok=True)
    try:
        report = run_rehearsal(root)
    except Exception as exc:
        report = {"status": "failed", "verification": "fixture_verified", "error": str(exc), "workdir": str(root)}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 1
    finally:
        if temporary is not None:
            temporary.cleanup()
    report["workdir_retained"] = args.workdir is not None
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
