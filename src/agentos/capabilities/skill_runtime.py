from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentos.capabilities.skill_projection import project_available_skills
from agentos.capabilities.skill_trust import SkillTrustPolicy
from agentos.capabilities.skill_types import (
    SkillLoadResult,
    SkillMetadata,
    SkillResourceRef,
    SkillTrustDecision,
)
from agentos.capabilities.tools import RegisteredTool
from agentos.context.models import ContextSlotProjection, TrustedSkillInstruction

if TYPE_CHECKING:
    from agentos.capabilities.skills import SkillRegistry


MAX_UNTRUSTED_TOOL_RESULT_CHARS = 8_000


class SkillTrustError(PermissionError):
    """Skill 未通过可信指令验证。"""


@dataclass(frozen=True, slots=True)
class _ActiveSkill:
    loaded: SkillLoadResult
    decision: SkillTrustDecision


class SkillRuntime:
    """管理 Session-scoped Skill 激活和投影。"""

    def __init__(
        self,
        registry: SkillRegistry,
        trust_policy: SkillTrustPolicy,
    ) -> None:
        self._registry = registry
        self._trust_policy = trust_policy
        self._active: dict[tuple[str, str], _ActiveSkill] = {}

    @property
    def registry(self) -> SkillRegistry:
        """返回 Skill 真值 Registry。"""

        return self._registry

    async def load(self, session_id: str, skill_name: str) -> str:
        """加载 Skill；只有 verified trusted Skill 才激活。"""

        key = self._activation_key(session_id, skill_name)
        self._active.pop(key, None)
        loaded = await self._registry.load(skill_name)
        if loaded.metadata.trust == "untrusted":
            resources = await self._registry.list_resources(skill_name)
            return self._bounded_untrusted_result(loaded, resources)
        decision = self._verify(loaded.metadata, loaded)
        self._active[key] = _ActiveSkill(loaded=loaded, decision=decision)
        return (
            f"Skill 已加载：{skill_name}。"
            "可信指令将在下一次模型请求中生效。"
        )

    def disable(self, session_id: str, skill_name: str) -> bool:
        """停用当前 Session 的 Skill。"""

        key = self._activation_key(session_id, skill_name)
        return self._active.pop(key, None) is not None

    def items(self, session_id: str) -> tuple[TrustedSkillInstruction, ...]:
        """返回当前 Session 仍通过 Policy 复验的可信指令。"""

        self._validate_session_id(session_id)
        instructions = []
        invalid = []
        for key, active in self._active.items():
            if key[0] != session_id:
                continue
            try:
                decision = self._verify(active.loaded.metadata, active.loaded)
            except SkillTrustError:
                invalid.append(key)
                continue
            if decision.subject != active.decision.subject:
                invalid.append(key)
                continue
            instructions.append(
                TrustedSkillInstruction(
                    skill_id=key[1],
                    text=active.loaded.content,
                ),
            )
        for key in invalid:
            self._active.pop(key, None)
        return tuple(instructions)

    def projections(self, session_id: str) -> tuple[ContextSlotProjection, ...]:
        """投影当前 Session 可发现的安全 Skill Metadata。"""

        self._validate_session_id(session_id)
        return project_available_skills(self._registry.descriptors())

    def close_session(self, session_id: str) -> None:
        """清除 Session 的全部激活状态。"""

        self._validate_session_id(session_id)
        for key in tuple(self._active):
            if key[0] == session_id:
                del self._active[key]

    def _verify(
        self,
        metadata: SkillMetadata,
        loaded: SkillLoadResult,
    ) -> SkillTrustDecision:
        decision = self._trust_policy.verify(metadata, loaded.subject)
        if not decision.verified or decision.subject != loaded.subject:
            raise SkillTrustError("skill trust verification failed")
        return decision

    def _bounded_untrusted_result(
        self,
        loaded: SkillLoadResult,
        resources: tuple[SkillResourceRef, ...],
    ) -> str:
        rendered = loaded.render_tool_result(resources)
        if len(rendered) <= MAX_UNTRUSTED_TOOL_RESULT_CHARS:
            return rendered
        return rendered[:MAX_UNTRUSTED_TOOL_RESULT_CHARS] + "\n\n[内容已截断]"

    def _activation_key(self, session_id: str, skill_name: str) -> tuple[str, str]:
        self._validate_session_id(session_id)
        if not skill_name:
            raise ValueError("skill_name must not be empty")
        return session_id, skill_name

    def _validate_session_id(self, session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")


@dataclass(frozen=True, slots=True)
class BoundSkillInstructionProvider:
    """把无参 System Port 绑定到一个 Session。"""

    runtime: SkillRuntime
    session_id: str

    def items(self) -> tuple[TrustedSkillInstruction, ...]:
        return self.runtime.items(self.session_id)


@dataclass(frozen=True, slots=True)
class BoundSkillProjectionProvider:
    """把无参 Context Projection Port 绑定到一个 Session。"""

    runtime: SkillRuntime
    session_id: str

    def projections(self) -> tuple[ContextSlotProjection, ...]:
        return self.runtime.projections(self.session_id)


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
                return json.dumps(
                    {
                        "error": f"Skill '{skill_name}' not found",
                        "available_skills": (
                            self.runtime.registry.available_skill_names()
                        ),
                    },
                    ensure_ascii=False,
                )

        def disable_skill(arguments: dict[str, object]) -> str:
            skill_name = str(arguments.get("skill_name", ""))
            self.runtime.disable(self.session_id, skill_name)
            return f"Skill 已停用：{skill_name}。"

        async def load_skill_resource(arguments: dict[str, object]) -> str:
            skill_name = str(arguments.get("skill_name", ""))
            path = str(arguments.get("path", ""))
            try:
                result = await self.runtime.registry.load_resource(skill_name, path)
                return result.render_tool_result()
            except KeyError:
                return json.dumps(
                    {
                        "error": (
                            f"Resource '{path}' for skill '{skill_name}' not found"
                        ),
                        "available_skills": (
                            self.runtime.registry.available_skill_names()
                        ),
                    },
                    ensure_ascii=False,
                )

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
