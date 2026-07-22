from __future__ import annotations

import asyncio
from collections.abc import Mapping

from _fake_redis_scripts import (
    UNHANDLED,
    evaluate_agentos_script,
    stream_id_gt,
    stream_id_key,
    xinfo_groups as script_xinfo_groups,
    xrange as script_xrange,
    xtrim as script_xtrim,
)


class FakeRedisError(RuntimeError):
    pass


class FakeAsyncRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.streams: dict[str, list[tuple[str, dict[str, object]]]] = {}
        self.groups: dict[tuple[str, str], str] = {}
        self.pending: dict[tuple[str, str], dict[str, dict[str, object]]] = {}
        self.xadd_ids: list[str] = []
        self.eval_calls: list[tuple[str, int, tuple[object, ...]]] = []
        self.read_started = asyncio.Event()
        self.fail = False
        self.closed = False
        self._sequence: dict[str, int] = {}
        self._signals: dict[str, asyncio.Event] = {}

    async def set(
        self,
        name: str,
        value: object,
        *,
        nx: bool = False,
        px: int | None = None,
        ex: int | None = None,
    ) -> bool | None:
        self._raise_if_failed()
        if nx and name in self.values:
            return None
        self.values[name] = value
        return True

    async def get(self, name: str) -> object | None:
        self._raise_if_failed()
        return self.values.get(name)

    async def eval(self, script: str, numkeys: int, *args: object) -> object:
        self._raise_if_failed()
        self.eval_calls.append((script, numkeys, args))
        result = await evaluate_agentos_script(self, script, numkeys, args)
        if result is not UNHANDLED:
            return result
        assert numkeys == 1
        key = str(args[0])
        expected = args[1]
        if self.values.get(key) != expected:
            return 0
        if "PEXPIRE" in script:
            return 1
        del self.values[key]
        return 1

    async def before_atomic_trim(self, name: str) -> None:
        pass

    async def before_atomic_replay(self, name: str) -> None:
        pass

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",
        *,
        mkstream: bool = False,
    ) -> bool:
        self._raise_if_failed()
        key = (name, groupname)
        if key in self.groups:
            raise FakeRedisError("BUSYGROUP Consumer Group name already exists")
        if mkstream:
            self.streams.setdefault(name, [])
        self.groups[key] = "0-0" if id == "0-0" else self._last_id(name)
        self.pending[key] = {}
        return True

    async def xadd(
        self,
        name: str,
        fields: Mapping[str, object],
        id: str = "*",
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        self._raise_if_failed()
        self.xadd_ids.append(id)
        stream = self.streams.setdefault(name, [])
        if id == "*":
            sequence = self._sequence.get(name, 0) + 1
            message_id = f"{sequence}-0"
        else:
            stream_id_key(id)
            if stream and not stream_id_gt(id, stream[-1][0]):
                raise FakeRedisError("stream ID is not greater than the top item")
            sequence = max(self._sequence.get(name, 0), stream_id_key(id)[0])
            message_id = id
        self._sequence[name] = sequence
        stream.append((message_id, dict(fields)))
        if maxlen is not None and len(stream) > maxlen:
            del stream[: len(stream) - maxlen]
        self._wake(name)
        return message_id

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Mapping[str, str],
        *,
        count: int | None = None,
        block: int | None = None,
        noack: bool = False,
    ) -> list[tuple[str, list[tuple[str, dict[str, object]]]]]:
        self._raise_if_failed()
        name, position = next(iter(streams.items()))
        assert position == ">"
        while True:
            last_id = self.groups[(name, groupname)]
            messages = [
                entry
                for entry in self.streams.get(name, [])
                if stream_id_gt(entry[0], last_id)
            ][:count]
            if messages:
                self.groups[(name, groupname)] = messages[-1][0]
                if not noack:
                    pending = self.pending[(name, groupname)]
                    for message_id, _ in messages:
                        pending[message_id] = {
                            "consumer": consumername,
                            "times_delivered": 1,
                            "idle": 0,
                        }
                return [(name, messages)]
            if block is None:
                return []
            self.read_started.set()
            await self._signal(name).wait()
            self._raise_if_failed()

    async def xpending_range(
        self,
        name: str,
        groupname: str,
        min: str,
        max: str,
        count: int,
        *,
        consumername: str | None = None,
        idle: int | None = None,
    ) -> list[dict[str, object]]:
        self._raise_if_failed()
        rows: list[dict[str, object]] = []
        for message_id, item in sorted(
            self.pending[(name, groupname)].items(),
            key=lambda pair: stream_id_key(pair[0]),
        ):
            if consumername is not None and item["consumer"] != consumername:
                continue
            if idle is not None and int(item["idle"]) < idle:
                continue
            rows.append({"message_id": message_id, **item})
        return rows[:count]

    async def xclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        message_ids: list[str],
    ) -> list[tuple[str, dict[str, object]]]:
        self._raise_if_failed()
        pending = self.pending[(name, groupname)]
        stream = dict(self.streams.get(name, []))
        claimed: list[tuple[str, dict[str, object]]] = []
        for message_id in message_ids:
            item = pending.get(message_id)
            if item is None or int(item["idle"]) < min_idle_time:
                continue
            item["consumer"] = consumername
            item["times_delivered"] = int(item["times_delivered"]) + 1
            item["idle"] = 0
            claimed.append((message_id, stream[message_id]))
        return claimed

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        self._raise_if_failed()
        pending = self.pending[(name, groupname)]
        removed = 0
        for message_id in ids:
            if pending.pop(message_id, None) is not None:
                removed += 1
        return removed

    async def xpending(self, name: str, groupname: str) -> dict[str, object]:
        self._raise_if_failed()
        ids = sorted(self.pending[(name, groupname)], key=stream_id_key)
        return {
            "pending": len(ids),
            "min": ids[0] if ids else None,
            "max": ids[-1] if ids else None,
            "consumers": [],
        }

    async def xlen(self, name: str) -> int:
        self._raise_if_failed()
        return len(self.streams.get(name, []))

    async def xinfo_groups(self, name: str) -> list[dict[str, object]]:
        return await script_xinfo_groups(self, name)

    async def xtrim(
        self,
        name: str,
        *,
        minid: str,
        approximate: bool = False,
    ) -> int:
        return await script_xtrim(self, name, minid)

    async def xrange(
        self,
        name: str,
        min: str = "-",
        max: str = "+",
        *,
        count: int | None = None,
    ) -> list[tuple[str, dict[str, object]]]:
        return await script_xrange(self, name, min, count)

    async def xrevrange(
        self,
        name: str,
        max: str = "+",
        min: str = "-",
        *,
        count: int | None = None,
    ) -> list[tuple[str, dict[str, object]]]:
        del max, min
        rows = list(reversed(self.streams.get(name, [])))
        return rows if count is None else rows[:count]

    async def xread(
        self,
        streams: Mapping[str, str],
        *,
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, object]]]]]:
        self._raise_if_failed()
        name, after = next(iter(streams.items()))
        while True:
            rows = [
                entry
                for entry in self.streams.get(name, [])
                if stream_id_gt(entry[0], after)
            ][:count]
            if rows:
                return [(name, rows)]
            if block is None:
                return []
            self.read_started.set()
            await self._signal(name).wait()
            self._raise_if_failed()

    async def aclose(self) -> None:
        self.closed = True

    def advance_pending(self, milliseconds: int) -> None:
        for pending in self.pending.values():
            for item in pending.values():
                item["idle"] = int(item["idle"]) + milliseconds

    def stream_fields(self, name: str) -> list[dict[str, object]]:
        return [fields for _, fields in self.streams.get(name, [])]

    def _last_id(self, name: str) -> str:
        stream = self.streams.get(name, [])
        return stream[-1][0] if stream else "0-0"

    def _signal(self, name: str) -> asyncio.Event:
        return self._signals.setdefault(name, asyncio.Event())

    def _wake(self, name: str) -> None:
        signal = self._signals.get(name)
        if signal is not None:
            signal.set()
        self._signals[name] = asyncio.Event()

    def _raise_if_failed(self) -> None:
        if self.fail:
            raise FakeRedisError("redis://user:secret@example.invalid")
