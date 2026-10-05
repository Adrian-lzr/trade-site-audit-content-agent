"""Deterministic deployment verification policy.

The verifier separates a deployment callback from a fresh target read.  A
commit existing in a local repository is evidence of an artifact only; it is
never sufficient for the ``verified`` status.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any


DEPLOYMENT_STATUSES = frozenset(
    {
        "prepared",
        "applied",
        "deployment_pending",
        "verified",
        "mismatch",
        "verification_unavailable",
        "rollback_pending",
        "rolled_back",
    }
)


@dataclass(frozen=True, slots=True)
class DeploymentVerification:
    status: str
    commit_matches: bool
    revision_matches: bool
    content_matches: bool
    evidence: dict[str, Any]
    error: str | None = None

    @property
    def verified(self) -> bool:
        return self.status == "verified"


@dataclass(frozen=True, slots=True)
class TargetObservation:
    """A fresh read from the configured deployment target.

    An observation is deliberately separate from a deployment callback.  A
    callback says what the deployer claims; this record is evidence fetched
    from the target after the callback was received.
    """

    target: str
    commit: str | None
    revision_hash: str | None
    content_hash: str | None
    evidence: dict[str, Any]


class TargetObservationUnavailable(RuntimeError):
    """Raised when no configured fresh target observer can be used."""


TargetObserver = Callable[..., TargetObservation | Mapping[str, Any]]
_target_observer: TargetObserver | None = None


def configure_target_observer(observer: TargetObserver | None) -> None:
    """Install a process-local observer, primarily for an authorized fixture."""
    global _target_observer
    _target_observer = observer


def _coerce_observation(raw: TargetObservation | Mapping[str, Any], target: str) -> TargetObservation:
    if isinstance(raw, TargetObservation):
        if raw.target != target:
            raise TargetObservationUnavailable("fresh observation target does not match the publication target")
        return raw
    if not isinstance(raw, Mapping):
        raise TargetObservationUnavailable("target observer returned an invalid observation")
    observed_target = str(raw.get("target") or target)
    if observed_target != target:
        raise TargetObservationUnavailable("fresh observation target does not match the publication target")
    evidence = raw.get("evidence")
    return TargetObservation(
        target=observed_target,
        commit=raw.get("commit", raw.get("observed_commit")),
        revision_hash=raw.get("revision_hash", raw.get("observed_revision_hash")),
        content_hash=raw.get("content_hash", raw.get("observed_content_hash")),
        evidence=dict(evidence) if isinstance(evidence, Mapping) else dict(raw),
    )


def _configured_observer() -> TargetObserver | None:
    if _target_observer is not None:
        return _target_observer
    spec = os.getenv("DEPLOYMENT_TARGET_OBSERVER", "").strip()
    if spec:
        module_name, separator, function_name = spec.partition(":")
        if not separator or not module_name or not function_name:
            raise TargetObservationUnavailable("DEPLOYMENT_TARGET_OBSERVER must be module:function")
        observer = getattr(importlib.import_module(module_name), function_name, None)
        if not callable(observer):
            raise TargetObservationUnavailable("configured target observer is not callable")
        return observer
    return None


def fetch_target_observation(target: str, *, attempt: Any | None = None) -> TargetObservation:
    """Fetch a fresh target observation through the configured adapter.

    ``content_hash`` is the hash of the adapter's canonical deployed
    representation (for example, the normalized publication artifact). It is
    not a claim that arbitrary HTML has been fully audited. The observer must
    include the representation and hash semantics in its evidence metadata.

    ``DEPLOYMENT_TARGET_OBSERVER=module:function`` is the preferred adapter
    hook.  For small authorized fixtures, ``DEPLOYMENT_TARGET_FETCH_URL`` may
    point at a JSON endpoint returning commit/revision_hash/content_hash.
    """
    observer = _configured_observer()
    if observer is not None:
        try:
            try:
                raw = observer(target, attempt=attempt)
            except TypeError:
                # Keep the adapter contract friendly to simple target-only
                # fixture functions while allowing production adapters to use
                # attempt metadata when they need it.
                raw = observer(target)
            return _coerce_observation(raw, target)
        except TargetObservationUnavailable:
            raise
        except Exception as exc:  # adapters must fail closed at the boundary
            raise TargetObservationUnavailable(str(exc) or "target observer failed") from exc
    fetch_url = os.getenv("DEPLOYMENT_TARGET_FETCH_URL", "").strip()
    if not fetch_url:
        raise TargetObservationUnavailable("fresh target page read is unavailable; no target observation adapter is configured")
    query = urlencode({"target": target})
    separator = "&" if "?" in fetch_url else "?"
    request = Request(f"{fetch_url}{separator}{query}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=float(os.getenv("DEPLOYMENT_TARGET_FETCH_TIMEOUT", "10"))) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise TargetObservationUnavailable(str(exc) or "target fetch failed") from exc
    return _coerce_observation(raw, target)


def callback_signature(*, secret: str, attempt_id: int, status: str, commit_sha: str | None,
                       deployment_id: str | None, nonce: str, revision_hash: str,
                       target: str) -> str:
    """Return the HMAC signature expected for a deployment callback."""
    canonical = json.dumps(
        {"attempt_id": attempt_id, "commit_sha": commit_sha, "deployment_id": deployment_id,
         "nonce": nonce, "revision_hash": revision_hash, "status": status, "target": target},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sha256=" + hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def verify_callback_signature(*, secret: str, signature: str | None, **callback: Any) -> bool:
    if not secret or not signature:
        return False
    expected = callback_signature(secret=secret, **callback)
    return hmac.compare_digest(expected, signature.strip())


def verify_deployment(
    *,
    expected_commit: str | None,
    observed_commit: str | None,
    expected_revision_hash: str,
    observed_revision_hash: str | None,
    expected_content_hash: str,
    observed_content_hash: str | None,
    fetch_error: str | None = None,
) -> DeploymentVerification:
    """Compare a fresh target observation with the approved publication."""
    if fetch_error or observed_commit is None or observed_revision_hash is None or observed_content_hash is None:
        return DeploymentVerification(
            status="verification_unavailable",
            commit_matches=bool(expected_commit and observed_commit and expected_commit == observed_commit),
            revision_matches=False,
            content_matches=False,
            evidence={
                "expected_commit": expected_commit,
                "observed_commit": observed_commit,
                "expected_revision_hash": expected_revision_hash,
                "expected_content_hash": expected_content_hash,
            },
            error=fetch_error or "fresh target observation is unavailable",
        )
    commit_matches = expected_commit is None or expected_commit == observed_commit
    revision_matches = expected_revision_hash == observed_revision_hash
    content_matches = expected_content_hash == observed_content_hash
    status = "verified" if commit_matches and revision_matches and content_matches else "mismatch"
    return DeploymentVerification(
        status=status,
        commit_matches=commit_matches,
        revision_matches=revision_matches,
        content_matches=content_matches,
        evidence={
            "expected_commit": expected_commit,
            "observed_commit": observed_commit,
            "expected_revision_hash": expected_revision_hash,
            "observed_revision_hash": observed_revision_hash,
            "expected_content_hash": expected_content_hash,
            "observed_content_hash": observed_content_hash,
        },
        error=None if status == "verified" else "deployed target does not match the approved revision",
    )


__all__ = [
    "DEPLOYMENT_STATUSES", "DeploymentVerification", "TargetObservation",
    "TargetObservationUnavailable", "callback_signature", "configure_target_observer",
    "fetch_target_observation", "verify_callback_signature", "verify_deployment",
]
