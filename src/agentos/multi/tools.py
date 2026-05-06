from __future__ import annotations

import json
from dataclasses import asdict

from agentos.capabilities import RegisteredTool, ToolRegistry
from agentos.multi.coordinator import AgentCoordinator
from agentos.multi.types import TaskHandle


class AgentCoordinationTools:
    """把 multi-agent 协调能力注册为普通 external tools。"""

    def __init__(
        self,
        *,
        coordinator: AgentCoordinator,
        parent_agent_id: str,
    ) -> None:
        """绑定 coordinator 和调用这些工具的父 agent。"""

        self.coordinator = coordinator
        self.parent_agent_id = parent_agent_id

    def register(self, registry: ToolRegistry) -> None:
        """向 ToolRegistry 注册四个协调工具。"""

        registry.register(
            RegisteredTool(
                name="spawn_subagent",
                description="Spawn an isolated local subagent for a task.",
                parameters=self._spawn_parameters(),
                handler=self._spawn_subagent,
            ),
        )
        registry.register(
            RegisteredTool(
                name="dispatch_to_expert",
                description="Dispatch a task to an available expert agent.",
                parameters=self._dispatch_parameters(),
                handler=self._dispatch_to_expert,
            ),
        )
        registry.register(
            RegisteredTool(
                name="check_agent_tasks",
                description="Check active multi-agent tasks and collect results.",
                parameters={"type": "object", "properties": {}},
                handler=self._check_agent_tasks,
            ),
        )
        registry.register(
            RegisteredTool(
                name="cancel_agent_task",
                description="Cancel a queued or running multi-agent task.",
                parameters=self._cancel_parameters(),
                handler=self._cancel_agent_task,
            ),
        )

    def _spawn_subagent(self, arguments: dict[str, object]) -> str:
        handle = self.coordinator.spawn(
            instruction=str(arguments["instruction"]),
            allowed_tool_names=self._string_tuple(
                arguments.get("allowed_tool_names", ()),
            ),
            parent_agent_id=self.parent_agent_id,
            timeout_seconds=float(arguments.get("timeout_seconds", 300)),
        )
        return self._json_handle(handle)

    def _dispatch_to_expert(self, arguments: dict[str, object]) -> str:
        handle = self.coordinator.dispatch(
            instruction=str(arguments["instruction"]),
            required_capabilities=self._string_tuple(
                arguments["required_capabilities"],
            ),
            parent_agent_id=self.parent_agent_id,
            allowed_tool_names=self._string_tuple(
                arguments.get("allowed_tool_names", ()),
            ),
            timeout_seconds=float(arguments.get("timeout_seconds", 300)),
        )
        return self._json_handle(handle)

    def _check_agent_tasks(self, arguments: dict[str, object]) -> str:
        active_tasks = self.coordinator.active_tasks(self.parent_agent_id)
        results = self.coordinator.collect_results(self.parent_agent_id)
        return json.dumps(
            {
                "active_tasks": [asdict(handle) for handle in active_tasks],
                "results": [asdict(result) for result in results],
            },
            sort_keys=True,
        )

    def _cancel_agent_task(self, arguments: dict[str, object]) -> str:
        task_id = str(arguments["task_id"])
        return json.dumps(
            {
                "task_id": task_id,
                "cancelled": self.coordinator.cancel(task_id),
            },
            sort_keys=True,
        )

    def _json_handle(self, handle: TaskHandle) -> str:
        return json.dumps(asdict(handle), sort_keys=True)

    def _string_tuple(self, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if not isinstance(value, list | tuple):
            raise ValueError("expected list of strings")
        return tuple(str(item) for item in value)

    def _spawn_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "instruction": {"type": "string"},
                "allowed_tool_names": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "timeout_seconds": {"type": "number"},
            },
            "required": ["instruction"],
        }

    def _dispatch_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "instruction": {"type": "string"},
                "required_capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "allowed_tool_names": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "timeout_seconds": {"type": "number"},
            },
            "required": ["instruction", "required_capabilities"],
        }

    def _cancel_parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        }
