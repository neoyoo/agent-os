import inspect

import pytest

from agentos.context import ContextSnapshotRenderer, ContextState
from agentos.context.projection import project_context_state
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider
from agentos.runtime import ProviderRequestBuilder, QueryLoop
from agentos.runtime.provider_attempt import ensure_provider_response_usable
from agentos.tokens import HeuristicTokenCounter
from tests._context_protocol_fixtures import default_context_renderer


class SnapshotOnlyContext:
    """测试用 context boundary，只暴露 snapshot。"""

    def snapshot(self) -> ContextState:
        """返回可渲染的 context snapshot。"""

        return ContextState(memory_context=["snapshot used"])


class SnapshotProjectionProvider:
    def projections(self):  # type: ignore[no-untyped-def]
        return project_context_state(SnapshotOnlyContext().snapshot())


def test_provider_request_builder_accepts_context_runtime_boundary() -> None:
    messages = MessageRuntime()
    messages.append_user("hello")
    builder = ProviderRequestBuilder(
        context_renderer=default_context_renderer(),
        message_runtime=messages,
        tools=[],
        snapshot_renderer=ContextSnapshotRenderer(HeuristicTokenCounter()),
        context_projections=SnapshotProjectionProvider(),
    )

    request = builder.build().request

    assert "snapshot used" not in request.system
    assert "snapshot used" not in request.messages[0].content[0].text  # type: ignore[union-attr]
    assert request.messages[1].content[0].text == "hello"  # type: ignore[union-attr]


def test_query_loop_binds_request_builder_without_reading_context_state_directly() -> None:
    messages = MessageRuntime()
    builder = ProviderRequestBuilder(
        context_renderer=default_context_renderer(),
        message_runtime=messages,
        tools=[],
    )
    QueryLoop(
        context_runtime=SnapshotOnlyContext(),
        message_runtime=messages,
        request_builder=builder,
        provider=FakeProvider(["ok"]),
    )

    request = builder.build().request

    assert "snapshot used" not in request.system


def test_query_loop_rejects_rebinding_partial_builder_to_another_context() -> None:
    messages = MessageRuntime()
    builder = ProviderRequestBuilder(
        context_renderer=default_context_renderer(),
        message_runtime=messages,
        tools=[],
    )
    QueryLoop(
        context_runtime=SnapshotOnlyContext(),
        message_runtime=messages,
        request_builder=builder,
        provider=FakeProvider(["first"]),
    )

    with pytest.raises(RuntimeError, match="already bound to another context source"):
        QueryLoop(
            context_runtime=SnapshotOnlyContext(),
            message_runtime=messages,
            request_builder=builder,
            provider=FakeProvider(["second"]),
        )


def test_query_loop_uses_provider_response_stop_reason_field_directly() -> None:
    source = inspect.getsource(ensure_provider_response_usable)

    assert 'getattr(response, "stop_reason"' not in source
