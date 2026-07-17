import asyncio
import inspect

import pytest

from agentos import Agent, AgentBuilder
from agentos.providers import ImagePart, TextPart
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime import (
    AgentStream,
    EventBus,
    LocalContinuationInput,
    ProviderRequestBuilder,
    RunOptions,
    TurnStartedEvent,
    UserTurnInput,
    iter_jsonl,
    iter_sse,
)
from agentos.runtime.errors import AgentBusyError, ContinuationUnavailableError
from agentos.runtime.continuation import ContinuationNotice
from tests._context_protocol_fixtures import default_context_renderer


def test_agent_stream_constructor_is_owned_by_the_runtime() -> None:
    signature = str(inspect.signature(AgentStream))

    assert signature == "() -> 'None'"
    with pytest.raises(TypeError, match="created by the agent runtime"):
        AgentStream()


def build_agent(provider: object) -> Agent:
    context = ContextRuntime()
    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": context,
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=default_context_renderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": provider,
        },
    )


def build_agent_with_attachments(provider: object) -> Agent:
    return AgentBuilder().provider(provider).build()  # type: ignore[arg-type]


class StaticNoticeProvider:
    def __init__(self, notices: tuple[ContinuationNotice, ...]) -> None:
        self.notices = notices
        self.calls = 0

    def consume_notices(self) -> tuple[ContinuationNotice, ...]:
        self.calls += 1
        notices = self.notices
        self.notices = ()
        return notices


def build_agent_with_context(
    provider: object,
    context: ContextRuntime,
    notice_provider: StaticNoticeProvider,
    event_bus: EventBus | None = None,
) -> Agent:
    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": context,
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=default_context_renderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": provider,
            "turn_notice_provider": notice_provider,
            "event_bus": event_bus,
        },
    )


def test_agent_run_returns_result_without_extra_objects() -> None:
    async def run() -> None:
        agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))

        result = await agent.run("hello")

        assert result.content == "ok"

    asyncio.run(run())


def test_agent_run_accepts_uploaded_attachments() -> None:
    async def run() -> None:
        provider = FakeProvider([ProviderResponse(content="ok")])
        agent = build_agent_with_attachments(provider)
        artifact = agent.artifacts.upload(
            data=b"image-bytes",
            filename="diagram.png",
            media_type="image/png",
        )

        result = await agent.run(
            UserTurnInput("inspect image", artifact_handles=(artifact.id,)),
        )

        assert result.content == "ok"
        assert provider.requests[0].messages[1].content == (TextPart("inspect image"),)
        mount = provider.requests[0].messages[2]
        assert mount.kind == "context_mount"
        assert isinstance(mount.content[1], ImagePart)
        assert mount.content[1].payload.handle == artifact.id
        assert mount.content[1].payload.data == b"image-bytes"

    asyncio.run(run())


def test_agent_stream_accepts_per_turn_thinking_options() -> None:
    async def run() -> None:
        agent = build_agent(
            FakeProvider(
                [
                    ProviderResponse(
                        content="answer",
                        thinking_content="think",
                    ),
                ],
            ),
        )

        stream = await agent.run(
            "hello",
            stream=True,
            options=RunOptions(thinking=True, show_thinking=True),
        )
        async with stream:
            events = [event async for event in stream]

        assert "AssistantThinkingDelta" in [type(event).__name__ for event in events]

    asyncio.run(run())


def test_iter_sse_projects_existing_stream_without_starting_another_run() -> None:
    async def run() -> None:
        provider = FakeProvider([ProviderResponse(content="ok")])
        agent = build_agent(provider)

        stream = await agent.run("hello", stream=True)
        async with stream:
            chunks = [chunk async for chunk in iter_sse(stream)]

        assert any(chunk.startswith("event: content_delta") for chunk in chunks)
        assert chunks[-1].startswith("event: done")
        assert len(provider.requests) == 1

    asyncio.run(run())


def test_iter_jsonl_projects_existing_stream_without_starting_another_run() -> None:
    async def run() -> None:
        provider = FakeProvider([ProviderResponse(content="ok")])
        agent = build_agent(provider)

        stream = await agent.run("hello", stream=True)
        async with stream:
            chunks = [chunk async for chunk in iter_jsonl(stream)]

        assert any('"type":"content_delta"' in chunk for chunk in chunks)
        assert all(chunk.endswith("\n") for chunk in chunks)
        assert len(provider.requests) == 1

    asyncio.run(run())


