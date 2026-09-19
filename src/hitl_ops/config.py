"""Twelve-factor configuration validated at startup.

Validation failures are sanitized: input values are never echoed back, and
production mode rejects debug configuration and default secrets.
"""

from __future__ import annotations

import enum
import logging
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_DATABASE_URL = "postgresql+asyncpg://hitl:hitl@localhost:5432/hitl_ops"
_REJECTED_PRODUCTION_PASSWORDS = frozenset({"hitl", "postgres", "password", "changeme", ""})
_MAX_REQUEST_BYTES_CEILING = 16 * 1024 * 1024
_MAX_RATE_LIMIT_PER_MINUTE = 1_000_000
# HS256 keys shorter than this are brute-forceable; the demo secrets that
# ship in compose/dev tooling must never reach production.
_MIN_IDENTITY_SECRET_BYTES = 32
_DEMO_IDENTITY_SECRETS = frozenset(
    {
        "learning-demo-identity-secret",
        "learning-demo-identity-secret-32-bytes-minimum",
    }
)


class Environment(enum.StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


def _database_password(url: str) -> str:
    try:
        return urlsplit(url).password or ""
    except ValueError:
        return ""


class Settings(BaseSettings):
    """Validated environment configuration; invalid values stop startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Environment = Environment.DEVELOPMENT
    debug: bool = False
    log_level: str = "INFO"
    database_url: str = _DEFAULT_DATABASE_URL
    otel_exporter_otlp_endpoint: str | None = None
    identity_issuer: str = "https://test-issuer.local"
    identity_audience: str = "hitl-ops"
    identity_shared_secret: str | None = None
    max_request_bytes: int = Field(default=65536, gt=0, le=_MAX_REQUEST_BYTES_CEILING)
    rate_limit_per_minute: int = Field(default=120, gt=0, le=_MAX_RATE_LIMIT_PER_MINUTE)
    # Backstop applied per client address to every request, including ones whose
    # credentials never validate. Kept above the per-principal limit so callers
    # behind a shared address are not throttled by one another.
    address_rate_limit_per_minute: int = Field(default=600, gt=0, le=_MAX_RATE_LIMIT_PER_MINUTE)

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        upper = value.upper()
        if upper not in logging.getLevelNamesMapping():
            raise ValueError("LOG_LEVEL must be a valid logging level name")
        return upper

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        scheme = urlsplit(value).scheme
        if not scheme.startswith("postgresql"):
            raise ValueError("DATABASE_URL must use a postgresql scheme")
        return value

    @model_validator(mode="after")
    def _production_safety(self) -> Settings:
        if self.app_env is not Environment.PRODUCTION:
            return self
        if self.debug:
            raise ValueError("debug mode is forbidden in production")
        if self.database_url == _DEFAULT_DATABASE_URL:
            raise ValueError("production requires an explicitly configured DATABASE_URL")
        if _database_password(self.database_url) in _REJECTED_PRODUCTION_PASSWORDS:
            raise ValueError("production rejects default database credentials")
        if self.identity_shared_secret is None:
            raise ValueError("production requires configured identity verification material")
        if len(self.identity_shared_secret.encode("utf-8")) < _MIN_IDENTITY_SECRET_BYTES:
            raise ValueError(
                f"production identity secret must be at least {_MIN_IDENTITY_SECRET_BYTES} bytes"
            )
        if self.identity_shared_secret in _DEMO_IDENTITY_SECRETS:
            raise ValueError("production rejects the demo identity secret")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env is Environment.PRODUCTION
