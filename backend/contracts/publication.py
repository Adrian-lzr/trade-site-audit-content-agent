"""Stable publication, deployment, and rollback commands/results."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import CONTRACT_VERSION, ContractModel, VersionGuard


class PublicationCommand(ContractModel):
    workspace_id: int = Field(ge=1)
    change_request_id: int = Field(ge=1)
    revision_id: int = Field(ge=1)
    expected_version: int = Field(ge=1)
    target: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=255)
    request_id: str | None = Field(default=None, min_length=1, max_length=120)

    @property
    def version_guard(self) -> VersionGuard:
        return VersionGuard(expected_version=self.expected_version)


class PublicationResult(ContractModel):
    contract_version: str = Field(default=CONTRACT_VERSION, min_length=1, max_length=40)
    publication_id: int = Field(ge=1)
    change_request_id: int = Field(ge=1)
    revision_id: int = Field(ge=1)
    status: str = Field(min_length=1, max_length=40)
    target: str | None = None
    commit_sha: str | None = None
    external_id: str | None = None
    error: str | None = None
    deployment_status: str = Field(min_length=1, max_length=40)
    deployment_id: str | None = None
    deployed_commit_sha: str | None = None
    deployed_at: datetime | None = None
    verified_at: datetime | None = None


class DeploymentCommand(ContractModel):
    status: Literal["deployed", "failed"]
    commit_sha: str | None = None
    deployment_id: str | None = Field(default=None, max_length=255)


class RollbackCommand(ContractModel):
    expected_current_sha: str = Field(min_length=40, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)

