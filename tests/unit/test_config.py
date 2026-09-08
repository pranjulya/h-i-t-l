"""Configuration unit tests: sanitized failures and production safety."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hitl_ops.config import Environment, Settings


def test_development_defaults_are_safe() -> None:
    resolved = Settings(_env_file=None)
    assert resolved.app_env is Environment.DEVELOPMENT
    assert resolved.is_production is False
    assert resolved.log_level == "INFO"
    assert "localhost:5432" in resolved.database_url


def test_invalid_log_level_error_is_sanitized() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, log_level="NOT_A_LEVEL")
    assert "NOT_A_LEVEL" not in str(excinfo.value)


def test_invalid_database_url_error_is_sanitized() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, database_url="definitely-not-a-url")
    assert "definitely-not-a-url" not in str(excinfo.value)


def test_production_rejects_debug() -> None:
    with pytest.raises(ValidationError, match="debug"):
        Settings(_env_file=None, app_env=Environment.PRODUCTION, debug=True)


def test_production_rejects_implicit_default_database_url() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=None, app_env=Environment.PRODUCTION)


def test_production_rejects_default_credentials() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            _env_file=None,
            app_env=Environment.PRODUCTION,
            database_url="postgresql+asyncpg://svc:hitl@db.internal:5432/hitl_ops",
        )
    assert "hitl" not in str(excinfo.value)


def test_valid_production_configuration_is_accepted() -> None:
    resolved = Settings(
        _env_file=None,
        app_env=Environment.PRODUCTION,
        database_url="postgresql+asyncpg://svc:9f8e7d6c@db.internal:5432/hitl_ops",
    )
    assert resolved.is_production is True
