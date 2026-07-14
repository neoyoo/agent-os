import pytest

from tests.multi.helpers import close_tracked_sync_agents


@pytest.fixture(autouse=True)
def close_multi_sync_agents():
    yield
    close_tracked_sync_agents()
