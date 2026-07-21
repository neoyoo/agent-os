from __future__ import annotations

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.redis._client import AsyncRedisClient, decode_text
from agentos.distributed.redis._event_codec import encode_envelope
from agentos.distributed._stream_models import ReplayItem, RunEventEnvelope


_ENSURE_TERMINAL_SCRIPT = """
-- agentos:replay:ensure-terminal:v1
local rows = redis.call('XRANGE', KEYS[1], '-', '+')
for _, row in ipairs(rows) do
    local fields = row[2]
    local attempt = nil
    local sequence = nil
    for index = 1, #fields, 2 do
        if fields[index] == 'execution_attempt' then
            attempt = fields[index + 1]
        elseif fields[index] == 'event_sequence' then
            sequence = fields[index + 1]
        end
    end
    if attempt == ARGV[1] and sequence == ARGV[2] then
        if #fields ~= #ARGV - 3 then
            return {'conflict', ''}
        end
        local expected = {}
        for index = 4, #ARGV, 2 do
            expected[ARGV[index]] = ARGV[index + 1]
        end
        for index = 1, #fields, 2 do
            if expected[fields[index]] ~= fields[index + 1] then
                return {'conflict', ''}
            end
        end
        return {'existing', row[1]}
    end
end
local values = {}
for index = 4, #ARGV do
    values[#values + 1] = ARGV[index]
end
local cursor = redis.call(
    'XADD', KEYS[1], 'MAXLEN', '=', ARGV[3], '*', unpack(values)
)
return {'appended', cursor}
"""


async def ensure_terminal(
    redis: AsyncRedisClient,
    *,
    stream: str,
    event: RunEventEnvelope,
    max_events: int,
) -> ReplayItem:
    fields = encode_envelope(event)
    flattened = tuple(
        item
        for name, value in fields.items()
        for item in (name, str(value))
    )
    response = await redis.call(
        "eval",
        _ENSURE_TERMINAL_SCRIPT,
        1,
        stream,
        str(event.execution_attempt),
        str(event.event_sequence),
        str(max_events),
        *flattened,
    )
    if not isinstance(response, (list, tuple)) or len(response) != 2:
        raise DeliveryUnavailableError()
    status = decode_text(response[0])
    cursor = decode_text(response[1])
    if status not in {"existing", "appended"} or cursor is None:
        raise DeliveryUnavailableError()
    return ReplayItem(cursor, event)


__all__ = ["ensure_terminal"]
