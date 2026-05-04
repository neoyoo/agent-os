from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from agentos.hooks.base import HookContext, HookExecutionFailure, HookName, HookResult
from agentos.hooks.registry import HookRegistry


@dataclass(slots=True)
class HookManager:
    """执行 HookRegistry 中匹配的显式 hook point。"""

    registry: HookRegistry
    failures: list[HookExecutionFailure] = field(default_factory=list)

    def dispatch(
        self,
        hook_name: HookName,
        payload: Mapping[str, object] | None = None,
    ) -> HookResult:
        """按注册顺序执行 hook，并返回最终执行决策。"""

        current_payload = dict(payload or {})
        modified = False
        for registration in self.registry.hooks_for(hook_name):
            context = HookContext(
                name=hook_name,
                payload=MappingProxyType(current_payload),
            )
            try:
                result = registration.handler(context)
            except Exception as error:
                self.failures.append(
                    HookExecutionFailure(
                        hook_name=hook_name,
                        error=str(error),
                    ),
                )
                if registration.failure_policy == "raise":
                    raise
                continue

            if result is None or result.action == "allow":
                continue
            if result.action == "deny":
                return result
            if result.action == "modify":
                current_payload = dict(result.payload or current_payload)
                modified = True

        if modified:
            return HookResult(action="modify", payload=current_payload)
        return HookResult(action="allow", payload=current_payload)
