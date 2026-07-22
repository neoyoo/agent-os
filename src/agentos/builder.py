from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import uuid4

from agentos._builder_local import assemble_local_state, assemble_provider_request_builder
from agentos._builder_state import RuntimeStateComponents
from agentos._builder_recall import assemble_recall_runtime
from agentos._builder_tools import assemble_tool_components
from agentos._builder_validation import require_unset
from agentos.capabilities import RegisteredTool, ToolCallRouter
from agentos.compression import CompressionRuntime, Compressor
from agentos.context import ContextProjectionProvider, ContextRenderer, ContextRuntime
from agentos.context.projection import default_system_section_registry
from agentos.events import EventBus
from agentos.messages import MessageRuntime
from agentos.policies import BudgetPolicy, TokenBudgetPolicy, ToolResultBudget
from agentos.providers import Provider
from agentos.runtime import Agent
from agentos.runtime.provider_request_builder import SystemEnvelopeRenderer
from agentos.tokens import HeuristicTokenCounter, TokenCounter


DEFAULT_COMPRESSION_BUDGET = BudgetPolicy(
    max_active_messages=20,
    retain_latest_messages=6,
)
"""AgentBuilder.with_compression() 使用的保守默认消息预算。"""


@dataclass(slots=True)
class AgentBuilder:
    """把 Agent 所需运行时组件组装成标准 Agent。"""

    _provider: Provider | None = None
    _tools: list[RegisteredTool] | None = None
    _context_runtime: ContextRuntime | None = None
    _message_runtime: MessageRuntime | None = None
    _context_renderer: SystemEnvelopeRenderer | None = None
    _compression_runtime: CompressionRuntime | None = None
    _event_bus: EventBus | None = None
    _tool_call_router: ToolCallRouter | None = None
    _tool_result_budget: ToolResultBudget | None = None
    _token_counter: TokenCounter | None = None
    _compression_requested: bool = False
    _compressor: Compressor | None = None
    _compression_context_window: int | None = None
    _compression_reserve_output_tokens: int = 4096
    _compression_retain_latest_tokens: int = 8000
    _compression_static_overhead_tokens: int = 0
    _compression_token_counter: TokenCounter | None = None
    _max_parallel_calls: int | None = None
    _context_projection_providers: tuple[ContextProjectionProvider, ...] | None = None

    def provider(self, provider: Provider) -> "AgentBuilder":
        """设置模型 provider。"""

        require_unset(self._provider, "provider")
        self._provider = provider
        return self

    def tools(self, tools: list[RegisteredTool]) -> "AgentBuilder":
        """设置外部工具声明。"""

        require_unset(self._tools, "tools")
        self._tools = list(tools)
        return self

    def context_runtime(self, runtime: ContextRuntime) -> "AgentBuilder":
        """覆盖默认 context runtime。"""

        require_unset(self._context_runtime, "context_runtime")
        self._context_runtime = runtime
        return self

    def message_runtime(self, runtime: MessageRuntime) -> "AgentBuilder":
        """覆盖默认 message runtime。"""

        require_unset(self._message_runtime, "message_runtime")
        self._message_runtime = runtime
        return self

    def context_renderer(self, renderer: SystemEnvelopeRenderer) -> "AgentBuilder":
        """覆盖默认 context renderer。"""

        require_unset(self._context_renderer, "context_renderer")
        self._context_renderer = renderer
        return self

    def compression_runtime(self, runtime: CompressionRuntime) -> "AgentBuilder":
        """覆盖默认 compression runtime。"""

        require_unset(self._compression_runtime, "compression_runtime")
        if self._compression_requested:
            raise ValueError(
                "AgentBuilder cannot use both .compression_runtime() and "
                ".with_compression(). Choose one compression setup.",
            )
        self._compression_runtime = runtime
        return self

    def event_bus(self, bus: EventBus) -> "AgentBuilder":
        """覆盖默认 event bus。"""

        require_unset(self._event_bus, "event_bus")
        self._event_bus = bus
        return self

    def tool_call_router(self, router: ToolCallRouter) -> "AgentBuilder":
        """覆盖默认 tool call router。"""

        require_unset(self._tool_call_router, "tool_call_router")
        self._tool_call_router = router
        return self

    def tool_result_budget(self, budget: ToolResultBudget) -> "AgentBuilder":
        """覆盖默认 tool result token 预算。"""

        require_unset(self._tool_result_budget, "tool_result_budget")
        self._tool_result_budget = budget
        return self

    def token_counter(self, counter: TokenCounter) -> "AgentBuilder":
        """覆盖默认 token counter。"""

        require_unset(self._token_counter, "token_counter")
        self._token_counter = counter
        return self

    def max_parallel_calls(self, value: int) -> "AgentBuilder":
        """设置单个工具批次的最大并发调用数。"""

        require_unset(self._max_parallel_calls, "max_parallel_calls")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("max_parallel_calls must be an integer greater than zero")
        self._max_parallel_calls = value
        return self

    def context_projections(
        self,
        providers: Iterable[ContextProjectionProvider],
    ) -> "AgentBuilder":
        """注册按每次 Provider attempt 重新读取的 Context 投影来源。"""

        require_unset(self._context_projection_providers, "context_projections")
        if isinstance(providers, (str, bytes)):
            raise TypeError("context projection providers must be an iterable")
        resolved = tuple(providers)
        if any(not callable(getattr(provider, "projections", None)) for provider in resolved):
            raise TypeError(
                "context projection providers must satisfy ContextProjectionProvider",
            )
        self._context_projection_providers = resolved
        return self

    def with_compression(
        self,
        compressor: Compressor | None = None,
        *,
        context_window: int | None = None,
        reserve_output_tokens: int = 4096,
        retain_latest_tokens: int = 8000,
        static_overhead_tokens: int = 0,
        token_counter: TokenCounter | None = None,
    ) -> "AgentBuilder":
        """启用 compression runtime，默认使用 deterministic compressor。"""

        if self._compression_requested:
            raise ValueError(
                "AgentBuilder.with_compression() called twice. Remove one call.",
            )
        if self._compression_runtime is not None:
            raise ValueError(
                "AgentBuilder cannot use both .compression_runtime() and "
                ".with_compression(). Choose one compression setup.",
            )
        self._compression_requested = True
        self._compressor = compressor
        self._compression_context_window = context_window
        self._compression_reserve_output_tokens = reserve_output_tokens
        self._compression_retain_latest_tokens = retain_latest_tokens
        self._compression_static_overhead_tokens = static_overhead_tokens
        self._compression_token_counter = token_counter
        return self

    def build(self, *, session_id: str | None = None) -> Agent:
        """构建标准 Agent facade。"""

        if session_id is not None and not session_id.strip():
            raise ValueError("session_id must not be empty")
        resolved_session_id = (
            f"session_{uuid4().hex}" if session_id is None else session_id
        )
        return Agent(
            query_loop_kwargs=self._query_loop_kwargs(resolved_session_id),
        )

    def _query_loop_kwargs(
        self, session_id: str, *, state: RuntimeStateComponents | None = None,
        messages: MessageRuntime | None = None, injected: Iterable[RegisteredTool] = (),
    ) -> dict[str, object]:
        """组装唯一 QueryLoop 使用的组件。"""

        if self._provider is None:
            raise ValueError(
                "AgentBuilder requires .provider() before .build(). "
                'Pass a Provider instance, e.g. AnthropicProvider(api_key="...")',
            )

        messages = messages or self._message_runtime or MessageRuntime()
        state = state or assemble_local_state(
            session_id=session_id, context=self._context_runtime,
            event_bus=self._event_bus,
        )
        context = state.context
        artifacts = state.artifacts
        compression_runtime = self._compression_runtime
        if self._compression_requested:
            compression_runtime = CompressionRuntime(
                context_runtime=context,
                message_runtime=messages,
                budget_policy=self._compression_budget_policy(),
                compressor=self._compressor,
                event_bus=self._event_bus,
            )
        recall_runtime = assemble_recall_runtime(
            compression_runtime=compression_runtime,
            message_runtime=messages,
        )
        tool_components = assemble_tool_components(
            tools=[*(self._tools or ()), *injected] or None,
            tool_call_router=self._tool_call_router,
            context_runtime=context,
            recall_runtime=recall_runtime,
            artifact_runtime=artifacts,
            max_parallel_calls=self._max_parallel_calls,
        )
        renderer = self._context_renderer or self._default_renderer()
        token_counter = self._token_counter or HeuristicTokenCounter()
        request_builder = assemble_provider_request_builder(
            renderer=renderer,
            messages=messages,
            tools=tool_components.provider_tools,
            token_counter=token_counter,
            state=state,
            extension_projections=self._context_projection_providers or (),
        )
        kwargs = {
            "context_runtime": context,
            "message_runtime": messages,
            "request_builder": request_builder,
            "provider": self._provider,
            "tool_result_budget": self._tool_result_budget or ToolResultBudget(),
            "token_counter": token_counter,
            "tool_scheduler": tool_components.scheduler,
            "session_state": state.session,
            "artifact_runtime": artifacts,
            "run_runtime": state.runs,
        }
        kwargs["tool_call_router"] = tool_components.router
        if compression_runtime is not None:
            kwargs["compression_runtime"] = compression_runtime
        if self._event_bus is not None:
            kwargs["event_bus"] = self._event_bus
        return kwargs

    def _compression_budget_policy(self) -> BudgetPolicy | TokenBudgetPolicy:
        if self._compression_context_window is None:
            return DEFAULT_COMPRESSION_BUDGET
        return TokenBudgetPolicy(
            token_counter=(
                self._compression_token_counter
                or self._token_counter
                or HeuristicTokenCounter()
            ),
            context_window=self._compression_context_window,
            reserve_output_tokens=self._compression_reserve_output_tokens,
            retain_latest_tokens=self._compression_retain_latest_tokens,
            static_overhead_tokens=self._compression_static_overhead_tokens,
        )

    def _default_renderer(self) -> ContextRenderer:
        return ContextRenderer(
            registry=default_system_section_registry(),
            token_counter=self._token_counter or HeuristicTokenCounter(),
        )
