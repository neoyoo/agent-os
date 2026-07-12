from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse
from agentos.runtime import Agent, ProviderRequestBuilder
from tests._context_protocol_fixtures import default_context_renderer


def build_agent_with_response(content: str) -> Agent:
    """构造返回固定内容的测试 Agent。"""

    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": ContextRuntime(),
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=default_context_renderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": FakeProvider([ProviderResponse(content=content)]),
        },
    )
