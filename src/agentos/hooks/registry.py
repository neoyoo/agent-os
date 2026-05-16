import asyncio
from dataclasses import dataclass

from agentos.hooks.base import HookFailurePolicy, HookHandler, HookName


@dataclass(frozen=True, slots=True)
class HookRegistration:
    """单个 hook 注册项。"""

    hook_name: HookName
    handler: HookHandler
    failure_policy: HookFailurePolicy = "continue"
    priority: int = 100


class HookRegistry:
    """按显式 hook point 保存有序 hook 注册。"""

    def __init__(self) -> None:
        """创建空 hook registry。"""

        self._registrations: list[HookRegistration] = []

    def register(
        self,
        hook_name: HookName,
        handler: HookHandler,
        failure_policy: HookFailurePolicy = "continue",
        priority: int = 100,
    ) -> None:
        """注册 hook，注册顺序就是执行顺序。"""

        if asyncio.iscoroutinefunction(handler):
            raise TypeError(
                "async handler not supported in synchronous HookManager; "
                f"got {handler!r}",
            )
        self._registrations.append(
            HookRegistration(
                hook_name=hook_name,
                handler=handler,
                failure_policy=failure_policy,
                priority=priority,
            ),
        )

    def hooks_for(self, hook_name: HookName) -> list[HookRegistration]:
        """返回匹配 hook point 的注册项。"""

        return [
            registration
            for registration in self._registrations
            if hook_name == registration.hook_name
        ]
