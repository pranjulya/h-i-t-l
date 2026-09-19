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
        identity_shared_secret="a" * 32,
    )
    assert resolved.is_production is True


@pytest.mark.parametrize("value", [0, -1, -65536])
def test_request_size_limit_must_be_positive(value: int) -> None:
    # A zero or negative cap would reject every request; fail at startup instead.
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_request_bytes=value)


@pytest.mark.parametrize("value", [0, -1, -120])
def test_rate_limit_must_be_positive(value: int) -> None:
    # A zero or negative limit would make the service permanently unusable.
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rate_limit_per_minute=value)


@pytest.mark.parametrize("value", [0, -1])
def test_address_rate_limit_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, address_rate_limit_per_minute=value)


def test_limits_reject_values_beyond_the_supported_range() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_request_bytes=64 * 1024 * 1024 * 1024)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rate_limit_per_minute=10_000_000_000)


def test_default_limits_start_up_cleanly() -> None:
    resolved = Settings(_env_file=None)
    assert resolved.max_request_bytes > 0
    assert resolved.rate_limit_per_minute > 0
