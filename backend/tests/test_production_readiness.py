from __future__ import annotations

import json
from pathlib import Path

from scripts.production_readiness import evaluate_evidence, load_evidence


def _provenance(ref: str) -> dict[str, object]:
    return {
        "evidence_type": "real",
        "owner": "acceptance-test",
        "source": "test fixture manifest",
        "verified_at": "2026-10-01T12:00:00+00:00",
        "evidence_ref": ref,
    }


def _complete_manifest() -> dict[str, object]:
    return {
        "evidence": {
            "provider": {**_provenance("test://provider"), "available": True},
            "authorized_site": {**_provenance("test://site-auth"), "authorized": True, "scope": "read"},
            "annotations": {
                **_provenance("test://annotations"),
                "required": 30,
                "completed": 30,
                "status": "complete",
            },
            "backup_restore": {
                **_provenance("test://backup-restore"),
                "backup": {"status": "passed", "evidence_ref": "test://backup"},
                "restore": {"status": "passed", "evidence_ref": "test://restore"},
            },
            "image_build": {
                **_provenance("test://image-build"),
                "docker_available": True,
                "built": True,
                "status": "passed",
            },
            "identity": {
                **_provenance("test://identity"),
                "mode": "required",
                "status": "verified",
            },
            "rollback": {
                **_provenance("test://rollback"),
                "executed": True,
                "verified": True,
            },
            "crm": {
                **_provenance("test://crm"),
                "available": True,
                "authorized": True,
                "status": "passed",
            },
            "remote_release_adapter": {
                **_provenance("test://remote-release"),
                "target": "authorized-ci",
                "remote": True,
                "write_scope": True,
                "status": "verified",
            },
        }
    }


def test_complete_manifest_passes_without_side_effects():
    report = evaluate_evidence(_complete_manifest())

    assert report["status"] == "passed"
    assert report["blocked"] == []
    assert {
        "required_real_provider",
        "authorized_site_evidence",
        "human_annotation_completion",
        "backup_restore_evidence",
        "image_build_evidence",
        "identity_mode",
        "rollback_evidence",
        "crm_evidence",
        "remote_release_adapter",
    } == set(report["passed"])
    assert report["read_only"] is True
    assert all(value is False for value in report["external_side_effects"].values())


def test_synthetic_provider_and_local_or_read_only_release_are_blocked():
    manifest = _complete_manifest()
    evidence = manifest["evidence"]
    assert isinstance(evidence, dict)
    evidence["provider"] = {
        **evidence["provider"],
        "evidence_type": "synthetic",
        "is_synthetic": True,
    }
    evidence["remote_release_adapter"] = {
        **evidence["remote_release_adapter"],
        "remote": False,
        "write_scope": False,
        "target": "local_git",
    }

    report = evaluate_evidence(manifest)

    assert report["status"] == "blocked"
    assert "required_real_provider" in report["blocked"]
    assert "remote_release_adapter" in report["blocked"]
    assert any("synthetic" in gap for gap in report["gaps"])
    assert any("local" in gap or "write" in gap for gap in report["gaps"])


def test_missing_docker_provider_and_crm_are_hard_blocks():
    report = evaluate_evidence({"authorized_site": {"evidence_type": "synthetic", "is_synthetic": True}})

    assert report["status"] == "blocked"
    for check_id in ("image_build_evidence", "required_real_provider", "crm_evidence"):
        assert check_id in report["blocked"]
    assert any("Docker" in gap for gap in report["gaps"])
    assert any("provider" in gap for gap in report["gaps"])
    assert any("CRM" in gap for gap in report["gaps"])


def test_manifest_can_be_loaded_from_environment_json(tmp_path: Path):
    source = _complete_manifest()
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(source), encoding="utf-8")

    loaded = load_evidence(environ={"PRODUCTION_READINESS_EVIDENCE_FILE": str(path)})

    assert loaded == source
