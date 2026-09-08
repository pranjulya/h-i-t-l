"""Security test fixtures share the API test environment."""

from __future__ import annotations

import pytest

from tests.api.conftest import api_settings  # noqa: F401
from tests.integration.conftest import reset_schema, run_alembic_upgrade


@pytest.fixture
def hardened_database(settings) -> object:
    reset_schema(settings.database_url)
    run_alembic_upgrade(settings.database_url, "head")
    return settings
