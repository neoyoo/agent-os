import inspect

from agentos.context import ContextRenderer, ContextState
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, provider_message_to_dict
from agentos.runtime import ProviderRequestBuilder, QueryLoop


class SnapshotOnlyContext:
    """测试用 context boundary，只暴露 snapshot。"""

    def snapshot(self) -> ContextState:
        """返回可渲染的 context snapshot。"""

        return ContextState(memory_context=["snapshot used"])


def test_provider_request_builder_accepts_context_runtime_boundary() -> None:
    messages = MessageRuntime()
    messages.append_user("hello")
    builder = ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=messages,
        tools=[],
    )

    request = builder.build(SnapshotOnlyContext())

    assert "snapshot used" in request.system
    assert [provider_message_to_dict(message) for message in request.messages] == [
        {"role": "user", "content": "hello"},
    ]


def test_query_loop_build_request_does_not_read_context_state_directly() -> None:
    messages = MessageRuntime()
    loop = QueryLoop(
        context_runtime=SnapshotOnlyContext(),
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=[],
        ),
        provider=FakeProvider(["ok"]),
    )

    request = loop.build_request()

    assert "snapshot used" in request.system


def test_query_loop_uses_provider_response_stop_reason_field_directly() -> None:
    source = inspect.getsource(QueryLoop._ensure_provider_response_usable)

    assert 'getattr(response, "stop_reason"' not in source
