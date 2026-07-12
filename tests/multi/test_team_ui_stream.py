from __future__ import annotations

from agentos.multi.serializers import (
    team_ui_event_from_dict,
    team_ui_event_to_dict,
)
from agentos.multi.team import InMemoryTeamUiStreamStore, TeamUiEvent


def test_in_memory_team_ui_stream_appends_and_replays_after_cursor() -> None:
    stream = InMemoryTeamUiStreamStore()

    created = stream.append(
        team_id="team_1",
        kind="team_created",
        payload={"team_id": "team_1", "leader_agent_id": "leader"},
        created_at=1.0,
    )
    message = stream.append(
        team_id="team_1",
        kind="message_appended",
        payload={"message_id": "message_1", "from_agent_id": "leader"},
        created_at=2.0,
    )

    assert created.event_id == 1
    assert message.event_id == 2
    assert stream.list_events("team_1") == (created, message)
    assert stream.list_events("team_1", after_event_id=1) == (message,)
    assert stream.list_events("other_team") == ()


def test_in_memory_team_ui_stream_bounds_old_events() -> None:
    stream = InMemoryTeamUiStreamStore(max_events_per_team=2)

    first = stream.append(
        team_id="team_1",
        kind="team_created",
        payload={"team_id": "team_1"},
        created_at=1.0,
    )
    second = stream.append(
        team_id="team_1",
        kind="member_added",
        payload={"agent_id": "worker_a"},
        created_at=2.0,
    )
    third = stream.append(
        team_id="team_1",
        kind="member_added",
        payload={"agent_id": "worker_b"},
        created_at=3.0,
    )

    assert stream.list_events("team_1") == (second, third)
    assert stream.list_events("team_1", after_event_id=first.event_id) == (
        second,
        third,
    )


def test_team_ui_event_serializes_and_round_trips() -> None:
    event = TeamUiEvent(
        event_id=3,
        team_id="team_1",
        kind="worker_run_completed",
        payload={
            "team_id": "team_1",
            "agent_id": "worker",
            "session_id": "session_worker",
            "delivery_id": "delivery_1",
            "status": "completed",
            "attempt": 2,
        },
        created_at=4.0,
    )

    assert team_ui_event_from_dict(team_ui_event_to_dict(event)) == event
