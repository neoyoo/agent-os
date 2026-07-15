from __future__ import annotations

import asyncio

import pytest

from agentos import AgentBuilder
from agentos.compression import CompressionRuntime
from agentos.context import CompressedSegment, ContextRuntime
from agentos.events import EventBus, RecallContextFailedEvent
from agentos.persistence import (
    InMemoryDurableSessionStore,
    InMemoryHotSessionStore,
)
from agentos.messages import ActiveWindow, MessageRef, MessageRuntime, StoredMessage
from agentos.policies import BudgetPolicy
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.recall import (
    CompressedSegmentPackage,
    InMemoryRecallIndex,
    RecallContextError,
    RecallRuntime,
    SegmentRecallDocument,
    SegmentRepository,
)


class RecordingActiveWindow(ActiveWindow):
    def __init__(self) -> None:
        super().__init__()
        self.prepend_calls: list[tuple[str, ...]] = []

    def prepend_temporary(self, message_ids: tuple[str, ...]) -> None:
        self.prepend_calls.append(message_ids)
        super().prepend_temporary(message_ids)


class FailedEventRecorder:
    def __init__(self) -> None:
        self.events: list[RecallContextFailedEvent] = []

    def record(self, event: object) -> None:
        if isinstance(event, RecallContextFailedEvent):
            self.events.append(event)


def build_segment_repository() -> tuple[SegmentRepository, InMemoryDurableSessionStore]:
    durable_store = InMemoryDurableSessionStore()
    runtime = SegmentRepository(
        hot_store=InMemoryHotSessionStore(),
        durable_store=durable_store,
        recall_index=InMemoryRecallIndex(),
    )
    package = CompressedSegmentPackage(
        segment=CompressedSegment(
            id="seg_1",
            topic="读取 pyproject.toml 里的项目名",
            summary="工具返回 project.name = agent-os。",
        ),
        source_refs=("msg_1", "msg_2"),
        recall_document=SegmentRecallDocument(
            session_id="session_1",
            segment_id="seg_1",
            topic="读取 pyproject.toml 里的项目名",
            summary="工具返回 project.name = agent-os。",
            keywords=("pyproject.toml", "agent-os"),
        ),
    )
    runtime.record_compressed_segment(package)
    durable_store.append_message(
        "session_1",
        StoredMessage(id="msg_1", role="user", content="读取 pyproject.toml"),
    )
    durable_store.append_message(
        "session_1",
        StoredMessage(id="msg_2", role="assistant", content="项目名是 agent-os"),
    )
    return runtime, durable_store


def test_recall_context_query_hydrates_then_prepends_temporary_messages() -> None:
    segment_repository, _ = build_segment_repository()
    messages = MessageRuntime(active_window=RecordingActiveWindow())
    recall = RecallRuntime(
        message_runtime=messages,
        segment_repository=segment_repository,
        session_id="session_1",
    )

    recalled = recall.recall_context(query="pyproject 项目名", limit=1)

    assert isinstance(recalled, tuple)
    assert tuple(message.id for message in recalled) == ("msg_1", "msg_2")
    assert messages.store.get("msg_1").content == "读取 pyproject.toml"
    assert messages.active_window.prepend_calls == [("msg_1", "msg_2")]  # type: ignore[attr-defined]
    assert messages.active_window.snapshot_refs() == (
        MessageRef("msg_1", temporary=True),
        MessageRef("msg_2", temporary=True),
    )


def test_recall_context_handle_uses_segment_repository() -> None:
    segment_repository, _ = build_segment_repository()
    messages = MessageRuntime(active_window=RecordingActiveWindow())
    recall = RecallRuntime(
        message_runtime=messages,
        segment_repository=segment_repository,
        session_id="session_1",
    )

    recalled = recall.recall_context(handle="seg_1")

    assert tuple(message.id for message in recalled) == ("msg_1", "msg_2")
    assert messages.store.get("msg_2").content == "项目名是 agent-os"


def test_recall_context_query_deduplicates_repeated_results() -> None:
    segment_repository, _ = build_segment_repository()
    messages = MessageRuntime(active_window=RecordingActiveWindow())
    recall = RecallRuntime(
        message_runtime=messages,
        segment_repository=segment_repository,
        session_id="session_1",
    )

    recall.recall_context(query="pyproject 项目名", limit=1)
    recall.recall_context(query="pyproject 项目名", limit=1)

    assert messages.active_window.snapshot_refs() == (
        MessageRef("msg_1", temporary=True),
        MessageRef("msg_2", temporary=True),
    )


