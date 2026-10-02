"""Deterministic deployment verification policy.

The verifier separates a deployment callback from a fresh target read.  A
commit existing in a local repository is evidence of an artifact only; it is
never sufficient for the ``verified`` status.
"""

from __future__ import annotations

from dataclasses import dataclass
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


__all__ = ["DEPLOYMENT_STATUSES", "DeploymentVerification", "verify_deployment"]
