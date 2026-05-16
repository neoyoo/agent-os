from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.integration


def test_live_backend_environment_is_declared() -> None:
    if not os.environ.get("AGENTOS_RUN_INTEGRATION"):
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 with docker-compose.test.yml services")

    assert os.environ.get("AGENTOS_TEST_POSTGRES_DSN")
    assert os.environ.get("AGENTOS_TEST_REDIS_URL")