def test_agent_rejects_unknown_query_loop_kwargs() -> None:
    with pytest.raises(ValueError, match="unknown query_loop_kwargs.*bad_key"):
        Agent(query_loop_kwargs={"provider": FakeProvider([]), "bad_key": object()})


def test_agent_interrupt_only_targets_current_created_stream() -> None:
    async def run() -> None:
        agent = build_agent(FakeProvider([ProviderResponse(content="ok")]))
        stream = await agent.run("cancelled", stream=True)

        assert agent.interrupt() is True
        assert [event async for event in stream] == []
        assert agent.interrupt() is False

        result = await agent.run("replacement")
        assert result.content == "ok"

    asyncio.run(run())


def test_agent_continuation_injects_notice_without_user_message() -> None:
    async def run() -> None:
        provider = FakeProvider([ProviderResponse(content="checked")])
        context = ContextRuntime()
        notice_provider = StaticNoticeProvider(
            (ContinuationNotice("task_completed", "task_1", "check_agent_tasks"),)
        )
        agent = build_agent_with_context(provider, context, notice_provider)

        result = await agent.run(LocalContinuationInput())

        assert result.content == "checked"
        assert [message.kind for message in provider.requests[0].messages] == [
            "context_snapshot",
            "continuation_data",
        ]
        snapshot_text = provider.requests[0].messages[0].content[0].text  # type: ignore[union-attr]
        continuation_text = provider.requests[0].messages[1].content[0].text  # type: ignore[union-attr]
        assert "task_1" not in snapshot_text
        assert 'kind="task_completed"' in continuation_text
        assert 'subject-id="task_1"' in continuation_text
        assert 'action="check_agent_tasks"' in continuation_text
        assert "# Runtime Notice" not in provider.requests[0].system
        assert "task_1" not in provider.requests[0].system
        assert notice_provider.calls == 1

    asyncio.run(run())


def test_agent_continuation_without_notices_raises_stable_error() -> None:
    async def run() -> None:
        provider = FakeProvider([ProviderResponse(content="should not run")])
        context = ContextRuntime()
        notice_provider = StaticNoticeProvider(())
        event_bus = EventBus()
        agent = build_agent_with_context(provider, context, notice_provider, event_bus)

        with pytest.raises(ContinuationUnavailableError):
            await agent.run(LocalContinuationInput())

        assert provider.requests == []
        assert [type(event).__name__ for event in event_bus.events] == [
            "TurnFailedEvent",
        ]
        assert notice_provider.calls == 1

    asyncio.run(run())


def test_agent_continuation_turn_started_event_is_marked_continuation() -> None:
    async def run() -> None:
        provider = FakeProvider([ProviderResponse(content="checked")])
        context = ContextRuntime()
        notice_provider = StaticNoticeProvider(
            (ContinuationNotice("task_completed", "task_1", "check_agent_tasks"),)
        )
        event_bus = EventBus()
        agent = build_agent_with_context(provider, context, notice_provider, event_bus)

        await agent.run(LocalContinuationInput())

        started = [
            event for event in event_bus.events if isinstance(event, TurnStartedEvent)
        ]
        assert len(started) == 1
        assert started[0].user_input == ""
        assert started[0].is_continuation is True

    asyncio.run(run())


def test_agent_continuation_clears_notice_when_stream_closes_before_request() -> None:
    async def run() -> None:
        provider = FakeProvider(
            [
                ProviderResponse(content="continuation response"),
                ProviderResponse(content="user answer"),
            ],
        )
        context = ContextRuntime()
        notice_provider = StaticNoticeProvider(
            (ContinuationNotice("task_completed", "task_1", "check_agent_tasks"),)
        )
        agent = build_agent_with_context(provider, context, notice_provider)

        stream = await agent.run(LocalContinuationInput(), stream=True)
        first_event = await anext(stream)
        await stream.aclose()

        assert type(first_event).__name__ == "TurnStreamStarted"
        assert notice_provider.calls == 1
        assert agent.interrupt() is False

        result = await agent.run("hello")

        assert result.content == "user answer"
        assert "# Runtime Notice" not in provider.requests[1].system
        assert all(
            message.kind != "continuation_data"
            for message in provider.requests[1].messages
        )

    asyncio.run(run())


def test_concurrent_run_is_busy_until_first_stream_closes() -> None:
    async def run() -> None:
        agent = build_agent(FakeProvider([ProviderResponse(content="replacement")]))
        first = await agent.run("first", stream=True)

        with pytest.raises(AgentBusyError):
            await agent.run("second")

        await first.aclose()
        replacement = await agent.run("third")
        assert replacement.content == "replacement"

    asyncio.run(run())
