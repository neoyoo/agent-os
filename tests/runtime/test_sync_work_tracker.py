import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from threading import Event as ThreadEvent

import pytest

from agentos import Agent
from agentos._sync_work import (
    SyncWorkTracker,
    bind_sync_work_tracker,
    current_sync_work_tracker,
    run_sync,
)
from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import AgentBusyError, ProviderRequestBuilder, QueryLoop
from agentos.workspace import (
    LocalWorkspaceExecutionBackend,
    WorkspaceExecutionRequest,
    WorkspaceExecutionResult,
    WorkspaceHandle,
)
from tests._context_protocol_fixtures import default_context_renderer


async def _next_loop_checkpoint() -> None:
    reached = asyncio.Event()
    asyncio.get_running_loop().call_soon(reached.set)
    await reached.wait()


def test_event_source_binding_is_scoped_to_anext_and_aclose() -> None:
    async def scenario() -> None:
        tracker = SyncWorkTracker()
        observations: list[SyncWorkTracker | None] = []

        async def events() -> AsyncIterator[str]:
            observations.append(current_sync_work_tracker())
            try:
                yield "event"
            finally:
                observations.append(current_sync_work_tracker())

        source = bind_sync_work_tracker(events(), tracker)

        assert current_sync_work_tracker() is None
        assert await anext(source) == "event"
        assert current_sync_work_tracker() is None
        await source.aclose()
        assert current_sync_work_tracker() is None
        assert observations == [tracker, tracker]

    asyncio.run(scenario())


def test_repeated_wait_cancellation_still_converges_and_collects_worker_error() -> None:
    class WorkerError(RuntimeError):
        pass

    async def scenario() -> None:
        tracker = SyncWorkTracker()
        worker_started = ThreadEvent()
        release_worker = ThreadEvent()
        worker_error = WorkerError("worker failed after cancellation")
        run_cancellations: list[asyncio.CancelledError] = []
        unhandled: list[BaseException | None] = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(
            lambda _loop, context: unhandled.append(context.get("exception")),
        )

        def fail_after_release() -> None:
            worker_started.set()
            release_worker.wait()
            raise worker_error

        async def events() -> AsyncIterator[None]:
            try:
                await run_sync(fail_after_release)
            except asyncio.CancelledError as error:
                run_cancellations.append(error)
                raise
            yield None

        source = bind_sync_work_tracker(events(), tracker)
        consumer = asyncio.create_task(anext(source))
        try:
            assert await asyncio.to_thread(worker_started.wait, 5)
            consumer.cancel("run cancellation")
            with pytest.raises(asyncio.CancelledError) as run_cancelled:
                await consumer
            assert run_cancelled.value is run_cancellations[0]
            assert run_cancelled.value.args == ("run cancellation",)

            wait_started = asyncio.Event()
            wait_until_idle = tracker.wait_until_idle

            async def observed_wait_until_idle() -> None:
                wait_started.set()
                await wait_until_idle()

            waiter = asyncio.create_task(observed_wait_until_idle())
            await wait_started.wait()
            waiter.cancel("cleanup cancellation one")
            await _next_loop_checkpoint()
            assert not waiter.done()
            waiter.cancel("cleanup cancellation two")
            await _next_loop_checkpoint()
            assert not waiter.done()

            release_worker.set()
            with pytest.raises(asyncio.CancelledError) as cleanup_cancelled:
                await waiter
            assert cleanup_cancelled.value.args == ("cleanup cancellation one",)
            assert tracker.exceptions == (worker_error,)
            assert unhandled == []
        finally:
            release_worker.set()
            await source.aclose()
            loop.set_exception_handler(previous_handler)

    asyncio.run(scenario())


def test_workspace_backend_sync_work_converges_before_run_lease_release(
    tmp_path: Path,
) -> None:
    class BlockingWorkspaceBackend(LocalWorkspaceExecutionBackend):
        def __init__(self) -> None:
            super().__init__()
            self.started = ThreadEvent()
            self.release = ThreadEvent()
            self.finished = ThreadEvent()

        def run(self, request: WorkspaceExecutionRequest) -> WorkspaceExecutionResult:
            self.started.set()
            self.release.wait()
            self.finished.set()
            return WorkspaceExecutionResult(
                backend=type(self).__name__,
                workspace_id=request.workspace.workspace_id,
                workspace_scope=request.workspace.scope,
                command=request.command,
                capability=request.capability,
                cwd=request.workspace.root or ".",
                exit_code=0,
                stdout="discarded",
            )

    async def scenario() -> None:
        backend = BlockingWorkspaceBackend()
        request = WorkspaceExecutionRequest(
            workspace=WorkspaceHandle(
                workspace_id="workspace_1",
                scope="task",
                root=str(tmp_path),
            ),
            command=("workspace-command",),
            capability="process.exec",
        )

        async def execute_workspace(_arguments: dict[str, object]) -> str:
            result = await backend.async_run(request)
            return result.stdout

        context = ContextRuntime()
        messages = MessageRuntime()
        registry = ToolRegistry()
        registry.register(
            RegisteredTool(
                name="workspace_exec",
                description="Execute a workspace command.",
                parameters={"type": "object", "properties": {}},
                handler=execute_workspace,
            ),
        )
        router = ToolCallRouter(tool_registry=registry, context_runtime=context)
        agent = Agent(
            QueryLoop(
                context_runtime=context,
                message_runtime=messages,
                request_builder=ProviderRequestBuilder(
                    context_renderer=default_context_renderer(),
                    message_runtime=messages,
                    tools=router.tool_specs(),
                ),
                provider=FakeProvider(
                    [
                        ProviderResponse(
                            tool_calls=(
                                ProviderToolCall(
                                    "call_1",
                                    "workspace_exec",
                                    {},
                                ),
                            ),
                        ),
                        ProviderResponse(content="replacement"),
                    ],
                ),
                tool_call_router=router,
            ),
        )
        stream = await agent.run("hello", stream=True)
        tracker = stream._pending_sync_work
        assert tracker is not None
        cleanup_wait_started = asyncio.Event()
        cleanup_wait_returned = asyncio.Event()
        wait_until_idle = tracker.wait_until_idle

        async def observe_wait_until_idle() -> None:
            cleanup_wait_started.set()
            await wait_until_idle()
            cleanup_wait_returned.set()

        tracker.wait_until_idle = observe_wait_until_idle  # type: ignore[method-assign]

        async def consume() -> None:
            async for _event in stream:
                pass

        consumer = asyncio.create_task(consume())
        assert await asyncio.to_thread(backend.started.wait, 5)
        close_task = asyncio.create_task(stream.aclose())
        results: list[object] = []
        try:
            await cleanup_wait_started.wait()
            assert not cleanup_wait_returned.is_set()
            assert not close_task.done()
            with pytest.raises(AgentBusyError):
                await agent.run("replacement", stream=True)
        finally:
            backend.release.set()
            results = await asyncio.gather(
                close_task,
                consumer,
                return_exceptions=True,
            )

        assert results[0] is None
        assert isinstance(results[1], asyncio.CancelledError)
        assert cleanup_wait_returned.is_set()
        assert backend.finished.is_set()
        replacement = await agent.run("replacement")
        assert replacement.content == "replacement"

    asyncio.run(scenario())
