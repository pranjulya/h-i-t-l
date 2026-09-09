"""API test fixtures: migrated database and bearer-token helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from hitl_ops.config import Settings
from tests.conftest import make_settings as base_make_settings
from tests.integration.conftest import reset_schema, run_alembic_upgrade

API_SECRET = "api-test-secret"


def api_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"identity_shared_secret": API_SECRET}
    values.update(overrides)
    return base_make_settings(**values)  # type: ignore[arg-type]


def bearer(
    actor: str = "user-1",
    tenant: str = "tenant-1",
    scopes: tuple[str, ...] = ("ops:write", "ops:read"),
) -> dict[str, str]:
    token = jwt.encode(
        {
            "sub": actor,
            "tenant": tenant,
            "scope": " ".join(scopes),
            "iss": "https://test-issuer.local",
            "aud": "hitl-ops",
            "exp": datetime.now(UTC) + timedelta(minutes=10),
        },
        API_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def settings() -> Settings:
    return api_settings()


@pytest.fixture
def migrated_database(settings: Settings) -> Settings:
    reset_schema(settings.database_url)
    run_alembic_upgrade(settings.database_url, "head")
    return settings
