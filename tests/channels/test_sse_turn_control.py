import time

import pytest

from agentos.channels.sse_turn_control import (
    InMemorySseTurnControlStore,
    RedisSseTurnControlStore,
    SseTurnAlreadyActiveError,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries_ms: dict[str, int] = {}
        self.set_calls: list[tuple[str, str, bool, int]] = []
        self.eval_calls: list[tuple[str, int, tuple[object, ...]]] = []
        self.deleted: list[str] = []

    def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        xx: bool = False,
        px: int | None = None,
    ) -> bool:
        self.set_calls.append((name, value, nx, int(px or 0)))
        if nx and name in self.values:
            return False
        if xx and name not in self.values:
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

    def eval(self, script: str, numkeys: int, *args: object) -> int:
        self.eval_calls.append((script, numkeys, tuple(args)))
        if "interrupt_requested" in script:
            key = str(args[0])
            current = self.values.get(key)
            if current is None:
                return 0
            import json

            payload = json.loads(current)
            payload["interrupt_requested"] = True
            self.values[key] = json.dumps(payload, separators=(",", ":"))
            self.expiries_ms[key] = (
                self.expiries_ms[key] if "PTTL" in script else int(args[1])
            )
            return 1
        if "ARGV[2]" in script:
            key = str(args[0])
            expected_turn_id = str(args[1])
            current = self.values.get(key)
            if current is None:
                return 0
            import json

            payload = json.loads(current)
            if payload["turn_stream_id"] != expected_turn_id:
                return 0
            payload["expires_at"] = float(args[2])
            self.values[key] = json.dumps(payload, separators=(",", ":"))
            self.expiries_ms[key] = int(args[3])
            return 1
        key = str(args[0])
        expected_turn_id = str(args[1])
        current = self.values.get(key)
        if current is None:
            return 0
        import json

        payload = json.loads(current)
        if payload["turn_stream_id"] != expected_turn_id:
            return 0
        self.delete(key)
        return 1


class InterruptBeforeRefreshEvalRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.interrupt_before_refresh_eval = False

    def eval(self, script: str, numkeys: int, *args: object) -> int:
        if self.interrupt_before_refresh_eval and "ARGV[2]" in script:
            self.interrupt_before_refresh_eval = False
            key = str(args[0])
            current = self.values.get(key)
            if current is not None:
                import json

                payload = json.loads(current)
                payload["interrupt_requested"] = True
                self.values[key] = json.dumps(payload, separators=(",", ":"))
        return super().eval(script, numkeys, *args)


def test_in_memory_sse_turn_control_claims_single_active_turn() -> None:
    store = InMemorySseTurnControlStore()

    state = store.claim_turn(
        "session_1",
        turn_stream_id="turn_1",
        owner_id="node-a",
        ttl_seconds=30,
    )

    assert state.session_id == "session_1"
    assert state.turn_stream_id == "turn_1"
    assert state.owner_id == "node-a"
    assert not state.interrupt_requested
    with pytest.raises(SseTurnAlreadyActiveError, match="active stream turn"):
        store.claim_turn(
            "session_1",
            turn_stream_id="turn_2",
            owner_id="node-b",
            ttl_seconds=30,
        )


def test_in_memory_sse_turn_control_shares_interrupt_request() -> None:
    store = InMemorySseTurnControlStore()
    store.claim_turn(
        "session_1",
        turn_stream_id="turn_1",
        owner_id="node-a",
        ttl_seconds=30,
    )

    assert store.request_interrupt("session_1")
    assert store.get_turn("session_1").interrupt_requested  # type: ignore[union-attr]


def test_in_memory_sse_turn_control_releases_only_matching_turn() -> None:
    store = InMemorySseTurnControlStore()
    store.claim_turn(
        "session_1",
        turn_stream_id="turn_1",
        owner_id="node-a",
        ttl_seconds=30,
    )

    store.release_turn("session_1", "turn_other")
    assert store.get_turn("session_1") is not None

    store.release_turn("session_1", "turn_1")
    assert store.get_turn("session_1") is None


