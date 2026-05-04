from agentos.compression import CompressionIndex
from agentos.context import ContextState, WorkingStateField, WorkingStateSchema
from agentos.context.state import CompressedSegment
from agentos.messages import MessageRuntime, ToolCall
from agentos.observability.events import EventLog, EventRecord
from agentos.persistence.base import SessionSnapshot
from agentos.persistence.serializers import (
    compression_index_from_dict,
    compression_index_to_dict,
    context_state_from_dict,
    context_state_to_dict,
    message_runtime_from_dict,
    message_runtime_to_dict,
    session_snapshot_from_dict,
    session_snapshot_to_dict,
)
from agentos.runtime import SessionState, TurnStartedEvent


def test_session_snapshot_event_records_are_typed_event_records() -> None:
    field = SessionSnapshot.__dataclass_fields__["event_records"]

    assert field.type == tuple[EventRecord, ...]


def test_context_state_round_trips_without_exposing_mutable_lists() -> None:
    state = ContextState(
        working_state_schema=WorkingStateSchema(
            fields=[
                WorkingStateField(
                    name="task_goal",
                    type="str",
                    purpose="当前任务目标和完成标准",
                ),
            ],
        ),
        working_state={"task_goal": "Persist context."},
        compressed_history=[
            CompressedSegment(id="seg_1", topic="history", summary="Old details."),
        ],
        inherited_state=["Keep architecture boundaries."],
        memory_context=["User prefers Chinese."],
    )

    restored = context_state_from_dict(context_state_to_dict(state))

    assert restored.working_state["task_goal"] == "Persist context."
    assert restored.compressed_history == state.compressed_history
    assert restored.inherited_state == ("Keep architecture boundaries.",)
    assert restored.memory_context == ("User prefers Chinese.",)


def test_message_runtime_round_trips_originals_active_refs_and_next_id() -> None:
    runtime = MessageRuntime()
    user = runtime.append_user("Need docs")
    assistant = runtime.append_assistant(
        "",
        tool_calls=[
            ToolCall(
                id="call_1",
                name="load_skill",
                arguments={"skill_name": "code-review"},
            ),
        ],
    )
    tool = runtime.append_tool_result("call_1", "Skill body")
    runtime.active_window.remove_refs([user.id], runtime.store)
    runtime.inject_temporary_recalled([user.id])

    restored = message_runtime_from_dict(message_runtime_to_dict(runtime))
    new_message = restored.append_user("Next")

    assert [message.id for message in restored.store.all()] == [
        user.id,
        assistant.id,
        tool.id,
        "msg_4",
    ]
    assert new_message.id == "msg_4"
    assert [ref.message_id for ref in restored.active_window.refs][:1] == [user.id]
    assert restored.active_window.refs[0].temporary is True


def test_compression_index_round_trips_segment_source_refs() -> None:
    index = CompressionIndex()
    index.record("seg_1", ["msg_1", "msg_2"])

    restored = compression_index_from_dict(compression_index_to_dict(index))

    assert restored.source_refs("seg_1") == ["msg_1", "msg_2"]


def test_session_snapshot_round_trips_full_runtime_state() -> None:
    session = SessionState(id="session_1")
    session.new_turn("hello")
    context_state = ContextState(working_state={"task_goal": "Recover session."})
    messages = MessageRuntime()
    messages.append_user("hello")
    index = CompressionIndex()
    index.record("seg_1", ["msg_1"])
    event_log = EventLog()
    event_log.record(
        TurnStartedEvent(
            session_id="session_1",
            turn_id="turn_1",
            user_input="hello",
        ),
    )
    snapshot = SessionSnapshot(
        session_state=session,
        context_state=context_state,
        message_runtime=messages,
        compression_index=index,
        next_segment_number=2,
        event_records=tuple(event_log.records),
    )

    restored = session_snapshot_from_dict(session_snapshot_to_dict(snapshot))

    assert restored.session_state.id == "session_1"
    assert restored.session_state.new_turn("next").id == "turn_2"
    assert restored.context_state.working_state["task_goal"] == "Recover session."
    assert restored.message_runtime.store.get("msg_1").content == "hello"
    assert restored.compression_index.source_refs("seg_1") == ["msg_1"]
    assert restored.next_segment_number == 2
    assert isinstance(restored.event_records[0], EventRecord)
