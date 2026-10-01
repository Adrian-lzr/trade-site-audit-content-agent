from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.git_publisher import (
    ChangeSet,
    GitCommandError,
    GitPublisher,
    IdempotencyConflictError,
    InvalidChangePathError,
    RollbackConflictError,
    UnauthorizedTargetError,
)


def _run(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo-site"
    repo.mkdir()
    _run(repo, "init", "--initial-branch=main")
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    _run(repo, "add", "README.md")
    _run(repo, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "initial")
    return repo


def _change(**kwargs) -> ChangeSet:
    values = {
        "changeset_id": "change-17",
        "revision": 2,
        "revision_hash": "a" * 64,
        "target": "demo",
        "files": {"content/index.html": "<h1>Updated</h1>\n"},
        "title": "Update demo page",
    }
    values.update(kwargs)
    return ChangeSet(**values)


def test_publishes_restricted_files_to_reviewable_local_branch(git_repo: Path):
    publisher = GitPublisher(git_repo, allowed_paths=["content"])

    result = publisher.publish(_change())

    assert result.created is True
    assert result.target == "demo"
    assert result.commit_sha == _run(git_repo, "rev-parse", result.branch_name)
    assert result.idempotency_key == "change-17:2:demo"
    assert _run(git_repo, "show", f"{result.commit}:content/index.html") == "<h1>Updated</h1>"
    message = _run(git_repo, "show", "-s", "--format=%B", result.commit)
    assert "revision=2" in message
    assert "hash=" + "a" * 64 in message
    assert _run(git_repo, "remote") == ""


def test_retry_returns_same_commit_without_second_commit(git_repo: Path):
    publisher = GitPublisher(git_repo)
    first = publisher.publish(_change())
    second = publisher.publish(_change())

    assert second.created is False
    assert second.commit == first.commit
    assert _run(git_repo, "rev-list", "--count", first.branch) == "2"


def test_same_identity_with_changed_hash_is_rejected(git_repo: Path):
    publisher = GitPublisher(git_repo)
    publisher.publish(_change())

    with pytest.raises(IdempotencyConflictError):
        publisher.publish(_change(revision_hash="b" * 64))


def test_unknown_target_is_rejected_before_git_command(git_repo: Path):
    publisher = GitPublisher(targets={"demo": git_repo})

    with pytest.raises(UnauthorizedTargetError):
        publisher.publish(_change(target="production"))


@pytest.mark.parametrize("path", ["../outside.txt", "/tmp/outside.txt", "content/../../outside.txt", "content\\..\\outside.txt"])
def test_path_escape_is_rejected(git_repo: Path, path: str):
    publisher = GitPublisher(git_repo)

    with pytest.raises(InvalidChangePathError):
        publisher.publish(_change(files={path: "nope"}))


def test_subprocess_errors_are_redacted(monkeypatch, git_repo: Path):
    def failing_runner(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0], returncode=128, stdout="", stderr="fatal: https://user:secret@example.test/repo.git refused",
        )

    publisher = GitPublisher(git_repo, runner=failing_runner)
    with pytest.raises(GitCommandError) as error:
        publisher.publish(_change())
    assert "secret" not in str(error.value)
    assert "[redacted]" in str(error.value)


def test_publisher_never_invokes_remote_operations(git_repo: Path):
    commands: list[list[str]] = []

    def recording_runner(args, **kwargs):
        commands.append(list(args))
        return subprocess.run(args, **kwargs)

    publisher = GitPublisher(git_repo, runner=recording_runner)
    publisher.publish(_change())
    assert all(command[1] not in {"push", "merge"} for command in commands if len(command) > 1)


def test_guarded_rollback_creates_revert_commit_and_reuses_it(git_repo: Path):
    publisher = GitPublisher(git_repo)
    published = publisher.publish(_change(files={"README.md": "updated\n"}))

    rollback = publisher.rollback(
        target="demo",
        source_branch=published.branch,
        source_commit=published.commit,
        expected_current_sha=published.commit,
        idempotency_key="rollback:17:sha:" + published.commit,
        title="Restore the previous page",
    )

    assert rollback.created is True
    assert rollback.commit_sha == _run(git_repo, "rev-parse", rollback.branch_name)
    assert _run(git_repo, "show", f"{rollback.commit}:README.md") == "demo"
    message = _run(git_repo, "show", "-s", "--format=%B", rollback.commit)
    assert "Trade-Visibility-Rollback:" in message
    replay = publisher.rollback(
        target="demo",
        source_branch=published.branch,
        source_commit=published.commit,
        expected_current_sha=published.commit,
        idempotency_key="rollback:17:sha:" + published.commit,
        title="Restore the previous page",
    )
    assert replay.created is False
    assert replay.commit == rollback.commit


def test_rollback_rejects_manual_commit_on_source_branch(git_repo: Path):
    publisher = GitPublisher(git_repo)
    published = publisher.publish(_change(files={"README.md": "updated\n"}))
    subprocess.run(["git", "switch", published.branch], cwd=git_repo, check=True, capture_output=True, text=True)
    (git_repo / "README.md").write_text("manual change\n", encoding="utf-8")
    _run(git_repo, "add", "README.md")
    _run(git_repo, "-c", "user.name=manual", "-c", "user.email=manual@example.invalid", "commit", "-m", "manual edit")

    with pytest.raises(RollbackConflictError, match="expected"):
        publisher.rollback(
            target="demo",
            source_branch=published.branch,
            source_commit=published.commit,
            expected_current_sha=published.commit,
            idempotency_key="rollback:manual",
        )


def test_rollback_rejects_manual_commit_on_deterministic_rollback_branch(git_repo: Path):
    publisher = GitPublisher(git_repo)
    published = publisher.publish(_change(files={"README.md": "updated\n"}))
    rollback = publisher.rollback(
        target="demo",
        source_branch=published.branch,
        source_commit=published.commit,
        expected_current_sha=published.commit,
        idempotency_key="rollback:manual-branch",
    )
    subprocess.run(["git", "switch", rollback.branch], cwd=git_repo, check=True, capture_output=True, text=True)
    (git_repo / "README.md").write_text("manual rollback edit\n", encoding="utf-8")
    _run(git_repo, "add", "README.md")
    _run(git_repo, "-c", "user.name=manual", "-c", "user.email=manual@example.invalid", "commit", "-m", "manual rollback edit")

    with pytest.raises(IdempotencyConflictError, match="another rollback"):
        publisher.rollback(
            target="demo",
            source_branch=published.branch,
            source_commit=published.commit,
            expected_current_sha=published.commit,
            idempotency_key="rollback:manual-branch",
        )


def test_rollback_retry_still_checks_manual_source_commit(git_repo: Path):
    publisher = GitPublisher(git_repo)
    published = publisher.publish(_change(files={"README.md": "updated\n"}))
    rollback_args = {
        "target": "demo",
        "source_branch": published.branch,
        "source_commit": published.commit,
        "expected_current_sha": published.commit,
        "idempotency_key": "rollback:retry-conflict",
    }
    created = publisher.rollback(**rollback_args)
    subprocess.run(["git", "switch", published.branch], cwd=git_repo, check=True, capture_output=True, text=True)
    (git_repo / "README.md").write_text("manual change after rollback\n", encoding="utf-8")
    _run(git_repo, "add", "README.md")
    _run(git_repo, "-c", "user.name=manual", "-c", "user.email=manual@example.invalid", "commit", "-m", "manual edit")

    with pytest.raises(RollbackConflictError, match="expected"):
        publisher.rollback(**rollback_args)
    assert _run(git_repo, "rev-parse", created.branch) == created.commit
