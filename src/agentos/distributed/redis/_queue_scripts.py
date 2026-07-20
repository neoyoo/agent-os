from __future__ import annotations

from urllib.parse import quote

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.redis._client import AsyncRedisClient


_RESERVE_DELIVERY_SCRIPT = """
-- agentos:queue:reserve:v1
if redis.call('GET', KEYS[2]) then
    redis.call('XACK', KEYS[3], ARGV[2], ARGV[1])
    return 2
end
local current = redis.call('GET', KEYS[1])
if not current then
    redis.call('SET', KEYS[1], ARGV[1])
    return 1
end
if current == ARGV[1] then
    return 1
end
redis.call('XACK', KEYS[3], ARGV[2], ARGV[1])
return 0
"""

_ACK_DELIVERY_SCRIPT = """
-- agentos:queue:ack:v1
local committed = redis.call('GET', KEYS[2])
local current = redis.call('GET', KEYS[1])
if committed then
    if committed ~= ARGV[1] then
        return -1
    end
    redis.call('EXPIRE', KEYS[2], ARGV[3])
    return redis.call('XACK', KEYS[3], ARGV[2], ARGV[1])
end
if current ~= ARGV[1] then
    return -1
end
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[3])
redis.call('DEL', KEYS[1])
return redis.call('XACK', KEYS[3], ARGV[2], ARGV[1])
"""

_TRIM_SAFELY_SCRIPT = """
-- agentos:queue:trim:v1
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

if redis.call('XLEN', KEYS[1]) <= tonumber(ARGV[1]) then
    return 0
end
local groups = redis.call('XINFO', 'GROUPS', KEYS[1])
local boundary = nil
for _, group in ipairs(groups) do
    local name = nil
    local last_delivered = nil
    for index = 1, #group, 2 do
        if group[index] == 'name' then
            name = group[index + 1]
        elseif group[index] == 'last-delivered-id' then
            last_delivered = group[index + 1]
        end
    end
    if not name or not last_delivered then
        error('invalid consumer group metadata')
    end
    local pending = redis.call('XPENDING', KEYS[1], name)
    local candidate = last_delivered
    if pending[1] > 0 then
        candidate = pending[2]
    end
    if candidate == '0-0' then
        return 0
    end
    if not boundary or stream_id_less(candidate, boundary) then
        boundary = candidate
    end
end
if not boundary then
    return 0
end
return redis.call('XTRIM', KEYS[1], 'MINID', boundary)
"""


async def reserve_delivery(
    redis: AsyncRedisClient,
    *,
    stream: str,
    group: str,
    outbox_id: str,
    delivery_id: str,
) -> str:
    result = await redis.call(
        "eval",
        _RESERVE_DELIVERY_SCRIPT,
        3,
        _processing_key(stream, group, outbox_id),
        _committed_key(stream, group, outbox_id),
        stream,
        delivery_id,
        group,
    )
    states = {0: "duplicate", 1: "reserved", 2: "committed"}
    try:
        return states[result]
    except (KeyError, TypeError):
        raise DeliveryUnavailableError() from None


async def commit_delivery(
    redis: AsyncRedisClient,
    *,
    stream: str,
    group: str,
    outbox_id: str,
    delivery_id: str,
    ttl_seconds: int,
) -> None:
    result = await redis.call(
        "eval",
        _ACK_DELIVERY_SCRIPT,
        3,
        _processing_key(stream, group, outbox_id),
        _committed_key(stream, group, outbox_id),
        stream,
        delivery_id,
        group,
        ttl_seconds,
    )
    if type(result) is not int or result < 0:
        raise DeliveryUnavailableError()


async def trim_safely(
    redis: AsyncRedisClient,
    *,
    stream: str,
    max_entries: int,
) -> None:
    result = await redis.call(
        "eval",
        _TRIM_SAFELY_SCRIPT,
        1,
        stream,
        max_entries,
    )
    if type(result) is not int or result < 0:
        raise DeliveryUnavailableError()


def _processing_key(stream: str, group: str, outbox_id: str) -> str:
    return f"{stream}:group:{quote(group, safe='')}:processing:{quote(outbox_id, safe='')}"


def _committed_key(stream: str, group: str, outbox_id: str) -> str:
    return f"{stream}:group:{quote(group, safe='')}:committed:{quote(outbox_id, safe='')}"
