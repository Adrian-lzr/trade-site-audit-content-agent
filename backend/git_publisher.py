"""Local, reviewable Git publication adapter.

The adapter deliberately has no remote Git, CMS, or website integration.  A
caller must explicitly register a local repository target before a change can
be prepared.  This keeps the Phase 4 demo useful while making accidental
production writes impossible through this module.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping


class GitPublisherError(RuntimeError):
    """Base class for deterministic, local publication failures."""


class UnauthorizedTargetError(GitPublisherError):
    """The requested target was not explicitly registered."""


class InvalidChangePathError(GitPublisherError):
    """A file path is absolute, escapes the repository, or is not allowed."""


class IdempotencyConflictError(GitPublisherError):
    """A deterministic branch already belongs to a different change."""


class GitCommandError(GitPublisherError):
    """A local Git command failed without exposing command credentials."""


class RollbackConflictError(GitPublisherError):
    """The branch moved since the caller captured its expected commit."""


@dataclass(frozen=True)
class ChangeSet:
    """The restricted, already-approved content handed to the Git adapter."""

    changeset_id: str
    revision: int | str
    revision_hash: str
    target: str
    files: Mapping[str, str]
    title: str | None = None


@dataclass(frozen=True)
class PublicationResult:
    """The local Git identity that a caller can persist in PublicationAttempt."""

    target: str
    branch: str
    commit: str
    changeset_id: str
    revision: str
    revision_hash: str
    idempotency_key: str
    created: bool

    @property
    def branch_name(self) -> str:
        return self.branch

    @property
    def commit_sha(self) -> str:
        return self.commit


@dataclass(frozen=True)
class RollbackResult:
    """The local Git identity produced by a guarded revert operation."""

    target: str
    branch: str
    commit: str
    source_branch: str
    source_commit: str
    expected_current_sha: str
    idempotency_key: str
    created: bool

    @property
    def branch_name(self) -> str:
        return self.branch

    @property
    def commit_sha(self) -> str:
        return self.commit


Runner = Callable[..., subprocess.CompletedProcess[str]]


class GitPublisher:
    """Create one deterministic local branch and commit for a restricted change.

    ``targets`` is the explicit allowlist.  The convenience ``repo_root``
    argument registers it as target ``"demo"``.  No method invokes push,
    merge, PR, HTTP, or CMS operations.
    """

    def __init__(
        self,
        repo_root: str | os.PathLike[str] | None = None,
        *,
        targets: Mapping[str, str | os.PathLike[str]] | None = None,
        allowed_paths: Iterable[str] | None = None,
        branch_prefix: str = "trade-visibility",
        runner: Runner | None = None,
    ) -> None:
        if targets is not None and repo_root is not None:
            raise ValueError("provide repo_root or targets, not both")
        if targets is None:
            if repo_root is None:
                raise ValueError("an explicit local Git target is required")
            targets = {"demo": repo_root}
        if not targets:
            raise ValueError("at least one explicit local Git target is required")

        normalized: dict[str, Path] = {}
        for name, path in targets.items():
            if not isinstance(name, str) or not name or "/" in name or "\\" in name:
                raise ValueError("target names must be non-empty single path components")
            root = Path(path).expanduser().resolve()
            if not root.is_dir():
                raise ValueError(f"Git target does not exist as a directory: {name}")
            normalized[name] = root
        self._targets = normalized
        self._allowed_paths = frozenset(self._normalize_relative_path(p) for p in allowed_paths or ())
        self._branch_prefix = self._safe_component(branch_prefix, "branch_prefix")
        self._runner = runner or subprocess.run

    @property
    def targets(self) -> Mapping[str, Path]:
        return self._targets

    def prepare(self, change_set: ChangeSet) -> PublicationResult:
        """Prepare the reviewable local commit (alias for ``publish``).

        The name follows the narrow Publisher adapter contract; despite the
        legacy ``publish`` name, this method never pushes or merges remotely.
        """
        return self.publish(change_set)

    def publish(self, change_set: ChangeSet) -> PublicationResult:
        """Materialize ``change_set`` in its registered local repository.

        Replaying the same changeset/revision/target returns the existing
        commit.  A changed revision hash for that identity is rejected.
        """
        target = self._targets.get(change_set.target)
        if target is None:
            raise UnauthorizedTargetError(f"target is not explicitly configured: {change_set.target!r}")
        self._validate_identity(change_set)
        files = self._validate_files(change_set.files)
        self._git(target, "rev-parse", "--git-dir")

        revision = str(change_set.revision)
        key = f"{change_set.changeset_id}:{revision}:{change_set.target}"
        branch = self._branch_name(change_set.changeset_id, revision, change_set.target)
        marker = self._marker(change_set, target=change_set.target)

        existing = self._existing_branch_commit(target, branch)
        if existing:
            message = self._git(target, "show", "-s", "--format=%B", existing).stdout
            if marker not in message:
                raise IdempotencyConflictError(f"branch {branch!r} already belongs to another revision")
            return PublicationResult(change_set.target, branch, existing, change_set.changeset_id, revision, change_set.revision_hash, key, False)

        worktree = Path(tempfile.mkdtemp(prefix="trade-visibility-publish-", dir=str(target.parent)))
        try:
            self._git(target, "worktree", "add", "--detach", str(worktree), "HEAD")
            self._git(worktree, "switch", "-c", branch)
            for relative, content in files.items():
                destination = self._safe_destination(worktree, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content, encoding="utf-8", newline="")
            self._git(worktree, "add", "--", *files)
            commit_message = self._commit_message(change_set, marker)
            committed = self._git(
                worktree,
                "-c", "user.name=Trade Visibility Agent",
                "-c", "user.email=trade-visibility@example.invalid",
                "commit", "-m", commit_message,
            )
            commit = self._git(worktree, "rev-parse", "HEAD").stdout.strip()
            if not commit:
                raise GitPublisherError("Git returned an empty commit id")
            return PublicationResult(change_set.target, branch, commit, change_set.changeset_id, revision, change_set.revision_hash, key, True)
        finally:
            try:
                self._git(target, "worktree", "remove", "--force", str(worktree))
            except GitPublisherError:
                shutil.rmtree(worktree, ignore_errors=True)

    def rollback(
        self,
        *,
        target: str,
        source_branch: str,
        expected_current_sha: str,
        source_commit: str | None = None,
        rollback_id: str | None = None,
        idempotency_key: str | None = None,
        title: str | None = None,
    ) -> RollbackResult:
        """Create a reviewable revert commit for one published local branch.

        The source branch is treated as a compare-and-swap target: its head
        must still equal ``expected_current_sha`` before the temporary
        worktree is created.  This catches a manual commit (or another
        rollback) instead of silently reverting the wrong tree.  The revert
        is materialized on a deterministic rollback branch, so the caller's
        checked-out worktree and the source branch are never changed.
        """
        root = self._targets.get(target)
        if root is None:
            raise UnauthorizedTargetError(f"target is not explicitly configured: {target!r}")
        self._validate_sha(expected_current_sha, "expected_current_sha")
        source_commit = source_commit or expected_current_sha
        self._validate_sha(source_commit, "source_commit")
        if source_commit != expected_current_sha:
            raise RollbackConflictError("rollback source commit does not match expected current commit")
        self._validate_ref(source_branch)
        self._git(root, "rev-parse", "--git-dir")

        key = idempotency_key or rollback_id
        if not key:
            key = f"rollback:{source_branch}:{expected_current_sha}:{target}"
        if not isinstance(key, str) or not key or any(ord(char) < 32 for char in key):
            raise GitPublisherError("invalid rollback idempotency key")
        branch = self._rollback_branch_name(source_branch, expected_current_sha, target, key)
        marker = self._rollback_marker(
            key=key,
            source_branch=source_branch,
            source_commit=source_commit,
            expected_current_sha=expected_current_sha,
            target=target,
        )

        # Always re-check the live source ref, including idempotent retries.
        # A valid rollback branch from an earlier attempt must not hide a
        # manual commit that moved the deployment branch afterwards.
        current = self._branch_commit(root, source_branch)
        if current != expected_current_sha:
            raise RollbackConflictError(
                f"rollback refused: branch {source_branch!r} is at {current or 'missing'}, "
                f"expected {expected_current_sha}"
            )

        existing = self._existing_branch_commit(root, branch)
        if existing:
            message = self._git(root, "show", "-s", "--format=%B", existing).stdout
            parent = self._git(root, "rev-parse", "--verify", f"{existing}^{{commit}}^").stdout.strip()
            if marker not in message or parent != expected_current_sha:
                raise IdempotencyConflictError(f"branch {branch!r} already belongs to another rollback")
            return RollbackResult(
                target, branch, existing, source_branch, source_commit, expected_current_sha, key, False
            )

        worktree = Path(tempfile.mkdtemp(prefix="trade-visibility-rollback-", dir=str(root.parent)))
        try:
            # Detach from the source branch so a checked-out branch is never
            # forced or mutated while the revert is prepared.
            self._git(root, "worktree", "add", "--detach", str(worktree), expected_current_sha)
            self._git(worktree, "switch", "-c", branch)
            self._git(
                worktree,
                "-c", "user.name=Trade Visibility Agent",
                "-c", "user.email=trade-visibility@example.invalid",
                "revert", "--no-edit", expected_current_sha,
            )
            # Replace Git's generated message with a deterministic marker
            # while retaining the standard revert subject and body.
            current_message = self._git(worktree, "show", "-s", "--format=%B", "HEAD").stdout.strip()
            commit_message = self._rollback_commit_message(title, current_message, marker)
            self._git(
                worktree,
                "-c", "user.name=Trade Visibility Agent",
                "-c", "user.email=trade-visibility@example.invalid",
                "commit", "--amend", "-m", commit_message,
            )
            commit = self._git(worktree, "rev-parse", "HEAD").stdout.strip()
            if not commit:
                raise GitPublisherError("Git returned an empty rollback commit id")
            current = self._branch_commit(root, source_branch)
            if current != expected_current_sha:
                raise RollbackConflictError(
                    f"rollback refused: branch {source_branch!r} moved during rollback preparation; "
                    f"expected {expected_current_sha}"
                )
            return RollbackResult(
                target, branch, commit, source_branch, source_commit, expected_current_sha, key, True
            )
        finally:
            try:
                self._git(root, "worktree", "remove", "--force", str(worktree))
            except GitPublisherError:
                shutil.rmtree(worktree, ignore_errors=True)

    # ``revert`` is kept as a descriptive alias for callers that use the Git
    # vocabulary directly; both paths share the same guarded implementation.
    revert = rollback

    def _git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        command = ["git", *args]
        try:
            result = self._runner(
                command,
                cwd=str(cwd),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise GitCommandError(f"unable to execute Git: {exc.__class__.__name__}") from exc
        if result.returncode:
            detail = self._redact_output(result.stderr or result.stdout or "").strip()
            raise GitCommandError(f"Git {' '.join(args[:2]) or 'command'} failed ({result.returncode}): {detail[:500]}")
        return result

    @staticmethod
    def _redact_output(value: str) -> str:
        # Git diagnostics may echo a remote URL containing basic-auth data.
        return re.sub(r"(https?://)([^/@\s]+):([^/@\s]+)@", r"\1[redacted]@", value)

    @staticmethod
    def _safe_component(value: str, label: str) -> str:
        if not isinstance(value, str) or not value or value in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError(f"invalid {label}")
        return value

    @classmethod
    def _normalize_relative_path(cls, value: str) -> str:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise InvalidChangePathError("change paths must be non-empty strings")
        value = value.replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", value):
            raise InvalidChangePathError(f"change path must be repository-relative: {value!r}")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise InvalidChangePathError(f"change path escapes repository: {value!r}")
        normalized = path.as_posix()
        if normalized.startswith("../") or normalized == "..":
            raise InvalidChangePathError(f"change path escapes repository: {value!r}")
        return normalized

    def _validate_files(self, files: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(files, Mapping) or not files:
            raise InvalidChangePathError("a changeset must contain at least one file")
        normalized: dict[str, str] = {}
        for raw_path, content in files.items():
            path = self._normalize_relative_path(raw_path)
            if self._allowed_paths and path not in self._allowed_paths and not any(path.startswith(p.rstrip("/") + "/") for p in self._allowed_paths):
                raise InvalidChangePathError(f"change path is not authorized: {path!r}")
            if not isinstance(content, str):
                raise InvalidChangePathError(f"content for {path!r} must be text")
            normalized[path] = content
        return normalized

    @staticmethod
    def _safe_destination(root: Path, relative: str) -> Path:
        destination = (root / relative).resolve()
        try:
            destination.relative_to(root)
        except ValueError as exc:
            raise InvalidChangePathError(f"change path resolves outside repository: {relative!r}") from exc
        return destination

    @staticmethod
    def _validate_identity(change_set: ChangeSet) -> None:
        for label, value in (("changeset_id", change_set.changeset_id), ("target", change_set.target), ("revision_hash", change_set.revision_hash)):
            if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
                raise GitPublisherError(f"invalid {label}")
        if not str(change_set.revision) or any(ord(char) < 32 for char in str(change_set.revision)):
            raise GitPublisherError("invalid revision")

    def _branch_name(self, changeset_id: str, revision: str, target: str) -> str:
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", changeset_id).strip(".-")[:48] or "change"
        safe_revision = re.sub(r"[^A-Za-z0-9._-]+", "-", revision).strip(".-")[:24] or "revision"
        suffix = hashlib.sha256(f"{changeset_id}\0{revision}\0{target}".encode()).hexdigest()[:12]
        return f"{self._branch_prefix}/{safe_id}-{safe_revision}-{suffix}"

    @staticmethod
    def _marker(change_set: ChangeSet, *, target: str) -> str:
        return f"Trade-Visibility-Change: {change_set.changeset_id}; revision={change_set.revision}; target={target}; hash={change_set.revision_hash}"

    @staticmethod
    def _commit_message(change_set: ChangeSet, marker: str) -> str:
        title = (change_set.title or f"Apply approved change {change_set.changeset_id}").strip().replace("\n", " ")
        return f"{title}\n\n{marker}"

    def _existing_branch_commit(self, root: Path, branch: str) -> str | None:
        result = self._runner(
            ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
            cwd=str(root), text=True, encoding="utf-8", errors="replace", capture_output=True, check=False,
        )
        if result.returncode:
            return None
        return result.stdout.strip() or None

    def _branch_commit(self, root: Path, branch: str) -> str | None:
        result = self._runner(
            ["git", "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
            cwd=str(root), text=True, encoding="utf-8", errors="replace", capture_output=True, check=False,
        )
        if result.returncode:
            return None
        return result.stdout.strip() or None

    @staticmethod
    def _validate_sha(value: str, label: str) -> None:
        if not isinstance(value, str) or len(value) not in {40, 64} or not re.fullmatch(r"[0-9a-fA-F]+", value):
            raise GitPublisherError(f"invalid {label}")

    @staticmethod
    def _validate_ref(value: str) -> None:
        if (
            not isinstance(value, str)
            or not value
            or "\x00" in value
            or value.startswith("-")
            or value.endswith("/")
            or ".." in value
            or "//" in value
            or any(ord(char) < 32 or char in {" ", "~", "^", ":", "?", "*", "[", "\\"} for char in value)
        ):
            raise GitPublisherError("invalid source branch")

    def _rollback_branch_name(self, source_branch: str, expected_sha: str, target: str, key: str) -> str:
        safe_source = re.sub(r"[^A-Za-z0-9._-]+", "-", source_branch).strip(".-")[-48:] or "publication"
        suffix = hashlib.sha256(f"{key}\0{expected_sha}\0{target}".encode()).hexdigest()[:12]
        return f"{self._branch_prefix}/rollback-{safe_source}-{expected_sha[:12]}-{suffix}"

    @staticmethod
    def _rollback_marker(*, key: str, source_branch: str, source_commit: str, expected_current_sha: str, target: str) -> str:
        return (
            "Trade-Visibility-Rollback: "
            f"key={key}; source_branch={source_branch}; source_commit={source_commit}; "
            f"expected_current_sha={expected_current_sha}; target={target}"
        )

    @staticmethod
    def _rollback_commit_message(title: str | None, generated: str, marker: str) -> str:
        subject = (title or generated.splitlines()[0] or "Revert published change").strip().replace("\n", " ")
        if not subject.lower().startswith("revert"):
            subject = f"Revert published change: {subject}"
        return f"{subject}\n\n{marker}"


__all__ = [
    "ChangeSet", "GitCommandError", "GitPublisher", "GitPublisherError",
    "IdempotencyConflictError", "InvalidChangePathError", "PublicationResult",
    "RollbackConflictError", "RollbackResult", "UnauthorizedTargetError",
]
