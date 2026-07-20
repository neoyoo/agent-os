from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.redis._client import AsyncRedisClient, decode_text


_REPLAY_SNAPSHOT_SCRIPT = """
-- agentos:replay:snapshot:v1
local function decimal_less(left, right)
    left = string.gsub(left, '^0+', '')
    right = string.gsub(right, '^0+', '')
    if left == '' then left = '0' end
    if right == '' then right = '0' end
    if string.len(left) ~= string.len(right) then
        return string.len(left) < string.len(right)
    end
    return left < right
end

local function stream_id_less(left, right)
    local left_ms, left_seq = string.match(left, '^(%d+)%-(%d+)$')
    local right_ms, right_seq = string.match(right, '^(%d+)%-(%d+)$')
    if not left_ms or not right_ms then
        error('invalid stream id')
    end
    if left_ms ~= right_ms then
        return decimal_less(left_ms, right_ms)
    end
    return decimal_less(left_seq, right_seq)
end

local oldest_rows = redis.call('XRANGE', KEYS[1], '-', '+', 'COUNT', 1)
if ARGV[1] ~= '' and #oldest_rows == 0 then
    return {'gap', 'unavailable', ''}
end
local oldest = ''
if #oldest_rows > 0 then
    oldest = oldest_rows[1][1]
end
if ARGV[1] ~= '' and ARGV[1] ~= '0-0' and stream_id_less(ARGV[1], oldest) then
    return {'gap', 'trimmed', oldest}
end
local minimum = '-'
if ARGV[1] ~= '' then
    minimum = '(' .. ARGV[1]
end
local rows = redis.call('XRANGE', KEYS[1], minimum, '+', 'COUNT', ARGV[2])
return {'batch', oldest, rows}
"""


@dataclass(frozen=True, slots=True)
class ReplaySnapshot:
    rows: tuple[tuple[str, object], ...] = ()
    gap_reason: Literal["trimmed", "unavailable"] | None = None
    oldest: str | None = None


async def read_replay_snapshot(
    redis: AsyncRedisClient,
    *,
    stream: str,
    after: str | None,
    limit: int,
) -> ReplaySnapshot:
    response = await redis.call(
        "eval",
        _REPLAY_SNAPSHOT_SCRIPT,
        1,
        stream,
        after or "",
        limit,
    )
    if not isinstance(response, (list, tuple)) or len(response) != 3:
        raise DeliveryUnavailableError()
    status = decode_text(response[0])
    detail = decode_text(response[1])
    if status == "gap" and detail in {"trimmed", "unavailable"}:
        oldest = decode_text(response[2]) or None
        if detail == "trimmed" and oldest is None:
            raise DeliveryUnavailableError()
        return ReplaySnapshot(gap_reason=detail, oldest=oldest)
    if status != "batch":
        raise DeliveryUnavailableError()
    return ReplaySnapshot(rows=tuple(_eval_stream_rows(response[2])))


def _eval_stream_rows(value: object) -> list[tuple[str, object]]:
    if not isinstance(value, (list, tuple)):
        raise DeliveryUnavailableError()
    rows: list[tuple[str, object]] = []
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise DeliveryUnavailableError()
        cursor = decode_text(row[0])
        if cursor is None:
            raise DeliveryUnavailableError()
        rows.append((cursor, _eval_fields(row[1])))
    return rows


def _eval_fields(value: object) -> object:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, (list, tuple)) or len(value) % 2:
        raise DeliveryUnavailableError()
    return dict(zip(value[::2], value[1::2], strict=True))
