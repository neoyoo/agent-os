import asyncio
from contextlib import aclosing

import pytest

from agentos import Agent
from agentos._json_values import FrozenJsonObject
from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolCallRouter,
    ToolInvocation,
    ToolRegistry,
)
from agentos.context import ContextRuntime
from agentos.durable import SQLiteDurableStore
from agentos.messages import MessageRuntime, ToolCall
from agentos.providers import ProviderRequest, ProviderResponse, ProviderToolCall
from agentos.runtime import ProviderRequestBuilder, QueryLoop
from agentos.runtime._execution_control import (
    PendingToolsCheckpointRequest,
    RunningCheckpointRequest,
)
from agentos.runtime.checkpoint import RuntimeCheckpointSource
from agentos.runtime.execution import RestoreAcceptedTurn, RunExecutionCursor
from agentos.runtime.errors import PayloadProtectionError
from agentos.runtime.payloads import (
    PayloadProtectionContext,
    ProtectedPayloadRef,
)
from agentos.runtime.run import RunOptions
from agentos.runtime.run_runtime import RunRuntime
from agentos.runtime.run_state import RunStatus
from agentos.runtime.session import SessionState
from agentos.runtime.tool_payloads import ToolPayloadRuntime
from agentos.security import FernetPayloadProtector
from tests._context_protocol_fixtures import default_context_renderer
from tests.durable._fixtures import NOW, database_path


class BarrierProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                tool_calls=(ProviderToolCall("call_1", "lookup", {"query": "x"}),),
            )
        return ProviderResponse("done")


class RecoveryProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = responses
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        response = self.responses[self.calls]
        self.calls += 1
        return response


class FailSecondPayloadProtector:
    def __init__(self) -> None:
        self._delegate = FernetPayloadProtector(
            FernetPayloadProtector.generate_key(),
        )
        self.protection_calls = 0
        self.run_id: str | None = None

    def protect(
        self,
        payload: FrozenJsonObject,
        *,
        context: PayloadProtectionContext,
    ) -> ProtectedPayloadRef:
        self.protection_calls += 1
        self.run_id = context.run_id
        if self.protection_calls == 2:
            raise RuntimeError("injected second protection failure")
        return self._delegate.protect(payload, context=context)

    def unprotect(
        self,
        reference: ProtectedPayloadRef,
        *,
        context: PayloadProtectionContext,
    ) -> FrozenJsonObject:
        return self._delegate.unprotect(reference, context=context)


def _recovery_loop(
    responses: list[ProviderResponse],
) -> tuple[QueryLoop, RecoveryProvider, ToolPayloadRuntime, list[dict[str, object]]]:
    session = SessionState("session_1")
    messages = MessageRuntime()
    context = ContextRuntime(session_id=session.id)
    payloads = ToolPayloadRuntime(
        FernetPayloadProtector(FernetPayloadProtector.generate_key()),
        PayloadProtectionContext(None, session.id),
    )
    observed_arguments: list[dict[str, object]] = []

    async def lookup(invocation: ToolInvocation) -> str:
        observed_arguments.append(dict(invocation.arguments))
        return "found"

    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            "lookup",
            "Lookup.",
            {"type": "object"},
            lookup,
            SideEffectPolicy.PURE,
        ),
    )
    router = ToolCallRouter(tool_registry=registry, context_runtime=context)
    provider = RecoveryProvider(responses)
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
        ),
        provider=provider,
        tool_call_router=router,
        session_state=session,
        tool_payload_runtime=payloads,
    )
    return loop, provider, payloads, observed_arguments


