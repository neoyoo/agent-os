from __future__ import annotations

import ast
import asyncio
from collections.abc import AsyncIterator
from dataclasses import fields
from pathlib import Path

import pytest

from agentos.artifacts import ArtifactRef, ArtifactRuntime, InMemoryArtifactStore
from agentos.artifacts.projection import ArtifactMountProjectionProvider
from agentos.context import ContextProtocolError, ContextSnapshotRenderer
from agentos.messages import (
    ConversationReadModel,
    MessageRuntime,
    MessageStore,
    StoredMessage,
    ToolCall,
)
from agentos.providers import (
    ProviderInputItem,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamOptions,
    ProviderToolCall,
)
from agentos.runtime import ProviderRequestBuilder, TurnStreamCompleted
from agentos.runtime.provider_attempt import ProviderAttemptRunner
from agentos.runtime.provider_request_builder import (
    ProviderRequestBuild,
    ProviderRequestReceipt,
)
from agentos.tokens import HeuristicTokenCounter
from tests._context_protocol_fixtures import default_context_renderer
from tests.runtime._query_loop_contract_fixtures import make_recording_agent


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _EmptyProjections:
    def projections(self) -> tuple[object, ...]:
        return ()


def test_turn_projection_order_and_temporary_receipt_are_rebuilt() -> None:
    async def scenario() -> None:
        messages = MessageRuntime()
        recalled = StoredMessage("msg_recalled", "user", "old evidence")
        messages.hydrate_messages([recalled])
        messages.active_window.prepend_temporary((recalled.id,))
        messages.append_assistant(
            "",
            [
                ToolCall(
                    "call_1",
                    "load_attachment",
                    {"handle": "art_12345678-1234-4234-9234-123456789abc"},
                )
            ],
        )
        messages.append_tool_result("call_1", "attachment loaded")
        artifacts = ArtifactRuntime(
            session_id="session_1",
            store=InMemoryArtifactStore(),
        )
        artifact = await artifacts.upload(
            data=b"image-bytes",
            filename="drawing.png",
            media_type="image/png",
        )
        await artifacts.load_attachment(artifact.id)
        await artifacts.prepare_projection_cache()
        builder = ProviderRequestBuilder(
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            snapshot_renderer=ContextSnapshotRenderer(HeuristicTokenCounter()),
            context_projections=_EmptyProjections(),
            input_projections=(ArtifactMountProjectionProvider(artifacts),),
        )

        first = builder.build()
        second = builder.build()

        expected_kinds = [
            "context_snapshot",
            "recalled_message",
            "business_message",
            "tool_result",
            "context_mount",
        ]
        assert [item.kind for item in first.request.messages] == expected_kinds
        assert first.request.messages[2].tool_calls[0].id == "call_1"
        assert first.request.messages[3].tool_call_id == "call_1"
        assert first.receipt.temporary_message_ids == (recalled.id,)
        assert second.receipt.temporary_message_ids == (recalled.id,)
        assert first.request is not second.request
        assert all(item.kind != "model_task" for item in second.request.messages)
        assert messages.has_temporary_recalled()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure_stage",
    ["before_hook", "after_hook", "validation"],
)
def test_attempt_failure_before_consumption_preserves_receipt(
    failure_stage: str,
) -> None:
    async def scenario() -> None:
        consumed: list[tuple[str, ...]] = []

        async def stream(
            _request: ProviderRequest,
            _options: ProviderStreamOptions | None,
        ) -> AsyncIterator[ProviderStreamEvent]:
            yield ProviderStreamCompleted("req-1", ProviderResponse(content="done"))

        def before_hook(request: ProviderRequest) -> ProviderRequest:
            if failure_stage == "before_hook":
                raise RuntimeError("before hook failed")
            return request

        def after_hook(
            _request: ProviderRequest,
            response: ProviderResponse,
        ) -> ProviderResponse:
            if failure_stage == "after_hook":
                raise RuntimeError("after hook failed")
            return response

        def validate(_response: ProviderResponse) -> None:
            if failure_stage == "validation":
                raise RuntimeError("validation failed")

        runner = ProviderAttemptRunner(
            request_factory=lambda: ProviderRequestBuild(
                ProviderRequest("system", ()),
                ProviderRequestReceipt(("msg_recalled",)),
            ),
            stream_provider=stream,
            before_call=before_hook,
            after_call=after_hook,
            ensure_usable=validate,
            consume_temporary=consumed.append,
            retry_policy=None,
            on_retry=lambda _attempt, _error: _unexpected_retry(),
        )

        with pytest.raises(RuntimeError, match="failed"):
            await _collect_provider_events(runner)
        assert consumed == []

    asyncio.run(scenario())


async def _unexpected_retry() -> None:
    raise AssertionError("retry is not expected")


