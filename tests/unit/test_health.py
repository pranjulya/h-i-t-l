"""Liveness and stable error-envelope unit tests (no database required)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hitl_ops.api.app import create_app
from tests.conftest import make_settings

_UNAVAILABLE_DATABASE_URL = "postgresql+asyncpg://postgres@localhost:1/unreachable"


def test_liveness_has_no_dependencies() -> None:
    with TestClient(create_app(make_settings(database_url=_UNAVAILABLE_DATABASE_URL))) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_unknown_route_uses_stable_error_envelope() -> None:
    with TestClient(create_app(make_settings(database_url=_UNAVAILABLE_DATABASE_URL))) as client:
        response = client.get("/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["retryable"] is False
    assert error["correlation_id"]
    assert error["message"]
    assert error["details"] == {}
    assert response.headers["X-Correlation-ID"] == error["correlation_id"]


def test_client_correlation_id_is_honored() -> None:
    with TestClient(create_app(make_settings(database_url=_UNAVAILABLE_DATABASE_URL))) as client:
        response = client.get("/health/live", headers={"X-Correlation-ID": "corr-123"})
    assert response.headers["X-Correlation-ID"] == "corr-123"


def test_unreachable_database_readiness_is_sanitized() -> None:
    with TestClient(create_app(make_settings(database_url=_UNAVAILABLE_DATABASE_URL))) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
