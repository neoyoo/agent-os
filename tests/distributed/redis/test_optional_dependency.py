import sys

import pytest

from agentos.distributed.errors import DistributedBackendUnavailableError
from agentos.distributed.redis.leases import RedisLeaseAdapter
from agentos.distributed.redis.queue import RedisQueueAdapter
from agentos.distributed.redis.replay import RedisEventReplayAdapter


def test_redis_adapters_map_missing_optional_dependency_to_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "redis", None)

    for adapter_type in (
        RedisLeaseAdapter,
        RedisQueueAdapter,
        RedisEventReplayAdapter,
    ):
        with pytest.raises(DistributedBackendUnavailableError):
            adapter_type("redis://user:secret@example.invalid")
