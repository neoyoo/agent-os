from __future__ import annotations

import json
from dataclasses import dataclass

from agentos.capabilities.skill_runtime import SkillRuntime
from agentos.capabilities.tools import RegisteredTool


@dataclass(frozen=True, slots=True)
class BoundSkillTools:
    """生成闭包捕获 Session 的 Skill 工具。"""

    runtime: SkillRuntime
    session_id: str

    def registered_tools(self) -> tuple[RegisteredTool, ...]:
        async def load_skill(arguments: dict[str, object]) -> str:
            skill_name = str(arguments.get("skill_name", ""))
            try:
                return await self.runtime.load(self.session_id, skill_name)
            except KeyError:
                return self._not_found(skill_name)

        async def disable_skill(arguments: dict[str, object]) -> str:
            skill_name = str(arguments.get("skill_name", ""))
            await self.runtime.disable(self.session_id, skill_name)
            return f"Skill 已停用：{skill_name}。"

        async def load_skill_resource(arguments: dict[str, object]) -> str:
            skill_name = str(arguments.get("skill_name", ""))
            path = str(arguments.get("path", ""))
            try:
                result = await self.runtime.registry.load_resource(skill_name, path)
                return result.render_tool_result()
            except KeyError:
                return self._not_found(skill_name, path)

        parameters = {
            "type": "object",
            "properties": {"skill_name": {"type": "string"}},
            "required": ["skill_name"],
            "additionalProperties": False,
        }
        return (
            RegisteredTool(
                name="load_skill",
                description="Load a Skill for the current Session.",
                parameters=parameters,
                handler=load_skill,
                kind="skill",
            ),
            RegisteredTool(
                name="disable_skill",
                description="Disable an active Skill for the current Session.",
                parameters=parameters,
                handler=disable_skill,
                kind="skill",
            ),
            RegisteredTool(
                name="load_skill_resource",
                description="Load an additional resource for a Skill by path.",
                parameters={
                    "type": "object",
                    "properties": {
                        "skill_name": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["skill_name", "path"],
                    "additionalProperties": False,
                },
                handler=load_skill_resource,
                kind="skill",
            ),
        )

    def _not_found(self, skill_name: str, path: str | None = None) -> str:
        error = (
            f"Skill '{skill_name}' not found"
            if path is None
            else f"Resource '{path}' for skill '{skill_name}' not found"
        )
        return json.dumps(
            {
                "error": error,
                "available_skills": self.runtime.registry.available_skill_names(),
            },
            ensure_ascii=False,
        )


__all__ = ["BoundSkillTools"]
