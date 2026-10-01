"""Local, explicit authorization checks for workspace-scoped API routes.

The first version intentionally does not authenticate a user.  A caller may
provide a local actor key through ``X-Local-User`` (with a few compatibility
aliases); the key is looked up in :class:`Membership` for the target
workspace.  This is useful for the local demo and for deterministic tests, but
the header is not a security credential.  A real deployment still needs an
authenticated identity provider or a trusted gateway to populate the actor
identity.

Anonymous requests remain supported while ``LOCAL_AUTH_MODE`` is ``demo``
(the default), preserving the existing no-auth local demonstration.  Set it
to ``required`` or ``strict`` to make a missing identity context an explicit
401 error on routes that opt into these checks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Mapping

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Membership, Workspace


class Role(StrEnum):
    viewer = "viewer"
    operator = "operator"
    reviewer = "reviewer"
    admin = "admin"


class Permission(StrEnum):
    """The small permission vocabulary used by the first-version API."""

    read = "read"
    operate = "operate"
    review = "review"
    admin = "admin"


@dataclass(frozen=True)
class IdentityContext:
    """The request's local actor assertion and resolved workspace role.

    ``user_id`` is ``None`` for the backwards-compatible anonymous demo path.
    When present, ``role`` is always loaded from the database; it is never
    accepted from a request header or payload.
    """

    user_id: str | None = None
    workspace_key: str | None = None
    role: Role | None = None
    source: str = "anonymous_demo"

    @property
    def authenticated(self) -> bool:
        """Whether a local actor key was supplied (not proof of identity)."""

        return self.user_id is not None


_USER_HEADERS = ("x-local-user", "x-user-id", "x-actor-id", "x-workspace-user")
_WORKSPACE_HEADERS = ("x-workspace-id", "x-workspace-key")

_ROLE_PERMISSIONS: dict[Permission, frozenset[Role]] = {
    Permission.read: frozenset(Role),
    Permission.operate: frozenset({Role.operator, Role.admin}),
    Permission.review: frozenset({Role.reviewer, Role.admin}),
    Permission.admin: frozenset({Role.admin}),
}


def _header_values(headers: Mapping[str, str], names: tuple[str, ...]) -> list[str]:
    normalized_headers = {key.casefold(): value for key, value in headers.items()}
    values: list[str] = []
    for name in names:
        value = normalized_headers.get(name)
        if value is None:
            continue
        value = value.strip()
        if value and value not in values:
            values.append(value)
    return values


def _request_headers(request: Request | Mapping[str, str]) -> Mapping[str, str]:
    if isinstance(request, Request):
        return request.headers
    return request


def identity_from_request(request: Request | Mapping[str, str]) -> IdentityContext:
    """Parse the local actor and active-workspace headers.

    Conflicting aliases are rejected so a proxy cannot accidentally produce
    two different actor identities.  The role header, if supplied, is ignored
    by design; roles come only from ``Membership``.
    """

    headers = _request_headers(request)
    user_values = _header_values(headers, _USER_HEADERS)
    workspace_values = _header_values(headers, _WORKSPACE_HEADERS)
    if len(user_values) > 1:
        raise HTTPException(400, "conflicting local identity headers")
    if len(workspace_values) > 1:
        raise HTTPException(400, "conflicting workspace identity headers")
    user_id = user_values[0] if user_values else None
    workspace_key = workspace_values[0] if workspace_values else None
    if user_id is None:
        return IdentityContext(workspace_key=workspace_key)
    if len(user_id) > 255:
        raise HTTPException(400, "local identity is too long")
    return IdentityContext(user_id=user_id, workspace_key=workspace_key, source="local_header")


def local_auth_required() -> bool:
    """Return whether checked routes require a local identity header."""

    configured = os.getenv("LOCAL_AUTH_MODE", "demo").strip().casefold()
    if os.getenv("AUTH_REQUIRED", "").strip().casefold() in {"1", "true", "yes"}:
        return True
    return configured in {"required", "strict", "enforced"}


def _workspace_key_matches(key: str, workspace: Workspace) -> bool:
    return key == str(workspace.id) or (workspace.external_id is not None and key == workspace.external_id)


def authorize_workspace(
    request: Request | Mapping[str, str],
    db: Session,
    workspace: Workspace,
    permission: Permission = Permission.read,
) -> IdentityContext:
    """Resolve and enforce a role for one workspace.

    In demo mode an omitted actor is intentionally allowed.  Once an actor
    header is present, membership and role checks are always enforced even in
    demo mode.  This makes accidental cross-workspace calls visible in local
    tests without claiming that the header itself authenticates anyone.
    """

    permission = Permission(permission)
    context = identity_from_request(request)
    if context.user_id is None:
        if local_auth_required():
            raise HTTPException(401, "identity context is required")
        return context
    if context.workspace_key is not None and not _workspace_key_matches(context.workspace_key, workspace):
        raise HTTPException(403, "workspace identity does not match the requested workspace")

    membership = db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace.id,
            Membership.user_id == context.user_id,
        )
    )
    if membership is None:
        raise HTTPException(403, "identity is not a member of this workspace")
    try:
        role = Role(membership.role)
    except ValueError as exc:  # defensive guard for legacy rows before constraints
        raise HTTPException(403, "workspace membership has an invalid role") from exc
    if role not in _ROLE_PERMISSIONS[permission]:
        raise HTTPException(403, f"role {role.value} cannot perform {permission.value}")
    return replace(context, role=role)


def effective_actor(context: IdentityContext) -> str:
    """Return the resolved actor, never a caller-supplied payload attribution."""

    if context.user_id:
        return context.user_id
    return "anonymous-demo"