def test_running_checkpoints_are_execution_barriers(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        store = await SQLiteDurableStore.open(
            database_path(tmp_path),
            clock=lambda: NOW,
        )
        session = SessionState("session_1")
        messages = MessageRuntime()
        context = ContextRuntime(session_id=session.id)
        protector = FernetPayloadProtector(FernetPayloadProtector.generate_key())
        payloads = ToolPayloadRuntime(
            protector,
            PayloadProtectionContext(None, session.id),
        )
        source = RuntimeCheckpointSource(session, messages, context, payloads)
        await store.initialize_session(session)
        reached = {
            stage: asyncio.Event()
            for stage in ("before_provider", "pending_tools", "after_tools")
        }
        released = {
            stage: asyncio.Event()
            for stage in ("before_provider", "pending_tools", "after_tools")
        }
        original_commit = store.commit_running

        async def blocked_commit(**kwargs):  # type: ignore[no-untyped-def]
            cursor = kwargs["checkpoint"].execution_cursor
            assert cursor is not None
            reached[cursor.stage].set()
            await released[cursor.stage].wait()
            return await original_commit(**kwargs)

        monkeypatch.setattr(store, "commit_running", blocked_commit)
        tool_calls = 0

        async def lookup(_invocation: ToolInvocation) -> str:
            nonlocal tool_calls
            tool_calls += 1
            return "found"

        registry = ToolRegistry()
        registry.register(
            RegisteredTool(
                "lookup",
                "Lookup.",
                {"type": "object"},
                lookup,
                SideEffectPolicy.PURE,
            ),
        )
        router = ToolCallRouter(tool_registry=registry, context_runtime=context)
        provider = BarrierProvider()
        loop = QueryLoop(
            context_runtime=context,
            message_runtime=messages,
            request_builder=ProviderRequestBuilder(
                context_renderer=default_context_renderer(),
                message_runtime=messages,
                tools=router.tool_specs(),
            ),
            provider=provider,
            tool_call_router=router,
            session_state=session,
            run_runtime=RunRuntime(session_id=session.id, store=store),
            checkpoint_source=source,
            checkpoint_store=store,
            tool_payload_runtime=payloads,
        )
        stream = await Agent(loop).run("hello", stream=True)

        async def consume() -> None:
            async with stream:
                async for _event in stream:
                    pass

        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(reached["before_provider"].wait(), timeout=1)
        assert provider.calls == 0
        released["before_provider"].set()

        await asyncio.wait_for(reached["pending_tools"].wait(), timeout=1)
        assert provider.calls == 1
        assert tool_calls == 0
        released["pending_tools"].set()

        await asyncio.wait_for(reached["after_tools"].wait(), timeout=1)
        assert provider.calls == 1
        assert tool_calls == 1
        released["after_tools"].set()

        await consumer
        assert provider.calls == 2
        await store.close()

    asyncio.run(scenario())


def test_partial_payload_protection_commits_failed_terminal(tmp_path) -> None:
    async def scenario() -> None:
        async with await SQLiteDurableStore.open(
            database_path(tmp_path),
            clock=lambda: NOW,
        ) as store:
            session = SessionState("session_partial_payload")
            messages = MessageRuntime()
            context = ContextRuntime(session_id=session.id)
            protector = FailSecondPayloadProtector()
            payloads = ToolPayloadRuntime(
                protector,
                PayloadProtectionContext(None, session.id),
            )
            source = RuntimeCheckpointSource(session, messages, context, payloads)
            await store.initialize_session(session)
            registry = ToolRegistry()
            registry.register(
                RegisteredTool(
                    "lookup",
                    "Lookup.",
                    {"type": "object"},
                    lambda _invocation: "found",
                    SideEffectPolicy.PURE,
                ),
            )
            router = ToolCallRouter(
                tool_registry=registry,
                context_runtime=context,
            )
            provider = RecoveryProvider([
                ProviderResponse(tool_calls=(
                    ProviderToolCall("call_1", "lookup", {"query": "first"}),
                    ProviderToolCall("call_2", "lookup", {"query": "second"}),
                )),
            ])
            loop = QueryLoop(
                context_runtime=context,
                message_runtime=messages,
                request_builder=ProviderRequestBuilder(
                    context_renderer=default_context_renderer(),
                    message_runtime=messages,
                    tools=router.tool_specs(),
                ),
                provider=provider,
                tool_call_router=router,
                session_state=session,
                run_runtime=RunRuntime(session_id=session.id, store=store),
                checkpoint_source=source,
                checkpoint_store=store,
                tool_payload_runtime=payloads,
            )

            with pytest.raises(PayloadProtectionError):
                await Agent(loop).run("hello")

            assert protector.run_id is not None
            run = await store.get(session_id=session.id, run_id=protector.run_id)
            checkpoint = await store.load_checkpoint(session.id)
            assert run is not None
            assert run.status is RunStatus.FAILED
            assert checkpoint is not None
            assert [message.role for message in checkpoint.messages] == ["user"]

    asyncio.run(scenario())


def test_before_provider_recovery_calls_provider_at_the_saved_index() -> None:
    async def scenario() -> None:
        loop, provider, _payloads, observed = _recovery_loop(
            [
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_next", "lookup", {"query": "next"}),
                    ),
                ),
            ],
        )
        assert loop.session_state is not None
        turn = loop.session_state.new_turn("hello", turn_id="turn_1")
        cursor = RunExecutionCursor("turn_1", "before_provider", 4)

        checkpoint = None
        async with aclosing(
            loop._run_provider_tool_events(
                "run_1",
                turn,
                RunOptions(),
                RestoreAcceptedTurn(cursor),
            ),
        ) as events:
            async for event in events:
                if isinstance(event, PendingToolsCheckpointRequest):
                    checkpoint = event
                    break

        assert checkpoint is not None
        assert checkpoint.plan.provider_call_index == 4
        assert provider.calls == 1
        assert observed == []

    asyncio.run(scenario())


