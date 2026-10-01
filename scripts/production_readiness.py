"""Evaluate production acceptance evidence without performing a release.

The command is intentionally a *read-only gate*.  It consumes an evidence
manifest (JSON from ``--input`` or an environment variable), checks that the
required P0/P1 evidence is explicit and attributable, and prints a JSON
report.  It never contacts Docker, a provider, a CRM, a site, Git, or a
deployment endpoint.  A fixture, demo, or otherwise synthetic record can be
useful for local rehearsal, but it cannot satisfy a production check.

Examples::

    python scripts/production_readiness.py
    python scripts/production_readiness.py --input output/acceptance.json
    $env:PRODUCTION_READINESS_EVIDENCE_JSON = Get-Content evidence.json -Raw
    python scripts/production_readiness.py

The accepted manifest shape is documented in ``docs/production-acceptance.md``.
The evaluator is deliberately tolerant of singular/plural field names so a
report can be assembled from existing check output without copying evidence
files into this repository.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence


ROOT = Path(__file__).resolve().parents[1]

_MANIFEST_ENV_NAMES = (
    "PRODUCTION_READINESS_EVIDENCE_JSON",
    "PRODUCTION_READINESS_EVIDENCE_FILE",
    "PRODUCTION_READINESS_EVIDENCE",
    "PRODUCTION_ACCEPTANCE_EVIDENCE_JSON",
    "PRODUCTION_ACCEPTANCE_EVIDENCE_FILE",
    "PRODUCTION_ACCEPTANCE_EVIDENCE",
)

_INDIVIDUAL_ENV_NAMES = {
    "required_real_provider": (
        "PRODUCTION_REQUIRED_REAL_PROVIDER_EVIDENCE",
        "PRODUCTION_PROVIDER_EVIDENCE",
    ),
    "authorized_site_evidence": (
        "PRODUCTION_AUTHORIZED_SITE_EVIDENCE",
        "PRODUCTION_SITE_EVIDENCE",
    ),
    "human_annotation_completion": (
        "PRODUCTION_HUMAN_ANNOTATION_EVIDENCE",
        "PRODUCTION_ANNOTATION_EVIDENCE",
    ),
    "backup_restore_evidence": (
        "PRODUCTION_BACKUP_RESTORE_EVIDENCE",
        "PRODUCTION_BACKUP_EVIDENCE",
    ),
    "image_build_evidence": (
        "PRODUCTION_IMAGE_BUILD_EVIDENCE",
        "PRODUCTION_DOCKER_EVIDENCE",
    ),
    "identity_mode": ("PRODUCTION_IDENTITY_EVIDENCE",),
    "rollback_evidence": ("PRODUCTION_ROLLBACK_EVIDENCE",),
    "crm_evidence": ("PRODUCTION_CRM_EVIDENCE", "PRODUCTION_ANALYTICS_CRM_EVIDENCE"),
    "remote_release_adapter": (
        "PRODUCTION_REMOTE_RELEASE_ADAPTER_EVIDENCE",
        "PRODUCTION_DEPLOYMENT_ADAPTER_EVIDENCE",
        "PRODUCTION_CMS_CI_EVIDENCE",
    ),
    "search_console_evidence": ("PRODUCTION_SEARCH_CONSOLE_EVIDENCE",),
}

_KEY_ALIASES = {
    "required_real_provider": (
        "required_real_provider",
        "required_provider",
        "provider",
        "providers",
        "visibility_provider",
        "provider_evidence",
    ),
    "authorized_site_evidence": (
        "authorized_site_evidence",
        "authorized_site",
        "authorized_sites",
        "site_evidence",
        "site",
        "sites",
    ),
    "human_annotation_completion": (
        "human_annotation_completion",
        "human_annotations",
        "annotations",
        "annotation_evidence",
    ),
    "backup_restore_evidence": (
        "backup_restore_evidence",
        "backup_restore",
        "backup_evidence",
        "restore_evidence",
    ),
    "image_build_evidence": (
        "image_build_evidence",
        "image_build",
        "images",
        "docker",
        "docker_evidence",
    ),
    "identity_mode": ("identity_mode", "identity", "identity_evidence", "auth"),
    "rollback_evidence": ("rollback_evidence", "rollback", "rollback_rehearsal"),
    "crm_evidence": (
        "crm_evidence",
        "crm",
        "analytics_crm",
        "real_inquiries",
        "inquiry_evidence",
    ),
    "remote_release_adapter": (
        "remote_release_adapter",
        "release_adapter",
        "deployment_adapter",
        "cms_ci_integration",
        "cms_integration",
        "remote_deployment",
        "publication_evidence",
    ),
    "search_console_evidence": (
        "search_console_evidence",
        "search_console",
        "searchconsole",
    ),
}

_REQUIRED_CHECKS = (
    "required_real_provider",
    "authorized_site_evidence",
    "human_annotation_completion",
    "backup_restore_evidence",
    "image_build_evidence",
    "identity_mode",
    "rollback_evidence",
    "crm_evidence",
    "remote_release_adapter",
)

_LABELS = {
    "required_real_provider": "required real provider",
    "authorized_site_evidence": "authorized site evidence",
    "human_annotation_completion": "human annotation completion",
    "backup_restore_evidence": "backup/restore evidence",
    "image_build_evidence": "Docker image build evidence",
    "identity_mode": "production identity mode",
    "rollback_evidence": "rollback evidence",
    "crm_evidence": "real CRM/inquiry evidence",
    "remote_release_adapter": "remote CMS/CI/deployment release adapter",
    "search_console_evidence": "Search Console evidence",
}

_SUCCESS_WORDS = {
    "pass",
    "passed",
    "success",
    "succeeded",
    "complete",
    "completed",
    "verified",
    "available",
    "authorized",
    "built",
    "ready",
    "ok",
    "true",
    "yes",
}
_FAILURE_WORDS = {
    "fail",
    "failed",
    "blocked",
    "missing",
    "unavailable",
    "incomplete",
    "not_configured",
    "unknown",
    "false",
    "no",
}
_SYNTHETIC_WORDS = {"synthetic", "fixture", "demo", "mock", "test", "offline"}
_REAL_WORDS = {"real", "production", "live", "external", "authorized"}


class EvidenceInputError(ValueError):
    """Raised when an evidence manifest cannot be read as a JSON object."""


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "pass", "passed", "complete", "verified"}
    return False


def _number(record: Mapping[str, Any], names: Iterable[str]) -> int | None:
    for name in names:
        value = record.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _as_records(value: Any) -> list[Mapping[str, Any]]:
    """Return candidate records while retaining malformed values as gaps."""

    if isinstance(value, Mapping):
        # A provider/site wrapper often stores rows below ``items`` or
        # ``records``.  Keep the wrapper too, because its evidence_ref and
        # is_synthetic flags may apply to every child row.
        for key in ("items", "records", "entries", "values"):
            children = value.get(key)
            if isinstance(children, list):
                rows = [row for row in children if isinstance(row, Mapping)]
                if rows:
                    return rows
        return [value]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, Mapping)]
    return []


def _lookup(source: Mapping[str, Any], check_id: str) -> tuple[Any, str | None]:
    aliases = _KEY_ALIASES[check_id]
    for key in aliases:
        if key in source:
            return source[key], key
    return None, None


def _evidence_kind(record: Mapping[str, Any], parent: Mapping[str, Any] | None = None) -> str:
    """Return ``real``, ``synthetic`` or ``unknown`` from explicit metadata."""

    values: list[Any] = []
    for candidate in (record, parent or {}):
        # ``evidence_type`` is the canonical manifest field.  Legacy aliases
        # remain readable, but a production record must still provide the
        # canonical field (enforced by ``_metadata_gaps`` below).
        if "evidence_type" in candidate:
            values.insert(0, candidate["evidence_type"])
        for key in ("is_synthetic", "synthetic"):
            if key in candidate:
                values.append(candidate[key])
        for key in ("evidence_kind", "kind", "source_kind", "data_kind", "provenance"):
            if key in candidate:
                values.append(candidate[key])
        for key in ("mode", "environment"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip().lower() in _SYNTHETIC_WORDS | _REAL_WORDS:
                values.append(value)
        if _truthy(candidate.get("human_verified")) or _truthy(candidate.get("manually_verified")):
            values.append("real")

    saw_synthetic = False
    saw_real = False
    for value in values:
        if isinstance(value, bool):
            saw_synthetic = saw_synthetic or value
            saw_real = saw_real or not value
            continue
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in _SYNTHETIC_WORDS or normalized in {"true", "1"}:
                saw_synthetic = True
            if normalized in _REAL_WORDS or normalized in {"false", "0"}:
                saw_real = True
    if saw_synthetic:
        # Contradictory metadata is conservatively treated as synthetic; it
        # cannot accidentally satisfy a real-production gate.
        return "synthetic"
    if saw_real:
        return "real"
    return "unknown"


def _references(record: Mapping[str, Any], parent: Mapping[str, Any] | None = None) -> list[str]:
    """Extract references without treating a reference as proof by itself."""

    refs: list[str] = []
    for candidate in (record, parent or {}):
        for key in (
            "evidence_ref",
            "evidence_uri",
            "artifact_uri",
            "source_locator",
            "ref",
            "path",
            "uri",
            "url",
            "report",
            "evidence",
        ):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                refs.append(value.strip())
            elif isinstance(value, Mapping):
                nested = _references(value)
                refs.extend(nested)
            elif isinstance(value, list):
                refs.extend(str(item).strip() for item in value if isinstance(item, str) and item.strip())
    return list(dict.fromkeys(refs))


def _metadata_gaps(record: Mapping[str, Any], parent: Mapping[str, Any] | None = None) -> list[str]:
    """Require provenance metadata before accepting any real evidence.

    ``evidence_ref`` identifies the artifact; ``owner``, ``source`` and
    ``verified_at`` identify who supplied it, where it came from, and when it
    was checked.  Missing provenance is a block, never an inferred pass.
    """

    merged = _merge_parent(record, parent)
    gaps: list[str] = []
    evidence_type = merged.get("evidence_type")
    if not isinstance(evidence_type, str) or evidence_type.strip().lower() != "real":
        gaps.append("evidence_type must be 'real'")
    for key in ("owner", "source", "verified_at"):
        value = merged.get(key)
        if isinstance(value, Mapping):
            present = bool(value)
        else:
            present = _text(value)
        if not present:
            gaps.append(f"missing provenance field {key}")
    verified_at = merged.get("verified_at")
    if _text(verified_at):
        try:
            datetime.fromisoformat(str(verified_at).strip().replace("Z", "+00:00"))
        except ValueError:
            gaps.append("verified_at must be an ISO-8601 timestamp")
    return gaps


def _write_scope(record: Mapping[str, Any]) -> bool | None:
    """Interpret an explicit publication/write scope without guessing."""

    for key in ("write_scope", "can_write", "publish_scope", "authorization_scope", "scope"):
        if key not in record:
            continue
        value = record[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, (list, tuple, set)):
            tokens = {str(item).strip().lower() for item in value}
            if tokens & {"write", "publish", "deploy", "admin", "cms_write"}:
                return True
            if tokens:
                return False
        if isinstance(value, str):
            tokens = {token.strip().lower() for token in value.replace(",", " ").split() if token.strip()}
            if tokens & {"write", "publish", "deploy", "admin", "cms_write", "read_write"}:
                return True
            if tokens & {"read", "read_only", "readonly", "view"}:
                return False
    return None


def _status(record: Mapping[str, Any], success_keys: Sequence[str]) -> bool | None:
    """Read an explicit result; ``None`` means no result was supplied."""

    for key in ("status", "result", "outcome", "state"):
        value = record.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in _SUCCESS_WORDS:
                return True
            if normalized in _FAILURE_WORDS:
                return False
    for key in success_keys:
        if key in record:
            return _truthy(record[key])
    return None


def _merge_parent(record: Mapping[str, Any], parent: Mapping[str, Any] | None) -> dict[str, Any]:
    if not parent:
        return dict(record)
    merged = dict(parent)
    merged.update(record)
    return merged


def _check_result(
    check_id: str,
    *,
    passed: bool,
    reason: str,
    evidence_kind: str = "unknown",
    evidence_refs: Iterable[str] = (),
    priority: str = "P0",
) -> dict[str, Any]:
    refs = list(dict.fromkeys(ref for ref in evidence_refs if _text(ref)))
    return {
        "id": check_id,
        "label": _LABELS.get(check_id, check_id),
        "priority": priority,
        "status": "passed" if passed else "blocked",
        "evidence_kind": evidence_kind,
        "evidence_refs": refs,
        "reason": reason,
    }


def _real_record_check(
    check_id: str,
    value: Any,
    *,
    success_keys: Sequence[str],
    priority: str,
    parent: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    records = _as_records(value)
    if not records:
        return _check_result(
            check_id,
            passed=False,
            reason=f"missing {_LABELS[check_id]} (a JSON evidence record is required)",
            priority=priority,
        )

    failures: list[str] = []
    first_kind = "unknown"
    refs: list[str] = []
    for record in records:
        merged = _merge_parent(record, parent)
        kind = _evidence_kind(record, parent)
        first_kind = kind if first_kind == "unknown" else first_kind
        current_refs = _references(record, parent)
        refs.extend(current_refs)
        if kind == "synthetic":
            failures.append("synthetic evidence cannot satisfy a production gate")
            continue
        if kind != "real":
            failures.append("evidence_kind/is_synthetic must explicitly identify real evidence")
            continue
        metadata_gaps = _metadata_gaps(record, parent)
        if metadata_gaps:
            failures.extend(metadata_gaps)
            continue
        if not current_refs:
            failures.append("real evidence is missing an evidence_ref/ref/path/URL")
            continue
        state = _status(merged, success_keys)
        if state is not True:
            failures.append("evidence is not explicitly marked passed/complete/verified")
            continue
        return _check_result(
            check_id,
            passed=True,
            reason="explicit real evidence is complete and referenced",
            evidence_kind="real",
            evidence_refs=current_refs,
            priority=priority,
        )

    reason = f"{_LABELS[check_id]} is blocked: " + "; ".join(dict.fromkeys(failures))
    return _check_result(
        check_id,
        passed=False,
        reason=reason,
        evidence_kind=first_kind,
        evidence_refs=refs,
        priority=priority,
    )


def _provider_check(value: Any) -> dict[str, Any]:
    records = _as_records(value)
    if not records:
        return _check_result(
            "required_real_provider",
            passed=False,
            reason="missing required real provider evidence; Docker/fixture or an unavailable provider is not sufficient",
            priority="P1",
        )
    failures: list[str] = []
    refs: list[str] = []
    for record in records:
        kind = _evidence_kind(record)
        current_refs = _references(record)
        refs.extend(current_refs)
        if kind == "synthetic":
            failures.append("provider evidence is synthetic/fixture")
            continue
        if kind != "real":
            failures.append("provider evidence must explicitly set evidence_kind=real or is_synthetic=false")
            continue
        metadata_gaps = _metadata_gaps(record)
        if metadata_gaps:
            failures.extend(metadata_gaps)
            continue
        if not current_refs:
            failures.append("real provider evidence has no evidence_ref/ref/URL")
            continue
        available = _status(record, ("available", "reachable", "authorized", "healthy"))
        if available is not True:
            failures.append("real provider is missing an explicit available/passed result")
            continue
        return _check_result(
            "required_real_provider",
            passed=True,
            reason="an explicitly real, referenced, available provider is present",
            evidence_kind="real",
            evidence_refs=current_refs,
            priority="P1",
        )
    return _check_result(
        "required_real_provider",
        passed=False,
        reason="required real provider is blocked: " + "; ".join(dict.fromkeys(failures)),
        evidence_kind="synthetic" if any(_evidence_kind(row) == "synthetic" for row in records) else "unknown",
        evidence_refs=refs,
        priority="P1",
    )


def _site_check(value: Any) -> dict[str, Any]:
    records = _as_records(value)
    if not records:
        return _check_result(
            "authorized_site_evidence",
            passed=False,
            reason="missing authorized site evidence; a site URL alone is not authorization",
            priority="P0",
        )
    failures: list[str] = []
    refs: list[str] = []
    for record in records:
        kind = _evidence_kind(record)
        current_refs = _references(record)
        refs.extend(current_refs)
        if kind == "synthetic":
            failures.append("site evidence is synthetic/fixture")
            continue
        if kind != "real":
            failures.append("site evidence must explicitly identify real evidence")
            continue
        metadata_gaps = _metadata_gaps(record)
        if metadata_gaps:
            failures.extend(metadata_gaps)
            continue
        if not current_refs:
            failures.append("authorized site has no evidence_ref/ref/URL")
            continue
        authorized = _status(record, ("authorized", "authorization_confirmed", "verified"))
        if authorized is not True:
            failures.append("site authorization is not explicitly confirmed")
            continue
        return _check_result(
            "authorized_site_evidence",
            passed=True,
            reason="an explicitly authorized real site is referenced",
            evidence_kind="real",
            evidence_refs=current_refs,
            priority="P0",
        )
    return _check_result(
        "authorized_site_evidence",
        passed=False,
        reason="authorized site evidence is blocked: " + "; ".join(dict.fromkeys(failures)),
        evidence_kind="synthetic" if any(_evidence_kind(row) == "synthetic" for row in records) else "unknown",
        evidence_refs=refs,
        priority="P0",
    )


def _annotation_check(value: Any) -> dict[str, Any]:
    records = _as_records(value)
    if not records:
        return _check_result(
            "human_annotation_completion",
            passed=False,
            reason="missing human annotation completion evidence",
            priority="P0",
        )
    failures: list[str] = []
    refs: list[str] = []
    for record in records:
        kind = _evidence_kind(record)
        current_refs = _references(record)
        refs.extend(current_refs)
        if kind == "synthetic":
            failures.append("annotation evidence is synthetic/template-only")
            continue
        if kind != "real":
            failures.append("annotation evidence must explicitly identify real human review")
            continue
        metadata_gaps = _metadata_gaps(record)
        if metadata_gaps:
            failures.extend(metadata_gaps)
            continue
        required = _number(record, ("required", "required_cases", "target", "minimum_cases"))
        completed = _number(record, ("completed", "complete", "complete_cases", "completed_cases", "case_count"))
        state = _status(record, ("complete", "completed", "human_verified"))
        if required is None or required <= 0:
            failures.append("annotation evidence must state a positive required case count")
            continue
        if completed is None or completed < required:
            failures.append(f"human annotations are incomplete ({completed or 0}/{required})")
            continue
        if state is not True:
            failures.append("annotation evidence is not explicitly marked complete")
            continue
        if not current_refs:
            failures.append("complete human annotations have no evidence_ref/ref/path")
            continue
        return _check_result(
            "human_annotation_completion",
            passed=True,
            reason=f"human annotation completion is {completed}/{required}",
            evidence_kind="real",
            evidence_refs=current_refs,
            priority="P0",
        )
    return _check_result(
        "human_annotation_completion",
        passed=False,
        reason="human annotation completion is blocked: " + "; ".join(dict.fromkeys(failures)),
        evidence_kind="synthetic" if any(_evidence_kind(row) == "synthetic" for row in records) else "unknown",
        evidence_refs=refs,
        priority="P0",
    )


def _backup_restore_check(value: Any) -> dict[str, Any]:
    records = _as_records(value)
    if not records:
        return _check_result(
            "backup_restore_evidence",
            passed=False,
            reason="missing backup/restore evidence; no database operation is performed by this gate",
            priority="P0",
        )
    failures: list[str] = []
    refs: list[str] = []
    for parent in records:
        kind = _evidence_kind(parent)
        parent_refs = _references(parent)
        refs.extend(parent_refs)
        if kind == "synthetic":
            failures.append("backup/restore evidence is synthetic/local rehearsal")
            continue
        if kind != "real":
            failures.append("backup/restore evidence must explicitly identify real execution")
            continue
        metadata_gaps = _metadata_gaps(parent)
        if metadata_gaps:
            failures.extend(metadata_gaps)
            continue
        backup = parent.get("backup")
        restore = parent.get("restore")
        # A flat report may use backup_status/restore_status instead of child
        # objects.  Convert those fields into tiny records for the same gate.
        if not isinstance(backup, Mapping):
            backup = {"status": parent.get("backup_status"), "evidence_ref": parent.get("backup_ref")}
        if not isinstance(restore, Mapping):
            restore = {"status": parent.get("restore_status"), "evidence_ref": parent.get("restore_ref")}
        child_refs = _references(backup, parent) + _references(restore, parent)
        refs.extend(child_refs)
        backup_state = _status(backup, ("success", "verified", "restored", "backup_created"))
        restore_state = _status(restore, ("success", "verified", "restored", "restore_verified"))
        if backup_state is not True:
            failures.append("backup step is not explicitly passed")
        if restore_state is not True:
            failures.append("restore step is not explicitly passed")
        if backup_state is True and restore_state is True and child_refs:
            return _check_result(
                "backup_restore_evidence",
                passed=True,
                reason="real backup and restore steps are both passed and referenced",
                evidence_kind="real",
                evidence_refs=child_refs,
                priority="P0",
            )
        if backup_state is True and restore_state is True:
            failures.append("backup/restore steps have no evidence references")
    return _check_result(
        "backup_restore_evidence",
        passed=False,
        reason="backup/restore evidence is blocked: " + "; ".join(dict.fromkeys(failures)),
        evidence_kind="synthetic" if any(_evidence_kind(row) == "synthetic" for row in records) else "unknown",
        evidence_refs=refs,
        priority="P0",
    )


def _image_check(value: Any) -> dict[str, Any]:
    records = _as_records(value)
    if not records:
        return _check_result(
            "image_build_evidence",
            passed=False,
            reason="missing Docker image build evidence; static Dockerfile checks do not prove an image was built",
            priority="P0",
        )
    failures: list[str] = []
    refs: list[str] = []
    for record in records:
        kind = _evidence_kind(record)
        current_refs = _references(record)
        refs.extend(current_refs)
        if kind == "synthetic":
            failures.append("image build evidence is synthetic/static configuration only")
            continue
        if kind != "real":
            failures.append("image build evidence must explicitly identify real Docker execution")
            continue
        metadata_gaps = _metadata_gaps(record)
        if metadata_gaps:
            failures.extend(metadata_gaps)
            continue
        docker = _status(record, ("docker_available", "docker", "daemon_available"))
        built = _status(record, ("built", "image_built", "build_succeeded", "success"))
        state = _status(record, ("status", "verified"))
        if docker is not True:
            failures.append("Docker daemon availability is not explicitly confirmed")
        if built is not True or (state is False):
            failures.append("image build is not explicitly passed")
        if docker is True and built is True and state is not False and current_refs:
            return _check_result(
                "image_build_evidence",
                passed=True,
                reason="real Docker availability and image build success are referenced",
                evidence_kind="real",
                evidence_refs=current_refs,
                priority="P0",
            )
        if docker is True and built is True:
            failures.append("image build evidence has no evidence_ref/ref/path")
    return _check_result(
        "image_build_evidence",
        passed=False,
        reason="Docker image build evidence is blocked: " + "; ".join(dict.fromkeys(failures)),
        evidence_kind="synthetic" if any(_evidence_kind(row) == "synthetic" for row in records) else "unknown",
        evidence_refs=refs,
        priority="P0",
    )


def _identity_check(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        record: Mapping[str, Any] = {"mode": value}
    elif isinstance(value, Mapping):
        record = value
    else:
        record = {}
    mode = str(record.get("mode", "")).strip().lower()
    refs = _references(record)
    kind = _evidence_kind(record)
    if not mode:
        return _check_result(
            "identity_mode",
            passed=False,
            reason="missing identity mode; production must use required/strict identity",
            evidence_kind=kind,
            evidence_refs=refs,
            priority="P0",
        )
    if mode not in {"required", "strict", "production", "trusted"}:
        return _check_result(
            "identity_mode",
            passed=False,
            reason=f"identity mode {mode!r} is not a production-required mode",
            evidence_kind=kind,
            evidence_refs=refs,
            priority="P0",
        )
    if kind != "real":
        return _check_result(
            "identity_mode",
            passed=False,
            reason="identity mode is production-required but its evidence is not explicitly real",
            evidence_kind=kind,
            evidence_refs=refs,
            priority="P0",
        )
    metadata_gaps = _metadata_gaps(record)
    if metadata_gaps:
        return _check_result(
            "identity_mode",
            passed=False,
            reason="identity evidence is blocked: " + "; ".join(metadata_gaps),
            evidence_kind=kind,
            evidence_refs=refs,
            priority="P0",
        )
    state = _status(record, ("configured", "enabled", "verified", "status"))
    if state is not True:
        return _check_result(
            "identity_mode",
            passed=False,
            reason="required identity mode is not explicitly marked configured/verified",
            evidence_kind=kind,
            evidence_refs=refs,
            priority="P0",
        )
    if not refs:
        return _check_result(
            "identity_mode",
            passed=False,
            reason="required identity mode has no evidence_ref/ref/path",
            evidence_kind=kind,
            evidence_refs=refs,
            priority="P0",
        )
    return _check_result(
        "identity_mode",
        passed=True,
        reason=f"identity mode {mode!r} is explicitly required and referenced",
        evidence_kind="real",
        evidence_refs=refs,
        priority="P0",
    )


def _rollback_check(value: Any) -> dict[str, Any]:
    records = _as_records(value)
    if not records:
        return _check_result(
            "rollback_evidence",
            passed=False,
            reason="missing rollback evidence; a local proposal without a verified rehearsal is not enough",
            priority="P0",
        )
    failures: list[str] = []
    refs: list[str] = []
    for record in records:
        kind = _evidence_kind(record)
        current_refs = _references(record)
        refs.extend(current_refs)
        if kind == "synthetic":
            failures.append("rollback evidence is synthetic/local rehearsal")
            continue
        if kind != "real":
            failures.append("rollback evidence must explicitly identify real execution")
            continue
        metadata_gaps = _metadata_gaps(record)
        if metadata_gaps:
            failures.extend(metadata_gaps)
            continue
        executed = _status(record, ("executed", "rollback_executed", "rehearsed"))
        verified = _status(record, ("verified", "rollback_verified", "success"))
        if executed is not True:
            failures.append("rollback was not explicitly executed/rehearsed")
        if verified is not True:
            failures.append("rollback result is not explicitly verified")
        if executed is True and verified is True and current_refs:
            return _check_result(
                "rollback_evidence",
                passed=True,
                reason="real rollback execution and verification are referenced",
                evidence_kind="real",
                evidence_refs=current_refs,
                priority="P0",
            )
        if executed is True and verified is True:
            failures.append("rollback evidence has no evidence_ref/ref/path")
    return _check_result(
        "rollback_evidence",
        passed=False,
        reason="rollback evidence is blocked: " + "; ".join(dict.fromkeys(failures)),
        evidence_kind="synthetic" if any(_evidence_kind(row) == "synthetic" for row in records) else "unknown",
        evidence_refs=refs,
        priority="P0",
    )


def _crm_check(value: Any) -> dict[str, Any]:
    records = _as_records(value)
    if not records:
        return _check_result(
            "crm_evidence",
            passed=False,
            reason="missing CRM/analytics evidence; production inquiry claims require an authorized real CRM",
            priority="P1",
        )
    failures: list[str] = []
    refs: list[str] = []
    for record in records:
        kind = _evidence_kind(record)
        current_refs = _references(record)
        refs.extend(current_refs)
        if kind == "synthetic":
            failures.append("CRM evidence is synthetic/demo data")
            continue
        if kind != "real":
            failures.append("CRM evidence must explicitly identify real data")
            continue
        metadata_gaps = _metadata_gaps(record)
        if metadata_gaps:
            failures.extend(metadata_gaps)
            continue
        available = _status(record, ("available", "connected", "authorized", "verified"))
        authorized = _truthy(record.get("authorized")) or _truthy(record.get("authorization_confirmed"))
        state = _status(record, ("status", "complete", "verified"))
        if available is not True:
            failures.append("CRM availability/connection is not explicitly passed")
        if not authorized:
            failures.append("CRM authorization is not explicitly confirmed")
        if state is False:
            failures.append("CRM evidence status is not passed")
        if available is True and authorized and state is not False and current_refs:
            return _check_result(
                "crm_evidence",
                passed=True,
                reason="authorized real CRM/inquiry evidence is referenced",
                evidence_kind="real",
                evidence_refs=current_refs,
                priority="P1",
            )
        if available is True and authorized and state is not False:
            failures.append("CRM evidence has no evidence_ref/ref/path")
    return _check_result(
        "crm_evidence",
        passed=False,
        reason="CRM evidence is blocked: " + "; ".join(dict.fromkeys(failures)),
        evidence_kind="synthetic" if any(_evidence_kind(row) == "synthetic" for row in records) else "unknown",
        evidence_refs=refs,
        priority="P1",
    )


def _remote_release_check(value: Any) -> dict[str, Any]:
    """Require a real remote CMS/CI/deployment write integration.

    The repository's local Git adapter and deployment callbacks are useful
    rehearsal evidence, but they are deliberately rejected here.  A
    production release needs an externally reachable adapter with explicit
    write scope and a referenced verification record.
    """

    records = _as_records(value)
    if not records:
        return _check_result(
            "remote_release_adapter",
            passed=False,
            reason="missing remote CMS/CI/deployment adapter evidence; local Git or not_configured cannot release production",
            priority="P0",
        )
    failures: list[str] = []
    refs: list[str] = []
    for record in records:
        kind = _evidence_kind(record)
        current_refs = _references(record)
        refs.extend(current_refs)
        if kind == "synthetic":
            failures.append("release adapter evidence is synthetic/local")
            continue
        if kind != "real":
            failures.append("release adapter evidence must explicitly identify real execution")
            continue
        metadata_gaps = _metadata_gaps(record)
        if metadata_gaps:
            failures.extend(metadata_gaps)
            continue
        target = str(record.get("target", record.get("adapter", record.get("system", "")))).strip().lower()
        if target in {"", "local", "local_git", "git_local", "demo", "fixture", "not_configured"}:
            failures.append("adapter target is local/not_configured; a remote CMS/CI/deployment target is required")
            continue
        remote = _truthy(record.get("remote")) or _truthy(record.get("remote_adapter"))
        if not remote:
            failures.append("remote adapter must explicitly set remote=true")
            continue
        write_scope = _write_scope(record)
        if write_scope is not True:
            failures.append("adapter does not have explicit write/publish scope; read-only site authorization is insufficient")
            continue
        state = _status(record, ("available", "configured", "connected", "authorized", "verified", "integration_tested", "success"))
        if state is not True:
            failures.append("remote release adapter is not explicitly configured/verified")
            continue
        if not current_refs:
            failures.append("remote release adapter evidence has no evidence_ref/ref/path")
            continue
        return _check_result(
            "remote_release_adapter",
            passed=True,
            reason="real remote release adapter has explicit write scope and verification evidence",
            evidence_kind="real",
            evidence_refs=current_refs,
            priority="P0",
        )
    return _check_result(
        "remote_release_adapter",
        passed=False,
        reason="remote release adapter is blocked: " + "; ".join(dict.fromkeys(failures)),
        evidence_kind="synthetic" if any(_evidence_kind(row) == "synthetic" for row in records) else "unknown",
        evidence_refs=refs,
        priority="P0",
    )


def _optional_check(check_id: str, value: Any) -> dict[str, Any]:
    """Check optional P1 evidence only when the manifest supplies it.

    Search Console is conditional in the plan: it requires a separately
    authorized site.  Omitting it therefore yields ``not_evaluated`` instead
    of pretending that a missing integration passed.  Supplying it opts into
    the same strict real-evidence rules as the required checks.
    """

    if value is None:
        return {
            "id": check_id,
            "label": _LABELS[check_id],
            "priority": "P1",
            "status": "not_evaluated",
            "evidence_kind": "unknown",
            "evidence_refs": [],
            "reason": "optional evidence was not supplied",
        }
    return _real_record_check(
        check_id,
        value,
        success_keys=("available", "authorized", "verified", "complete", "success"),
        priority="P1",
    )


def _source_manifest(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = manifest.get("evidence")
    if isinstance(nested, Mapping):
        # Include top-level aliases too, while allowing the explicit evidence
        # object to take precedence.
        merged: dict[str, Any] = {key: value for key, value in manifest.items() if key != "evidence"}
        merged.update(nested)
        return merged
    return manifest


def evaluate_evidence(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a loaded evidence manifest and return a JSON-compatible report."""

    if not isinstance(manifest, Mapping):
        raise EvidenceInputError("evidence manifest root must be a JSON object")
    source = _source_manifest(manifest)
    checks: list[dict[str, Any]] = []

    values: dict[str, Any] = {}
    for check_id in _REQUIRED_CHECKS + ("search_console_evidence",):
        value, _ = _lookup(source, check_id)
        values[check_id] = value

    checks.append(_provider_check(values["required_real_provider"]))
    checks.append(_site_check(values["authorized_site_evidence"]))
    checks.append(_annotation_check(values["human_annotation_completion"]))
    checks.append(_backup_restore_check(values["backup_restore_evidence"]))
    checks.append(_image_check(values["image_build_evidence"]))
    checks.append(_identity_check(values["identity_mode"]))
    checks.append(_rollback_check(values["rollback_evidence"]))
    checks.append(_crm_check(values["crm_evidence"]))
    checks.append(_remote_release_check(values["remote_release_adapter"]))
    if values["search_console_evidence"] is not None:
        checks.append(_optional_check("search_console_evidence", values["search_console_evidence"]))

    passed = [check["id"] for check in checks if check["status"] == "passed"]
    blocked = [check["id"] for check in checks if check["status"] == "blocked"]
    not_evaluated = [check["id"] for check in checks if check["status"] == "not_evaluated"]
    gaps = [
        f"{check['id']}: {check['reason']}"
        for check in checks
        if check["status"] == "blocked"
    ]
    synthetic = [
        check["id"]
        for check in checks
        if check.get("evidence_kind") == "synthetic"
    ]
    real = [
        check["id"]
        for check in checks
        if check.get("evidence_kind") == "real" and check["status"] == "passed"
    ]
    status = "passed" if not blocked else "blocked"
    return {
        "status": status,
        "passed": passed,
        "blocked": blocked,
        "gaps": gaps,
        "checks": checks,
        "not_evaluated": not_evaluated,
        "synthetic_evidence": synthetic,
        "real_evidence": real,
        "read_only": True,
        "external_side_effects": {
            "provider_calls": False,
            "site_requests": False,
            "crm_requests": False,
            "docker_commands": False,
            "release_or_publish": False,
        },
        "manifest_mode": str(source.get("mode", "production")),
    }


