from __future__ import annotations

from dataclasses import dataclass

from agentos.attachments import AttachmentRuntime
from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.compression import CompressionIndex, CompressionRuntime, Compressor
from agentos.context import CapabilityPlane, ContextRenderer, ContextRuntime
from agentos.events import EventBus
from agentos.messages import MessageRuntime
from agentos.policies import BudgetPolicy, TokenBudgetPolicy, ToolResultBudget
from agentos.providers import Provider
from agentos.recall import RecallRuntime
from agentos.runtime import Agent, AsyncQueryLoop, ProviderRequestBuilder
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
    _context_renderer: ContextRenderer | None = None
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

    def provider(self, provider: Provider) -> "AgentBuilder":
        """设置模型 provider。"""

        if self._provider is not None:
            raise ValueError("AgentBuilder.provider() called twice. Remove one call.")
        self._provider = provider
        return self

    def tools(self, tools: list[RegisteredTool]) -> "AgentBuilder":
        """设置外部工具声明。"""

        if self._tools is not None:
            raise ValueError("AgentBuilder.tools() called twice. Remove one call.")
        self._tools = list(tools)
        return self

    def context_runtime(self, runtime: ContextRuntime) -> "AgentBuilder":
        """覆盖默认 context runtime。"""

        if self._context_runtime is not None:
            raise ValueError(
                "AgentBuilder.context_runtime() called twice. Remove one call.",
            )
        self._context_runtime = runtime
        return self

    def message_runtime(self, runtime: MessageRuntime) -> "AgentBuilder":
        """覆盖默认 message runtime。"""

        if self._message_runtime is not None:
            raise ValueError(
                "AgentBuilder.message_runtime() called twice. Remove one call.",
            )
        self._message_runtime = runtime
        return self

    def context_renderer(self, renderer: ContextRenderer) -> "AgentBuilder":
        """覆盖默认 context renderer。"""

        if self._context_renderer is not None:
            raise ValueError(
                "AgentBuilder.context_renderer() called twice. Remove one call.",
            )
        self._context_renderer = renderer
        return self

    def compression_runtime(self, runtime: CompressionRuntime) -> "AgentBuilder":
        """覆盖默认 compression runtime。"""

        if self._compression_runtime is not None:
            raise ValueError(
                "AgentBuilder.compression_runtime() called twice. Remove one call.",
            )
        if self._compression_requested:
            raise ValueError(
                "AgentBuilder cannot use both .compression_runtime() and "
                ".with_compression(). Choose one compression setup.",
            )
        self._compression_runtime = runtime
        return self

    def event_bus(self, bus: EventBus) -> "AgentBuilder":
        """覆盖默认 event bus。"""

        if self._event_bus is not None:
            raise ValueError("AgentBuilder.event_bus() called twice. Remove one call.")
        self._event_bus = bus
        return self

    def tool_call_router(self, router: ToolCallRouter) -> "AgentBuilder":
        """覆盖默认 tool call router。"""

        if self._tool_call_router is not None:
            raise ValueError(
                "AgentBuilder.tool_call_router() called twice. Remove one call.",
            )
        self._tool_call_router = router
        return self

    def tool_result_budget(self, budget: ToolResultBudget) -> "AgentBuilder":
        """覆盖默认 tool result token 预算。"""

        if self._tool_result_budget is not None:
            raise ValueError(
                "AgentBuilder.tool_result_budget() called twice. Remove one call.",
            )
        self._tool_result_budget = budget
        return self

    def token_counter(self, counter: TokenCounter) -> "AgentBuilder":
        """覆盖默认 token counter。"""

        if self._token_counter is not None:
            raise ValueError(
                "AgentBuilder.token_counter() called twice. Remove one call.",
            )
        self._token_counter = counter
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

    def build(self) -> Agent:
        """构建标准 Agent facade。"""

        return Agent(query_loop_kwargs=self._query_loop_kwargs())

    def build_async(self) -> Agent:
        """构建使用原生 AsyncQueryLoop 的 Agent facade。"""

        return Agent(query_loop=AsyncQueryLoop(**self._query_loop_kwargs()))  # type: ignore[arg-type]

    def _query_loop_kwargs(self) -> dict[str, object]:
        """组装 QueryLoop / AsyncQueryLoop 共用组件。"""

        if self._provider is None:
            raise ValueError(
                "AgentBuilder requires .provider() before .build(). "
                'Pass a Provider instance, e.g. AnthropicProvider(api_key="...")',
            )

        messages = self._message_runtime or MessageRuntime()
        context = self._context_runtime or ContextRuntime(event_bus=self._event_bus)
        attachments = AttachmentRuntime()
        compression_runtime = self._compression_runtime
        if self._compression_requested:
            compression_runtime = CompressionRuntime(
                context_runtime=context,
                message_runtime=messages,
                budget_policy=self._compression_budget_policy(),
                compressor=self._compressor,
                event_bus=self._event_bus,
            )
        recall_runtime = RecallRuntime(
            compression_index=(
                compression_runtime.index
                if compression_runtime is not None
                else CompressionIndex()
            ),
            message_runtime=messages,
        )
        tool_registry = ToolRegistry()
        for tool in self._tools or []:
            tool_registry.register(tool)
        if self._tools is not None and self._tool_call_router is not None:
            raise ValueError(
                "AgentBuilder cannot use both .tools() and .tool_call_router(). "
                "Choose one tool setup.",
            )
        tool_router = self._tool_call_router
        provider_tools = []
        if self._tools is not None:
            tool_router = ToolCallRouter(
                tool_registry=tool_registry,
                context_runtime=context,
                recall_runtime=recall_runtime,
                attachment_runtime=attachments,
            )
            provider_tools = tool_router.tool_specs()
        elif tool_router is not None:
            if getattr(tool_router, "attachment_runtime", None) is None:
                tool_router.attachment_runtime = attachments
            provider_tools = tool_router.tool_specs()
        else:
            tool_router = ToolCallRouter(
                tool_registry=tool_registry,
                context_runtime=context,
                recall_runtime=recall_runtime,
                attachment_runtime=attachments,
            )
            provider_tools = tool_router.tool_specs()
        renderer = self._context_renderer or self._default_renderer(
            tool_registry=tool_registry,
            tool_router=tool_router,
        )
        request_builder = ProviderRequestBuilder(
            context_renderer=renderer,
            message_runtime=messages,
            tools=provider_tools,
            attachment_runtime=attachments,
        )
        kwargs = {
            "context_runtime": context,
            "message_runtime": messages,
            "request_builder": request_builder,
            "provider": self._provider,
            "tool_result_budget": self._tool_result_budget or ToolResultBudget(),
            "token_counter": self._token_counter or HeuristicTokenCounter(),
        }
        if tool_router is not None:
            kwargs["tool_call_router"] = tool_router
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

    def _default_renderer(
        self,
        *,
        tool_registry: ToolRegistry,
        tool_router: ToolCallRouter | None,
    ) -> ContextRenderer:
        tool_groups = []
        if self._tools:
            tool_groups.append(tool_registry.capability_tool_group("Registered tools"))
        elif tool_router is not None:
            tool_groups.append(
                tool_router.tool_registry.capability_tool_group("Registered tools"),
            )
        return ContextRenderer(
            capability_plane=CapabilityPlane(tool_groups=tool_groups),
        )
