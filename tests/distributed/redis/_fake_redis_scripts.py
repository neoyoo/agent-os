from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


UNHANDLED = object()


class ScriptRedis(Protocol):
    values: dict[str, object]
    streams: dict[str, list[tuple[str, dict[str, object]]]]
    groups: dict[tuple[str, str], str]
    pending: dict[tuple[str, str], dict[str, dict[str, object]]]

    async def xack(self, name: str, groupname: str, *ids: str) -> int: ...

    async def xadd(
        self,
        name: str,
        fields: Mapping[str, object],
        id: str = "*",
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str: ...

    async def before_atomic_trim(self, name: str) -> None: ...

    async def before_atomic_replay(self, name: str) -> None: ...

    def _raise_if_failed(self) -> None: ...


async def evaluate_agentos_script(
    redis: ScriptRedis,
    script: str,
    numkeys: int,
    args: tuple[object, ...],
) -> object:
    if "agentos:queue:reserve:v1" in script:
        assert numkeys == 3 and len(args) == 5
        processing, committed, stream, delivery_id, group = map(str, args)
        if committed in redis.values:
            await redis.xack(stream, group, delivery_id)
            return 2
        current = redis.values.get(processing)
        if current is None:
            redis.values[processing] = delivery_id
            return 1
        if current == delivery_id:
            return 1
        await redis.xack(stream, group, delivery_id)
        return 0

    if "agentos:queue:ack:v1" in script:
        assert numkeys == 3 and len(args) == 6
        processing, committed, stream = map(str, args[:3])
        delivery_id, group = map(str, args[3:5])
        current = redis.values.get(processing)
        committed_delivery = redis.values.get(committed)
        if committed_delivery is not None:
            if committed_delivery != delivery_id:
                return -1
            return await redis.xack(stream, group, delivery_id)
        if current != delivery_id:
            return -1
        redis.values[committed] = delivery_id
        redis.values.pop(processing, None)
        return await redis.xack(stream, group, delivery_id)

    if "agentos:queue:trim:v1" in script:
        assert numkeys == 1 and len(args) == 2
        return await _trim_safely(redis, str(args[0]), int(args[1]))

    if "agentos:replay:snapshot:v1" in script:
        assert numkeys == 1 and len(args) == 3
        return await _read_replay_snapshot(
            redis,
            str(args[0]),
            str(args[1]),
            int(args[2]),
        )

    if "agentos:replay:ensure-terminal:v1" in script:
        assert numkeys == 1 and len(args) >= 6 and (len(args) - 4) % 2 == 0
        stream, attempt, sequence, max_events = map(str, args[:4])
        expected = {
            str(name): str(value)
            for name, value in zip(args[4::2], args[5::2], strict=True)
        }
        for cursor, fields in redis.streams.get(stream, []):
            current = {str(name): str(value) for name, value in fields.items()}
            if (
                current.get("execution_attempt") == attempt
                and current.get("event_sequence") == sequence
            ):
                if current != expected:
                    return ["conflict", ""]
                return ["existing", cursor]
        cursor = await redis.xadd(
            stream,
            expected,
            id="*",
            maxlen=int(max_events),
            approximate=False,
        )
        return ["appended", cursor]

    return UNHANDLED


async def _trim_safely(redis: ScriptRedis, name: str, max_entries: int) -> int:
    await redis.before_atomic_trim(name)
    stream = redis.streams.get(name, [])
    if len(stream) <= max_entries:
        return 0
    boundaries: list[str] = []
    for (stream_name, group), last_id in redis.groups.items():
        if stream_name != name:
            continue
        pending_ids = sorted(redis.pending[(name, group)], key=stream_id_key)
        boundary = pending_ids[0] if pending_ids else last_id
        if boundary == "0-0":
            return 0
        boundaries.append(boundary)
    if not boundaries:
        return 0
    boundary = min(boundaries, key=stream_id_key)
    kept = [entry for entry in stream if not stream_id_gt(boundary, entry[0])]
    removed = len(stream) - len(kept)
    redis.streams[name] = kept
    return removed


async def _read_replay_snapshot(
    redis: ScriptRedis,
    name: str,
    after: str,
    limit: int,
) -> object:
    await redis.before_atomic_replay(name)
    stream = redis.streams.get(name, [])
    if after and not stream:
        return ["gap", "unavailable", ""]
    oldest = stream[0][0] if stream else ""
    if after and after != "0-0" and stream_id_key(after) < stream_id_key(oldest):
        return ["gap", "trimmed", oldest]
    rows = [
        entry for entry in stream if not after or stream_id_gt(entry[0], after)
    ][:limit]
    raw_rows = [[message_id, _raw_field_pairs(fields)] for message_id, fields in rows]
    return ["batch", oldest, raw_rows]


async def xinfo_groups(redis: ScriptRedis, name: str) -> list[dict[str, object]]:
    redis._raise_if_failed()
    return [
        {
            "name": group,
            "last-delivered-id": last_id,
            "pending": len(redis.pending[(stream, group)]),
        }
        for (stream, group), last_id in redis.groups.items()
        if stream == name
    ]


async def xtrim(redis: ScriptRedis, name: str, minid: str) -> int:
    redis._raise_if_failed()
    stream = redis.streams.get(name, [])
    kept = [entry for entry in stream if not stream_id_gt(minid, entry[0])]
    removed = len(stream) - len(kept)
    redis.streams[name] = kept
    return removed


async def xrange(
    redis: ScriptRedis,
    name: str,
    minimum: str,
    count: int | None,
) -> list[tuple[str, dict[str, object]]]:
    redis._raise_if_failed()
    exclusive = minimum.startswith("(")
    lower = minimum[1:] if exclusive else minimum
    rows = [
        entry
        for entry in redis.streams.get(name, [])
        if lower == "-"
        or stream_id_gt(entry[0], lower)
        or (not exclusive and entry[0] == lower)
    ]
    return rows[:count]


def stream_id_gt(left: str, right: str) -> bool:
    return stream_id_key(left) > stream_id_key(right)


def stream_id_key(value: str) -> tuple[int, int]:
    milliseconds, sequence = value.split("-", 1)
    return int(milliseconds), int(sequence)


def _raw_field_pairs(fields: Mapping[str, object]) -> list[object]:
    pairs: list[object] = []
    for name, value in fields.items():
        pairs.extend((name, value))
    return pairs
