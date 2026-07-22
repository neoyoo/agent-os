import asyncio
from datetime import UTC, datetime
import os
from uuid import uuid4

import pytest

from agentos.distributed.models import RequestScope
from agentos.distributed.redis._team_event_codec import encode_team_envelope
from agentos.distributed.redis.team_replay import RedisTeamEventReplayAdapter
from agentos.multi.team_delivery_types import TeamDeliveryResult
from agentos.multi.team_event_types import (
    TeamDeliveryAppliedEvent,
    TeamEventEnvelope,
    TeamEventReplayBatch,
    TeamStreamGap,
)
from agentos.runtime.run_state import RunStatus


REDIS_URL = os.environ.get("AGENTOS_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(
    REDIS_URL is None,
    reason="set AGENTOS_TEST_REDIS_URL to run live Redis tests",
)
SCOPE = RequestScope("tenant_1", "user_1")
NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)


def _event(sequence: int) -> TeamEventEnvelope:
    return TeamEventEnvelope(
        tenant_id=SCOPE.tenant_id,
        team_id="team_1",
        event_sequence=sequence,
        event=TeamDeliveryAppliedEvent(
            delivery_id="team_delivery_" + "3" * 64,
            result=TeamDeliveryResult(
                result_kind="internal_start",
                observed_run_id="run_1",
                observed_aggregate_version=sequence,
                observed_run_status=RunStatus.QUEUED,
            ),
        ),
        occurred_at=NOW,
    )


def test_live_team_replay_handles_ordering_retention_and_oversized_streams() -> None:
    async def scenario() -> None:
        from redis.asyncio import Redis

        client = Redis.from_url(REDIS_URL, decode_responses=True)
        prefix = f"agentos_team_live_{uuid4().hex}"
        replay = RedisTeamEventReplayAdapter(
            client=client,
            key_prefix=prefix,
            max_events=2,
        )
        try:
            second = await replay.append(scope=SCOPE, event=_event(2))
            first = await replay.append(scope=SCOPE, event=_event(1))
            assert await replay.append(scope=SCOPE, event=_event(1)) == first
            third = await replay.append(scope=SCOPE, event=_event(3))
            assert await replay.append(scope=SCOPE, event=_event(1)) == first

            assert await replay.replay(
                scope=SCOPE,
                team_id="team_1",
                after=first.cursor,
                limit=10,
            ) == TeamStreamGap(
                SCOPE.tenant_id,
                "team_1",
                first.cursor,
                second.cursor,
                "trimmed",
            )
            assert await replay.replay(
                scope=SCOPE,
                team_id="team_1",
                after=second.cursor,
                limit=10,
            ) == TeamEventReplayBatch((third,), third.cursor)

            stream = f"{prefix}:team-events:tenant_1:team_1"
            fourth_event = _event(4)
            await client.xadd(
                stream,
                encode_team_envelope(fourth_event),
                id="4-0",
            )
            fifth = await replay.append(scope=SCOPE, event=_event(5))
            canonical = await replay.replay(
                scope=SCOPE,
                team_id="team_1",
                after=None,
                limit=10,
            )
            assert isinstance(canonical, TeamEventReplayBatch)
            assert tuple(item.event.event_sequence for item in canonical.items) == (4, 5)
            assert canonical.next_cursor == fifth.cursor

            for index in range(3):
                await client.xadd(stream, {"legacy": str(index)})
            sixth = await replay.append(scope=SCOPE, event=_event(6))
            assert await replay.replay(
                scope=SCOPE,
                team_id="team_1",
                after=None,
                limit=10,
            ) == TeamEventReplayBatch((sixth,), sixth.cursor)
        finally:
            await replay.close()
            keys = [
                key
                async for key in client.scan_iter(match=f"{prefix}:*")
            ]
            if keys:
                await client.delete(*keys)
            await client.aclose()

    asyncio.run(scenario())
