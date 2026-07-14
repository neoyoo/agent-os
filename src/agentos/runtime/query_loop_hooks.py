from __future__ import annotations

from dataclasses import dataclass

from agentos.capabilities.executor import ToolExecutionResult
from agentos.hooks import HookManager, HookResult
from agentos.providers import ProviderRequest, ProviderResponse, ProviderToolCall


@dataclass(slots=True)
class QueryLoopHooks:
    """把 QueryLoop 生命周期转换为显式 Hook 调用。"""

    manager: HookManager | None

    def before_provider_call(self, request: ProviderRequest) -> ProviderRequest:
        result = self._dispatch("before_provider_call", {"request": request})
        self._raise_if_denied(result, "provider call")
        if result.action == "modify" and result.payload is not None:
            modified = result.payload.get("request")
            if isinstance(modified, ProviderRequest):
                return modified
        return request

    def after_provider_call(
        self,
        request: ProviderRequest,
        response: ProviderResponse,
    ) -> ProviderResponse:
        result = self._dispatch(
            "after_provider_call",
            {"request": request, "response": response},
        )
        self._raise_if_denied(result, "provider response")
        if result.action == "modify" and result.payload is not None:
            modified = result.payload.get("response")
            if isinstance(modified, ProviderResponse):
                return modified
        return response

    def before_tool_call(self, call: ProviderToolCall) -> ToolExecutionResult | None:
        result = self._dispatch(
            "before_tool_call",
            {"tool_call": call, "tool_name": call.name, "tool_call_id": call.id},
        )
        if result.action == "deny":
            return ToolExecutionResult(
                call.id,
                f"tool call denied by hook: {result.reason or 'denied'}",
            )
        return None

    def after_tool_call(
        self,
        call: ProviderToolCall,
        result: ToolExecutionResult,
    ) -> ToolExecutionResult:
        hook_result = self._dispatch(
            "after_tool_call",
            {
                "tool_call": call,
                "tool_name": call.name,
                "tool_call_id": call.id,
                "result": result,
            },
        )
        self._raise_if_denied(hook_result, "tool result")
        if hook_result.action == "modify" and hook_result.payload is not None:
            modified = hook_result.payload.get("result", result)
            if isinstance(modified, ToolExecutionResult):
                return modified
        return result

    def _dispatch(
        self,
        name: str,
        payload: dict[str, object] | None = None,
    ) -> HookResult:
        if self.manager is None:
            return HookResult(action="allow", payload=dict(payload or {}))
        return self.manager.dispatch(name, payload)  # type: ignore[arg-type]

    @staticmethod
    def _raise_if_denied(result: HookResult, target: str) -> None:
        if result.action == "deny":
            raise RuntimeError(
                f"{target} denied by hook: {result.reason or 'denied'}",
            )
