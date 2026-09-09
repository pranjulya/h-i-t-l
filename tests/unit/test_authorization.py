"""Token validation and current RBAC evaluation unit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from hitl_ops.domain.errors import ForbiddenError
from hitl_ops.infrastructure.identity import (
    AuthenticatedActor,
    IdentityValidationError,
    RoleAssignment,
    evaluate_current_roles,
    require_role,
    validate_token,
)

_SECRET = "unit-test-secret"
_ISSUER = "https://test-issuer.local"
_AUDIENCE = "hitl-ops"
_NOW = datetime.now(UTC).replace(microsecond=0)


def _token(**overrides: object) -> str:
    claims: dict = {
        "sub": "user-1",
        "tenant": "tenant-1",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": _NOW + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, _SECRET, algorithm="HS256")


def test_valid_token_yields_authenticated_actor() -> None:
    actor = validate_token(
        _token(), shared_secret=_SECRET, issuer=_ISSUER, audience=_AUDIENCE, now=_NOW
    )
    assert actor == AuthenticatedActor(actor_id="user-1", tenant_id="tenant-1", correlation_id="")


def test_expired_token_is_rejected() -> None:
    token = _token(exp=_NOW - timedelta(minutes=1))
    with pytest.raises(IdentityValidationError):
        validate_token(token, shared_secret=_SECRET, issuer=_ISSUER, audience=_AUDIENCE, now=_NOW)


def test_wrong_audience_is_rejected() -> None:
    with pytest.raises(IdentityValidationError):
        validate_token(
            _token(aud="other"),
            shared_secret=_SECRET,
            issuer=_ISSUER,
            audience=_AUDIENCE,
            now=_NOW,
        )


def test_wrong_issuer_is_rejected() -> None:
    with pytest.raises(IdentityValidationError):
        validate_token(
            _token(iss="https://evil.example"),
            shared_secret=_SECRET,
            issuer=_ISSUER,
            audience=_AUDIENCE,
            now=_NOW,
        )


def test_tampered_signature_is_rejected() -> None:
    token = _token()[:-3] + "abc"
    with pytest.raises(IdentityValidationError):
        validate_token(token, shared_secret=_SECRET, issuer=_ISSUER, audience=_AUDIENCE, now=_NOW)


def test_missing_subject_is_rejected() -> None:
    token = jwt.encode(
        {
            "tenant": "tenant-1",
            "iss": _ISSUER,
            "aud": _AUDIENCE,
            "exp": _NOW + timedelta(minutes=5),
        },
        _SECRET,
        algorithm="HS256",
    )
    with pytest.raises(IdentityValidationError):
        validate_token(token, shared_secret=_SECRET, issuer=_ISSUER, audience=_AUDIENCE, now=_NOW)


def test_actor_requires_tenant_and_subject() -> None:
    with pytest.raises(IdentityValidationError):
        AuthenticatedActor(actor_id="", tenant_id="tenant-1", correlation_id="")


def _assignment(**overrides: object) -> RoleAssignment:
    values: dict = {
        "role": "approver",
        "valid_from": _NOW - timedelta(days=1),
        "valid_until": None,
        "revoked_at": None,
        "environments": (),
    }
    values.update(overrides)
    return RoleAssignment(**values)  # type: ignore[arg-type]


def test_active_assignment_counts() -> None:
    roles = evaluate_current_roles((_assignment(),), _NOW)
    assert roles == frozenset({"approver"})


def test_not_yet_valid_assignment_is_excluded() -> None:
    roles = evaluate_current_roles((_assignment(valid_from=_NOW + timedelta(days=1)),), _NOW)
    assert roles == frozenset()


def test_expired_assignment_is_excluded() -> None:
    roles = evaluate_current_roles((_assignment(valid_until=_NOW - timedelta(minutes=1)),), _NOW)
    assert roles == frozenset()


def test_revoked_assignment_is_excluded() -> None:
    roles = evaluate_current_roles((_assignment(revoked_at=_NOW - timedelta(minutes=1)),), _NOW)
    assert roles == frozenset()


def test_environment_scope_is_respected() -> None:
    scoped = _assignment(environments=("staging",))
    assert "approver" in evaluate_current_roles((scoped,), _NOW, environment="staging")
    assert "approver" not in evaluate_current_roles((scoped,), _NOW, environment="production")
    unscoped = _assignment()
    assert "approver" in evaluate_current_roles((unscoped,), _NOW, environment="production")


def test_require_role_raises_forbidden() -> None:
    require_role(frozenset({"approver"}), "approver")
    with pytest.raises(ForbiddenError):
        require_role(frozenset({"auditor"}), "approver")
