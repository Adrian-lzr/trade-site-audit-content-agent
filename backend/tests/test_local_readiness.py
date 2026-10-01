from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_five_page_local_readiness_rehearsal(tmp_path: Path):
    workdir = tmp_path / "rehearsal"
    output = tmp_path / "readiness.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "local_readiness.py"),
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
    assert report["pages"] == 5
    assert report["published_attempts"] == 5
    assert report["deployment_callbacks"] == 5
    assert report["verified_deployments"] == 5
    assert report["idempotency"] == {
        "publication_outbox_unique": True,
        "publication_replay_rejected": True,
        "rollback_replay_same_attempt": True,
    }
    assert report["rollback"]["source_final_status"] == "rolled_back"
    assert report["rollback"]["rollback_final_status"] == "verified"
    assert report["external_side_effects"] == {
        "cms_write": False,
        "deployment_callbacks_are_local_state": True,
        "external_deployment": False,
        "remote_git_push": False,
    }
    assert (workdir / "readiness.sqlite").is_file()
    assert (workdir / "repository" / ".git").exists()


def test_five_page_readiness_cleans_temporary_sqlite_on_process_exit(tmp_path: Path):
    """The default temporary-directory path must also work on Windows."""

    output = tmp_path / "readiness-default.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "local_readiness.py"),
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
    assert report["workdir_retained"] is False


def test_postgres_restore_target_validation_is_local_and_non_destructive():
    from scripts.local_postgres_restore import validate_targets

    with pytest.raises(ValueError, match="localhost"):
        validate_targets(
            "postgresql+psycopg://user:pass@db.internal:5432/source",
            "postgresql+psycopg://user:pass@127.0.0.1:5432/source_restore",
        )
    with pytest.raises(ValueError, match="different databases"):
        validate_targets(
            "postgresql+psycopg://user:pass@127.0.0.1:5432/source",
            "postgresql+psycopg://user:pass@127.0.0.1:5432/source",
        )
    with pytest.raises(ValueError, match="_restore"):
        validate_targets(
            "postgresql+psycopg://user:pass@127.0.0.1:5432/source",
            "postgresql+psycopg://user:pass@127.0.0.1:5432/target",
        )

    from scripts.local_postgres_restore import _container_name

    assert _container_name("postgres-source-1", "source container") == "postgres-source-1"
    with pytest.raises(ValueError, match="simple Docker container name"):
        _container_name("source;rm -rf", "source container")


def test_compose_boundary_validator_requires_profile_isolation():
    from scripts.local_compose_check import validate_rendered_profiles

    def service(*, ports=None, **extra):
        value = dict(extra)
        if ports is not None:
            value["ports"] = ports
        return value

    loopback = [{"host_ip": "127.0.0.1", "published": 8000, "target": 8000}]
    default = {
        "services": {
            "postgres": service(ports=loopback),
            "api": service(ports=loopback, command=["sh", "-c", "alembic upgrade head"]),
            "worker": service(depends_on={"api": {"condition": "service_healthy"}}),
            "web": service(ports=loopback),
        }
    }
    demo = {
        "services": {
            **default["services"],
            "fixture": service(profiles=["demo"], network_mode="service:api"),
        }
    }
    report = validate_rendered_profiles(default, demo)
    assert report["fixture_profile_gated"] is True
    assert report["fixture_shared_api_namespace"] is True
    assert report["containers_started"] is False

    bad_demo = {
        "services": {
            **default["services"],
            "fixture": service(profiles=["demo"], network_mode="service:api", ports=loopback),
        }
    }
    with pytest.raises(RuntimeError, match="no independent host port"):
        validate_rendered_profiles(default, bad_demo)
