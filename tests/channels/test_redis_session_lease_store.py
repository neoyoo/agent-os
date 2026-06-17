from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agentos.channels.durable_session import (
    RedisSessionLeaseStore,
    SessionLeaseError,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries_ms: dict[str, int] = {}
        self.counters: dict[str, int] = {}
        self.set_calls: list[tuple[str, str, bool, int]] = []
        self.eval_calls: list[tuple[str, int, tuple[object, ...]]] = []
        self.deleted: list[str] = []

    def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
    ) -> bool:
        self.set_calls.append((name, value, nx, int(px or 0)))
        if nx and name in self.values:
            return False
        self.values[name] = value
        self.expiries_ms[name] = int(px or 0)
        return True

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def delete(self, name: str) -> int:
        self.deleted.append(name)
        if name in self.values:
            del self.values[name]
            self.expiries_ms.pop(name, None)
            return 1
        return 0

    def incr(self, name: str) -> int:
        self.counters[name] = self.counters.get(name, 0) + 1
        return self.counters[name]

    def eval(self, script: str, numkeys: int, *args: object) -> int:
        self.eval_calls.append((script, numkeys, tuple(args)))
        key = str(args[0])
        expected_token = str(args[1])
        current = self.values.get(key)
        if current is None:
            return 0
        payload = json.loads(current)
        if payload["token"] != expected_token:
            return 0
        if len(args) == 2:
            self.delete(key)
            return 1
        new_payload = str(args[2])
        ttl_ms = int(args[3])
        self.values[key] = new_payload
        self.expiries_ms[key] = ttl_ms
        return 1


def test_redis_session_lease_store_acquires_with_set_nx_px() -> None:
    client = FakeRedis()
    store = RedisSessionLeaseStore(
        url="redis://unused",
        client=client,
        key_prefix="agentos-test",
    )

    lease = store.acquire(
        "s1",
        owner_id="node-a",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )

    assert lease.session_id == "s1"
    assert lease.owner_id == "node-a"
    assert lease.token
    assert lease.fence == 1
    assert lease.expires_at is not None
    assert client.set_calls[0][0] == "agentos-test:session:lease:s1"
    assert client.set_calls[0][2] is True
    assert client.set_calls[0][3] == 30_000
    payload = json.loads(client.values["agentos-test:session:lease:s1"])
    assert payload["owner_id"] == "node-a"
    assert payload["token"] == lease.token
    assert payload["fence"] == 1


def test_redis_session_lease_store_increments_fencing_token_per_acquire() -> None:
    client = FakeRedis()
    store = RedisSessionLeaseStore(url="redis://unused", client=client)

    first = store.acquire(
        "s1",
        owner_id="node-a",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )
    store.release(first)
    second = store.acquire(
        "s1",
        owner_id="node-b",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )

    assert first.fence == 1
    assert second.fence == 2
    payload = json.loads(client.values["agentos:session:lease:s1"])
    assert payload["fence"] == 2


def test_redis_session_lease_store_rejects_concurrent_acquire() -> None:
    client = FakeRedis()
    store = RedisSessionLeaseStore(url="redis://unused", client=client)

    store.acquire(
        "s1",
        owner_id="node-a",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )

    with pytest.raises(SessionLeaseError, match="session is locked"):
        store.acquire(
            "s1",
            owner_id="node-b",
            ttl_seconds=30.0,
            wait_timeout_seconds=0,
        )


def test_redis_session_lease_store_ignores_stale_release() -> None:
    client = FakeRedis()
    store = RedisSessionLeaseStore(url="redis://unused", client=client)
    first = store.acquire(
        "s1",
        owner_id="node-a",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )
    store.release(first)
    second = store.acquire(
        "s1",
        owner_id="node-b",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )

    store.release(first)

    assert json.loads(client.values["agentos:session:lease:s1"])["token"] == second.token
    store.release(second)
    assert "agentos:session:lease:s1" not in client.values


def test_redis_session_lease_store_releases_with_atomic_compare_token_script() -> None:
    client = FakeRedis()
    store = RedisSessionLeaseStore(url="redis://unused", client=client)
    lease = store.acquire(
        "s1",
        owner_id="node-a",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )

    store.release(lease)

    assert client.eval_calls
    script, numkeys, args = client.eval_calls[-1]
    assert "cjson.decode" in script
    assert numkeys == 1
    assert args == ("agentos:session:lease:s1", lease.token)
    assert "agentos:session:lease:s1" not in client.values
    assert client.deleted == ["agentos:session:lease:s1"]


def test_redis_session_lease_store_refresh_preserves_owner_and_extends_ttl() -> None:
    client = FakeRedis()
    store = RedisSessionLeaseStore(url="redis://unused", client=client)
    lease = store.acquire(
        "s1",
        owner_id="node-a",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )

    refreshed = store.refresh(lease)

    assert refreshed.session_id == lease.session_id
    assert refreshed.owner_id == lease.owner_id
    assert refreshed.token == lease.token
    assert refreshed.fence == lease.fence
    assert refreshed.expires_at is not None
    assert refreshed.expires_at >= lease.expires_at  # type: ignore[operator]
    assert client.expiries_ms["agentos:session:lease:s1"] == 30_000


def test_redis_session_lease_store_refreshes_with_atomic_compare_token_script() -> None:
    client = FakeRedis()
    store = RedisSessionLeaseStore(url="redis://unused", client=client)
    lease = store.acquire(
        "s1",
        owner_id="node-a",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )

    refreshed = store.refresh(lease)

    assert client.eval_calls
    script, numkeys, args = client.eval_calls[-1]
    assert "cjson.decode" in script
    assert numkeys == 1
    assert args[0] == "agentos:session:lease:s1"
    assert args[1] == lease.token
    assert json.loads(str(args[2]))["token"] == lease.token
    assert json.loads(str(args[2]))["fence"] == lease.fence
    assert args[3] == 30_000
    assert json.loads(client.values["agentos:session:lease:s1"])["token"] == refreshed.token


def test_redis_session_lease_store_ensure_owned_compares_current_token() -> None:
    client = FakeRedis()
    store = RedisSessionLeaseStore(url="redis://unused", client=client)
    lease = store.acquire(
        "s1",
        owner_id="node-a",
        ttl_seconds=30.0,
        wait_timeout_seconds=0,
    )

    store.ensure_owned(lease)

    assert "agentos:session:lease:s1" in client.values
    assert client.expiries_ms["agentos:session:lease:s1"] == 30_000

    with pytest.raises(SessionLeaseError, match="session lease is not owned"):
        store.ensure_owned(replace(lease, token="stale-token"))
