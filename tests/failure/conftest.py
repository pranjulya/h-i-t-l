"""Failure-injection fixtures share the API/integration environment."""

from __future__ import annotations

import pytest

from tests.api.conftest import api_settings  # noqa: F401
from tests.integration.conftest import prepare_database


@pytest.fixture
def failure_database(settings) -> object:
    prepare_database(settings.database_url)
    return settings
