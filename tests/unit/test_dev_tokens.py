"""Demo token minting tests: learning-mode only, claims complete."""

from __future__ import annotations

import pytest

from hitl_ops.config import Environment, Settings
from hitl_ops.dev_tokens import DEMO_SCOPES, DemoTokenError, mint_token
from hitl_ops.infrastructure.identity import validate_token


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "identity_shared_secret": "unit-test-secret-value-32-bytes-min",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_minted_token_validates_and_carries_scopes() -> None:
    settings = _settings()
    token = mint_token(
        actor="approver-1", tenant="tenant-1", scopes=DEMO_SCOPES, minutes=5, settings=settings
    )
    actor = validate_token(
        token,
        shared_secret="unit-test-secret-value-32-bytes-min",
        issuer=settings.identity_issuer,
        audience=settings.identity_audience,
    )
    assert actor.actor_id == "approver-1"
    assert actor.tenant_id == "tenant-1"
    assert actor.scopes == frozenset({"ops:read", "ops:write"})


def test_production_refuses_to_mint_demo_tokens() -> None:
    production = _settings(
        app_env=Environment.PRODUCTION,
        database_url="postgresql+asyncpg://svc:9f8e7d6c@db.internal:5432/hitl_ops",
    )
    with pytest.raises(DemoTokenError, match="production"):
        mint_token(actor="a", tenant="t", scopes="", minutes=1, settings=production)


def test_missing_secret_fails_closed() -> None:
    with pytest.raises(DemoTokenError, match="IDENTITY_SHARED_SECRET"):
        mint_token(
            actor="a",
            tenant="t",
            scopes="",
            minutes=1,
            settings=_settings(identity_shared_secret=None),
        )
