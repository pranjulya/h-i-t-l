"""OIDC/JWT identity validation and current RBAC evaluation.

Identity comes from validated tokens; roles and scopes come from current
database role assignments. Token claims never grant authority by themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import jwt

from hitl_ops.domain.errors import ForbiddenError


class IdentityValidationError(Exception):
    """Token validation failed; details are never propagated to clients."""


@dataclass(frozen=True, slots=True)
class AuthenticatedActor:
    actor_id: str
    tenant_id: str
    correlation_id: str
    scopes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.actor_id or not self.tenant_id:
            raise IdentityValidationError("token must carry actor and tenant")


def validate_token(
    token: str,
    *,
    shared_secret: str,
    issuer: str,
    audience: str,
    now: datetime | None = None,
) -> AuthenticatedActor:
    """Validate a token signed with the configured test issuer (HS256).

    Production identity uses OIDC discovery/JWKS via the accepted ADR; the
    validation rules (issuer, audience, signature, time claims) are identical.
    """

    try:
        claims = jwt.decode(
            token,
            shared_secret,
            algorithms=["HS256"],
            issuer=issuer,
            audience=audience,
            leeway=5,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise IdentityValidationError("token validation failed") from exc
    tenant = claims.get("tenant")
    if not isinstance(tenant, str) or not tenant:
        raise IdentityValidationError("token must carry a tenant claim")
    correlation = claims.get("correlation_id", "")
    scopes = _parse_scopes(claims.get("scope", claims.get("scopes", "")))
    return AuthenticatedActor(
        actor_id=str(claims["sub"]),
        tenant_id=tenant,
        correlation_id=str(correlation),
        scopes=scopes,
    )


def _parse_scopes(raw: object) -> frozenset[str]:
    if isinstance(raw, str):
        return frozenset(part for part in raw.split() if part)
    if isinstance(raw, (list, tuple)):
        return frozenset(str(part) for part in raw if part)
    return frozenset()


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    role: str
    valid_from: datetime
    valid_until: datetime | None
    revoked_at: datetime | None
    environments: tuple[str, ...]


def evaluate_current_roles(
    assignments: tuple[RoleAssignment, ...], at: datetime, environment: str | None = None
) -> frozenset[str]:
    """Pure evaluation of current authorization from assignments.

    An assignment counts only when it is inside its validity window, unrevoked,
    and (when scoped) covers the evaluated environment.
    """

    at = at.astimezone(UTC)
    current: set[str] = set()
    for assignment in assignments:
        if assignment.revoked_at is not None:
            continue
        if assignment.valid_from > at:
            continue
        if assignment.valid_until is not None and assignment.valid_until <= at:
            continue
        if assignment.environments and environment not in assignment.environments:
            continue
        current.add(assignment.role)
    return frozenset(current)


def require_role(roles: frozenset[str], required: str) -> None:
    if required not in roles:
        raise ForbiddenError(f"missing required role: {required}")