async def _collect_provider_events(
    runner: ProviderAttemptRunner,
) -> list[ProviderStreamEvent]:
    return [event async for event in runner.run_stream(None)]


def test_stream_and_non_stream_agent_modes_have_final_result_parity() -> None:
    async def scenario() -> None:
        agent, recorder = make_recording_agent()

        result = await agent.run("complete")
        stream = await agent.run("stream", stream=True)
        events = [event async for event in stream]
        terminal = next(event for event in events if isinstance(event, TurnStreamCompleted))

        assert result.content == terminal.content == "answer"
        assert recorder.control_flow_runs == 2
        assert recorder.provider_calls == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("entry", ["put", "from_messages", "hydrate"])
def test_provider_input_cannot_enter_message_state(entry: str) -> None:
    internal = ProviderInputItem.model_task("task")
    store = MessageStore()
    runtime = MessageRuntime()

    with pytest.raises(TypeError, match="StoredMessage"):
        if entry == "put":
            store.put(internal)  # type: ignore[arg-type]
        elif entry == "from_messages":
            MessageStore.from_messages([internal], next_id=1)  # type: ignore[list-item]
        else:
            runtime.hydrate_messages([internal])  # type: ignore[list-item]
    assert store.all() == []
    assert runtime.store.all() == []


def test_provider_input_cannot_enter_projection_or_temporary_receipt() -> None:
    internal = ProviderInputItem.model_task("task")
    model = ConversationReadModel()
    renderer = ContextSnapshotRenderer(HeuristicTokenCounter())

    with pytest.raises(TypeError, match="domain event"):
        model.build(messages=(), events=(internal,))
    with pytest.raises(TypeError, match="StoredMessage"):
        model.build(messages=(internal,), events=())  # type: ignore[arg-type]
    with pytest.raises(ContextProtocolError, match="projection"):
        renderer.render((internal,))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="temporary message IDs"):
        ProviderRequestReceipt((internal,))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="temporary message IDs"):
        ProviderRequestReceipt("msg_recalled")  # type: ignore[arg-type]


def test_stored_message_rejects_provider_transcript_fields() -> None:
    internal = ProviderInputItem.model_task("task")

    with pytest.raises(TypeError, match="content must be str"):
        StoredMessage("msg_1", "user", internal)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ToolCall"):
        StoredMessage(
            "msg_1",
            "assistant",
            "",
            tool_calls=(ProviderToolCall("call_1", "lookup"),),  # type: ignore[arg-type]
        )


def test_boundary_value_objects_exclude_adapter_specific_fields() -> None:
    assert [field.name for field in fields(ArtifactRef)] == [
        "artifact_id",
        "filename",
        "media_type",
    ]
    assert [field.name for field in fields(ProviderInputItem)] == [
        "role",
        "kind",
        "origin",
        "authority",
        "persistence",
        "visibility",
        "content",
        "tool_calls",
        "tool_call_id",
    ]
    assert [field.name for field in fields(StoredMessage)] == [
        "id",
        "role",
        "content",
        "artifact_refs",
        "tool_calls",
        "tool_call_id",
    ]


def test_architecture_has_one_model_task_producer_and_no_builder_adapter_imports() -> None:
    producers: list[str] = []
    artifact_definitions: list[str] = []
    for path in (PROJECT_ROOT / "src" / "agentos").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.ClassDef) and node.name == "ArtifactRef"
            for node in ast.walk(tree)
        ):
            artifact_definitions.append(path.relative_to(PROJECT_ROOT).as_posix())
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "model_task"
            for node in ast.walk(tree)
        ):
            producers.append(path.relative_to(PROJECT_ROOT).as_posix())

    builder_imports: set[str] = set()
    for relative_path in (
        "src/agentos/builder.py",
        "src/agentos/runtime/provider_request_builder.py",
    ):
        tree = ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                builder_imports.add(node.module or "")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                builder_imports.update(alias.name for alias in node.names)
    message_provider_imports: list[str] = []
    for path in (PROJECT_ROOT / "src" / "agentos" / "messages").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "agentos.providers",
            ):
                message_provider_imports.append(node.module or "")
            if isinstance(node, ast.Import):
                message_provider_imports.extend(
                    alias.name
                    for alias in node.names
                    if alias.name.startswith("agentos.providers")
                )

    assert producers == ["src/agentos/compression/llm_compressor.py"]
    assert artifact_definitions == ["src/agentos/artifacts/types.py"]
    assert message_provider_imports == []
    assert {
        "AnthropicProvider",
        "OpenAIProvider",
        "OpenAICompatibleProvider",
        "agentos.providers.anthropic",
        "agentos.providers.openai",
        "agentos.providers.openai_compatible",
    }.isdisjoint(builder_imports)