def test_recall_context_query_failure_does_not_modify_window() -> None:
    class FailingSegmentRepository:
        def recall_by_query(self, session_id: str, query: str, limit: int):
            raise RuntimeError("query failed")

    messages = MessageRuntime()
    current = messages.append_user("Current question")
    before = messages.active_window.snapshot_refs()
    recorder = FailedEventRecorder()
    recall = RecallRuntime(
        message_runtime=messages,
        segment_repository=FailingSegmentRepository(),  # type: ignore[arg-type]
        event_bus=EventBus(subscribers=[recorder]),
        session_id="session_1",
    )

    with pytest.raises(RecallContextError, match="query recall failed"):
        recall.recall_context(query="pyproject")

    assert before == (MessageRef(current.id),)
    assert messages.active_window.snapshot_refs() == before
    assert [(event.handle, event.error) for event in recorder.events] == [
        ("query:pyproject", "query recall failed"),
    ]


def test_recall_context_hydrate_failure_does_not_modify_window() -> None:
    class ConflictingSegmentRepository:
        def recall_by_query(
            self,
            session_id: str,
            query: str,
            limit: int,
        ) -> tuple[StoredMessage, ...]:
            return (
                StoredMessage("msg_2", "user", "Hydrated before failure"),
                StoredMessage("msg_3", "assistant", "Conflicting value"),
            )

    messages = MessageRuntime()
    current = messages.append_user("Current question")
    messages.store.put(StoredMessage("msg_3", "assistant", "Stored value"))
    before = messages.active_window.snapshot_refs()
    recall = RecallRuntime(
        message_runtime=messages,
        segment_repository=ConflictingSegmentRepository(),  # type: ignore[arg-type]
        session_id="session_1",
    )

    with pytest.raises(ValueError, match="message id conflict: msg_3"):
        recall.recall_context(query="pyproject")

    assert messages.store.get("msg_2").content == "Hydrated before failure"
    assert before == (MessageRef(current.id),)
    assert messages.active_window.snapshot_refs() == before


def test_recall_context_rejects_handle_and_query_together() -> None:
    segment_repository, _ = build_segment_repository()
    recall = RecallRuntime(
        message_runtime=MessageRuntime(),
        segment_repository=segment_repository,
        session_id="session_1",
    )

    with pytest.raises(RecallContextError, match="either handle or query"):
        recall.recall_context("seg_1", query="pyproject")


def test_recall_context_query_requires_session_id() -> None:
    segment_repository, _ = build_segment_repository()
    recall = RecallRuntime(
        message_runtime=MessageRuntime(),
        segment_repository=segment_repository,
    )

    with pytest.raises(RecallContextError, match="session_id is required"):
        recall.recall_context(query="pyproject")


def test_query_loop_keeps_recalled_originals_and_stored_tool_result_distinct() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    compression = CompressionRuntime(
        context_runtime=context,
        message_runtime=messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )
    provider = FakeProvider(
        [
            "Captured first history.",
            ProviderResponse(
                tool_calls=(
                    ProviderToolCall(
                        id="call_recall",
                        name="recall_context",
                        arguments={"handle": "seg_1"},
                    ),
                ),
            ),
            "recalled done",
        ],
    )
    agent = (
        AgentBuilder()
        .provider(provider)
        .context_runtime(context)
        .message_runtime(messages)
        .compression_runtime(compression)
        .build()
    )

    asyncio.run(agent.run("First detail"))
    result = asyncio.run(agent.run("Current task"))

    assert result.content == "recalled done"
    projected = provider.requests[2].messages
    assert [item.kind for item in projected[1:]] == [
        "recalled_message",
        "recalled_message",
        "business_message",
        "business_message",
        "tool_result",
    ]
    assert projected[1].content[0].text == "First detail"  # type: ignore[union-attr]
    tool_item = projected[-1]
    assert tool_item.tool_call_id == "call_recall"
    assert "First detail" in tool_item.content[0].text  # type: ignore[union-attr]
    stored_tool_results = [
        message
        for message in messages.store.all()
        if message.role == "tool" and message.tool_call_id == "call_recall"
    ]
    assert len(stored_tool_results) == 1
    assert stored_tool_results[0].content == tool_item.content[0].text  # type: ignore[union-attr]