def test_pending_tools_recovery_replays_the_saved_batch_before_next_provider() -> None:
    async def scenario() -> None:
        loop, provider, payloads, observed = _recovery_loop(
            [ProviderResponse("done")],
        )
        assert loop.session_state is not None
        turn = loop.session_state.new_turn("hello", turn_id="turn_1")
        call = ProviderToolCall("call_1", "lookup", {"query": "saved"})
        assistant = loop.message_runtime.append_assistant(
            "",
            [ToolCall(call.id, call.name, call.arguments)],
        )
        plan = payloads.build_plan(
            run_id="run_1",
            turn_id=turn.id,
            provider_call_index=4,
            assistant_message_id=assistant.id,
            calls=(call,),
        )
        cursor = payloads.pending_cursor(plan)

        events = [
            event
            async for event in loop._run_provider_tool_events(
                "run_1",
                turn,
                RunOptions(),
                RestoreAcceptedTurn(cursor),
            )
        ]

        assert observed == [{"query": "saved"}]
        assert provider.calls == 1
        after_tools = next(
            event.cursor
            for event in events
            if isinstance(event, RunningCheckpointRequest)
        )
        assert after_tools.stage == "after_tools"
        assert after_tools.provider_call_index == 4

    asyncio.run(scenario())


def test_after_tools_recovery_skips_tool_batch_and_advances_provider_index() -> None:
    async def scenario() -> None:
        loop, provider, _payloads, observed = _recovery_loop(
            [
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_next", "lookup", {"query": "next"}),
                    ),
                ),
            ],
        )
        assert loop.session_state is not None
        turn = loop.session_state.new_turn("hello", turn_id="turn_1")
        assistant = loop.message_runtime.append_assistant(
            "",
            [ToolCall("call_old", "lookup", {"query": "old"})],
        )
        loop.message_runtime.append_tool_result("call_old", "found")
        cursor = RunExecutionCursor(
            turn.id,
            "after_tools",
            4,
            assistant.id,
        )

        checkpoint = None
        async with aclosing(
            loop._run_provider_tool_events(
                "run_1",
                turn,
                RunOptions(),
                RestoreAcceptedTurn(cursor),
            ),
        ) as events:
            async for event in events:
                if isinstance(event, PendingToolsCheckpointRequest):
                    checkpoint = event
                    break

        assert checkpoint is not None
        assert checkpoint.plan.provider_call_index == 5
        assert provider.calls == 1
        assert observed == []

    asyncio.run(scenario())


def test_after_tools_recovery_executes_same_arguments_as_a_new_operation() -> None:
    async def scenario() -> None:
        repeated = ProviderToolCall(
            "call_repeated",
            "lookup",
            {"query": "same"},
        )
        loop, provider, _payloads, observed = _recovery_loop(
            [
                ProviderResponse(tool_calls=(repeated,)),
                ProviderResponse("done"),
            ],
        )
        assert loop.session_state is not None
        turn = loop.session_state.new_turn("hello", turn_id="turn_1")
        assistant = loop.message_runtime.append_assistant(
            "",
            [ToolCall("call_original", "lookup", {"query": "same"})],
        )
        loop.message_runtime.append_tool_result("call_original", "found")
        cursor = RunExecutionCursor(
            turn.id,
            "after_tools",
            0,
            assistant.id,
        )

        _ = [
            event
            async for event in loop._run_provider_tool_events(
                "run_1",
                turn,
                RunOptions(),
                RestoreAcceptedTurn(cursor),
            )
        ]

        assert provider.calls == 2
        assert observed == [{"query": "same"}]

    asyncio.run(scenario())


def test_recovery_preserves_consumed_tool_iteration_budget() -> None:
    async def scenario() -> None:
        loop, provider, _payloads, observed = _recovery_loop(
            [
                ProviderResponse(
                    tool_calls=(
                        ProviderToolCall("call_excess", "lookup", {"query": "extra"}),
                    ),
                ),
            ],
        )
        loop.max_tool_iterations = 4
        assert loop.session_state is not None
        turn = loop.session_state.new_turn("hello", turn_id="turn_1")
        assistant = None
        for index in range(4):
            call_id = f"call_{index}"
            assistant = loop.message_runtime.append_assistant(
                "",
                [ToolCall(call_id, "lookup", {"query": str(index)})],
            )
            loop.message_runtime.append_tool_result(call_id, "found")
        assert assistant is not None
        cursor = RunExecutionCursor(
            turn.id,
            "after_tools",
            3,
            assistant.id,
        )

        with pytest.raises(
            RuntimeError,
            match="^provider tool-call loop exceeded max iterations$",
        ):
            _ = [
                event
                async for event in loop._run_provider_tool_events(
                    "run_1",
                    turn,
                    RunOptions(),
                    RestoreAcceptedTurn(cursor),
                )
            ]

        assert provider.calls == 1
        assert observed == []

    asyncio.run(scenario())
