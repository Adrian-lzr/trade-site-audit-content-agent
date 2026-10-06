from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_local_html_publication_e2e_covers_apply_observe_verify_and_guarded_rollback(tmp_path: Path):
    workdir = tmp_path / "html-e2e"
    output = tmp_path / "html-e2e.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "local_html_publication_e2e.py"),
            "--workdir",
            str(workdir),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["verification"] == "fixture_verified"
    assert report["stages"]["approved_revision"]["is_synthetic"] is True
    assert report["stages"]["approved_revision"]["state"] == "approved"
    assert report["stages"]["constrained_html_apply"]["status"] == "applied"
    assert report["stages"]["constrained_html_apply"]["unrelated_content_preserved"] is True
    assert report["stages"]["fresh_target_observation"]["evidence"]["fresh_read"] is True
    assert report["stages"]["verified"] == {
        "callback_signature_verified": True,
        "commit_matches": True,
        "content_matches": True,
        "revision_matches": True,
        "status": "verified",
    }
    assert report["stages"]["guarded_rollback"] == {
        "manual_edit_conflict_rejected": True,
        "replay_rejected_as_new_write": True,
        "restored_content_hash": report["stages"]["approved_revision"]["base_content_hash"],
        "status": "rolled_back",
    }
    assert report["external_side_effects"] == {
        "cms_write": False,
        "http_requests": 0,
        "is_synthetic": True,
        "production_site_write": False,
        "remote_git_push": False,
    }
    assert (workdir / "site-repository" / "index.html").is_file()