def _decode_value(raw: str, *, label: str) -> Any:
    value = raw.strip()
    if not value:
        raise EvidenceInputError(f"{label} is empty")
    path = Path(value).expanduser()
    if path.is_file():
        try:
            value = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise EvidenceInputError(f"unable to read {label} file {path}: {exc}") from exc
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise EvidenceInputError(f"{label} must contain a JSON object or point to a JSON file: {exc}") from exc


def load_evidence(*, input_path: str | Path | None = None, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Load JSON evidence from a file, manifest env var, and optional env overlays."""

    env = os.environ if environ is None else environ
    manifest: Any
    if input_path is not None:
        path = Path(input_path).expanduser()
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise EvidenceInputError(f"unable to read evidence file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise EvidenceInputError(f"invalid JSON evidence file {path}: {exc}") from exc
    else:
        manifest = None
        for name in _MANIFEST_ENV_NAMES:
            raw = env.get(name)
            if raw and raw.strip():
                manifest = _decode_value(raw, label=name)
                break
        if manifest is None:
            manifest = {}

    if not isinstance(manifest, Mapping):
        raise EvidenceInputError("evidence manifest root must be a JSON object")
    merged: dict[str, Any] = dict(manifest)
    overlay = dict(merged.get("evidence")) if isinstance(merged.get("evidence"), Mapping) else {}
    for check_id, names in _INDIVIDUAL_ENV_NAMES.items():
        for name in names:
            raw = env.get(name)
            if not raw or not raw.strip():
                continue
            if check_id == "identity_mode" and name == "PRODUCTION_IDENTITY_MODE":
                overlay[check_id] = {"mode": raw.strip()}
                break
            try:
                overlay[check_id] = _decode_value(raw, label=name)
            except EvidenceInputError:
                # A bare artifact path/URL is useful context, but it has no
                # result or provenance and therefore remains blocked by the
                # evaluator.  Keeping it as a reference makes the gap
                # actionable without turning the reference into proof.
                overlay[check_id] = {"evidence_ref": raw.strip()}
            break
    # A short identity mode is useful in CI, but still requires a separate
    # JSON evidence record before the identity gate can pass.
    identity_mode = env.get("PRODUCTION_IDENTITY_MODE")
    if identity_mode and identity_mode.strip():
        current = overlay.get("identity_mode")
        if isinstance(current, Mapping):
            current = dict(current)
            current["mode"] = identity_mode.strip()
            overlay["identity_mode"] = current
        elif current is None:
            overlay["identity_mode"] = {"mode": identity_mode.strip()}
    if overlay:
        merged["evidence"] = overlay
    return merged


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "--evidence", dest="input_path", type=Path, help="JSON evidence manifest path")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        manifest = load_evidence(input_path=args.input_path)
        report = evaluate_evidence(manifest)
    except EvidenceInputError as exc:
        report = {
            "status": "blocked",
            "passed": [],
            "blocked": ["evidence_input"],
            "gaps": [f"evidence_input: {exc}"],
            "checks": [],
            "not_evaluated": [],
            "synthetic_evidence": [],
            "real_evidence": [],
            "read_only": True,
            "external_side_effects": {
                "provider_calls": False,
                "site_requests": False,
                "crm_requests": False,
                "docker_commands": False,
                "release_or_publish": False,
            },
        }
    encoded = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.expanduser().parent.mkdir(parents=True, exist_ok=True)
        args.output.expanduser().write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["status"] == "passed" else 1


# Short aliases make the module convenient to call from a test or another
# read-only CI step without coupling callers to the CLI name.
check = evaluate_evidence
check_gate = evaluate_evidence


if __name__ == "__main__":
    raise SystemExit(main())
