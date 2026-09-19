"""Security test fixtures share the API test environment."""

from __future__ import annotations

import pytest

from tests.api.conftest import api_settings  # noqa: F401
from tests.integration.conftest import prepare_database


@pytest.fixture
def hardened_database(settings) -> object:
    prepare_database(settings.database_url)
    return settings