def test_in_memory_sse_turn_control_expires_stale_turn() -> None:
    store = InMemorySseTurnControlStore()
    store.claim_turn(
        "session_1",
        turn_stream_id="turn_1",
        owner_id="node-a",
        ttl_seconds=0.001,
    )

    deadline = time.monotonic() + 0.5
    while store.get_turn("session_1") is not None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert store.get_turn("session_1") is None
    store.claim_turn(
        "session_1",
        turn_stream_id="turn_2",
        owner_id="node-b",
        ttl_seconds=30,
    )


def test_redis_sse_turn_control_claims_interrupts_and_releases() -> None:
    client = FakeRedis()
    store = RedisSseTurnControlStore(
        url="redis://unused",
        client=client,
        key_prefix="agentos-test",
    )

    state = store.claim_turn(
        "session_1",
        turn_stream_id="turn_1",
        owner_id="node-a",
        ttl_seconds=30,
    )

    assert state.session_id == "session_1"
    assert client.set_calls[0][0] == "agentos-test:sse:turn:session_1"
    assert client.set_calls[0][2] is True
    assert client.set_calls[0][3] == 30_000
    assert store.request_interrupt("session_1")
    assert store.get_turn("session_1").interrupt_requested  # type: ignore[union-attr]

    store.release_turn("session_1", "turn_other")
    assert store.get_turn("session_1") is not None

    store.release_turn("session_1", "turn_1")
    assert store.get_turn("session_1") is None


def test_redis_sse_turn_control_refreshes_only_matching_turn_atomically() -> None:
    client = FakeRedis()
    store = RedisSseTurnControlStore(
        url="redis://unused",
        client=client,
        key_prefix="agentos-test",
    )
    store.claim_turn(
        "session_1",
        turn_stream_id="turn_1",
        owner_id="node-a",
        ttl_seconds=30,
    )

    store.refresh_turn("session_1", "turn_1", ttl_seconds=45)

    assert len(client.eval_calls) == 1
    script, numkeys, args = client.eval_calls[0]
    assert "turn_stream_id" in script
    assert numkeys == 1
    assert args[0] == "agentos-test:sse:turn:session_1"
    assert args[1] == "turn_1"
    assert args[3] == 45_000
    assert client.expiries_ms["agentos-test:sse:turn:session_1"] == 45_000


def test_redis_sse_turn_control_refresh_preserves_concurrent_interrupt() -> None:
    client = InterruptBeforeRefreshEvalRedis()
    store = RedisSseTurnControlStore(
        url="redis://unused",
        client=client,
        key_prefix="agentos-test",
    )
    store.claim_turn(
        "session_1",
        turn_stream_id="turn_1",
        owner_id="node-a",
        ttl_seconds=30,
    )

    client.interrupt_before_refresh_eval = True

    store.refresh_turn("session_1", "turn_1", ttl_seconds=45)

    refreshed = store.get_turn("session_1")
    assert refreshed is not None
    assert refreshed.interrupt_requested


def test_redis_sse_turn_control_cross_node_interrupt_preserves_existing_ttl() -> None:
    client = FakeRedis()
    owner_store = RedisSseTurnControlStore(
        url="redis://unused",
        client=client,
        key_prefix="agentos-test",
    )
    follower_store = RedisSseTurnControlStore(
        url="redis://unused",
        client=client,
        key_prefix="agentos-test",
    )
    owner_store.claim_turn(
        "session_1",
        turn_stream_id="turn_1",
        owner_id="node-a",
        ttl_seconds=5,
    )

    assert follower_store.request_interrupt("session_1")

    assert client.expiries_ms["agentos-test:sse:turn:session_1"] == 5_000
    assert follower_store.get_turn("session_1").interrupt_requested  # type: ignore[union-attr]
