from __future__ import annotations

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.redis._client import AsyncRedisClient, decode_text
from agentos.distributed.redis._team_event_codec import encode_team_envelope
from agentos.multi.team_event_types import TeamEventEnvelope, TeamEventReplayItem


MAX_TEAM_REPLAY_EVENTS = 1_000


_ENSURE_TEAM_EVENT_SCRIPT = """
-- agentos:team-replay:ensure-event:v2
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

local function event_sequence(fields)
    for index = 1, #fields, 2 do
        if fields[index] == 'event_sequence' then
            return fields[index + 1]
        end
    end
    return nil
end

local expected = {}
local values = {}
for index = 3, #ARGV, 2 do
    expected[ARGV[index]] = ARGV[index + 1]
    values[#values + 1] = ARGV[index]
    values[#values + 1] = ARGV[index + 1]
end

local cursor = ARGV[1] .. '-0'
local max_events = tonumber(ARGV[2])
local length = redis.call('XLEN', KEYS[1])
local oversized = length > max_events
local rows = {}
if length > max_events then
    local newest = redis.call(
        'XREVRANGE', KEYS[1], '+', '-', 'COUNT', ARGV[2]
    )
    for index = #newest, 1, -1 do
        rows[#rows + 1] = newest[index]
    end
elseif length > 0 then
    rows = redis.call('XRANGE', KEYS[1], '-', '+', 'COUNT', ARGV[2])
end
local valid = #rows == math.min(length, max_events)
for _, row in ipairs(rows) do
    local sequence = event_sequence(row[2])
    if not sequence or sequence .. '-0' ~= row[1] then
        valid = false
    end
end
if not valid then
    redis.call('UNLINK', KEYS[1])
    rows = {}
elseif oversized then
    redis.call('UNLINK', KEYS[1])
    for _, row in ipairs(rows) do
        redis.call('XADD', KEYS[1], row[1], unpack(row[2]))
    end
end

for _, row in ipairs(rows) do
    if row[1] == cursor then
        local fields = row[2]
        if #fields ~= #ARGV - 2 then
            return {'conflict', ''}
        end
        for index = 1, #fields, 2 do
            if expected[fields[index]] ~= fields[index + 1] then
                return {'conflict', ''}
            end
        end
        return {'existing', row[1]}
    end
end

if #rows == 0 or stream_id_less(rows[#rows][1], cursor) then
    redis.call(
        'XADD', KEYS[1], 'MAXLEN', '=', ARGV[2], cursor, unpack(values)
    )
    return {'appended', cursor}
end
if #rows >= max_events and stream_id_less(cursor, rows[1][1]) then
    return {'trimmed', cursor}
end

local keep_from = 1
if #rows >= max_events then
    keep_from = 2
end
redis.call('UNLINK', KEYS[1])
local inserted = false
for index = keep_from, #rows do
    if not inserted and stream_id_less(cursor, rows[index][1]) then
        redis.call('XADD', KEYS[1], cursor, unpack(values))
        inserted = true
    end
    redis.call('XADD', KEYS[1], rows[index][1], unpack(rows[index][2]))
end
if not inserted then
    redis.call('XADD', KEYS[1], cursor, unpack(values))
end
return {'appended', cursor}
"""


async def ensure_team_event(
    redis: AsyncRedisClient,
    *,
    stream: str,
    event: TeamEventEnvelope,
    max_events: int,
) -> TeamEventReplayItem:
    fields = encode_team_envelope(event)
    flattened = tuple(
        item for name, value in fields.items() for item in (name, str(value))
    )
    response = await redis.call(
        "eval",
        _ENSURE_TEAM_EVENT_SCRIPT,
        1,
        stream,
        str(event.event_sequence),
        str(max_events),
        *flattened,
    )
    if not isinstance(response, (list, tuple)) or len(response) != 2:
        raise DeliveryUnavailableError()
    status = decode_text(response[0])
    cursor = decode_text(response[1])
    if status not in {"existing", "appended", "trimmed"} or cursor is None:
        raise DeliveryUnavailableError()
    return TeamEventReplayItem(cursor, event)


__all__ = ["MAX_TEAM_REPLAY_EVENTS", "ensure_team_event"]
