from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentos.capabilities.skill_activation_store import (
    SkillActivationRecord,
    SkillActivationStore,
)
from agentos.capabilities.skill_projection import project_available_skills
from agentos.capabilities.skill_trust import (
    SkillTrustPolicy,
    SkillVerificationSubject,
)
from agentos.capabilities.skill_types import (
    SkillLoadResult,
    SkillMetadata,
    SkillResourceRef,
    SkillTrustDecision,
)
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
        activation_store: SkillActivationStore | None = None,
    ) -> None:
        self._registry = registry
        self._trust_policy = trust_policy
        self._activation_store = activation_store
        self._active: dict[tuple[str, str], _ActiveSkill] = {}

    @property
    def registry(self) -> SkillRegistry:
        """返回 Skill 真值 Registry。"""

        return self._registry

    async def load(self, session_id: str, skill_name: str) -> str:
        """加载 Skill；只有 verified trusted Skill 才激活。"""

        key = self._activation_key(session_id, skill_name)
        self._deactivate(key)
        loaded = await self._registry.load(skill_name)
        if loaded.metadata.trust == "untrusted":
            resources = await self._registry.list_resources(skill_name)
            return self._bounded_untrusted_result(loaded, resources)
        decision = self._verify(loaded.metadata, loaded)
        if not self._registry.is_subject_current(loaded.subject):
            raise SkillTrustError("skill source revision changed during load")
        active = _ActiveSkill(loaded=loaded, decision=decision)
        if self._activation_store is not None:
            self._activation_store.save(
                SkillActivationRecord(session_id, loaded.subject, decision.policy_id),
            )
        self._active[key] = active
        return f"Skill 已加载：{skill_name}。可信指令将在下一次模型请求中生效。"

    async def restore(self, session_id: str) -> tuple[str, ...]:
        """重新加载并复验持久激活；不一致记录会被删除。"""

        self._validate_session_id(session_id)
        if self._activation_store is None:
            return ()
        for key in tuple(self._active):
            if key[0] == session_id:
                del self._active[key]
        restored = []
        for record in self._activation_store.list(session_id):
            try:
                loaded = await self._registry.load(record.skill_name)
                decision = self._verify(loaded.metadata, loaded)
                valid = (
                    loaded.subject == record.subject
                    and decision.subject == record.subject
                    and decision.policy_id == record.policy_id
                    and self._registry.is_subject_current(loaded.subject)
                )
            except (KeyError, SkillTrustError, ValueError):
                valid = False
            if not valid:
                self._activation_store.delete(session_id, record.skill_name)
                continue
            self._active[(session_id, record.skill_name)] = _ActiveSkill(
                loaded=loaded,
                decision=decision,
            )
            restored.append(record.skill_name)
        return tuple(restored)

    def disable(self, session_id: str, skill_name: str) -> bool:
        """停用当前 Session 的 Skill。"""

        key = self._activation_key(session_id, skill_name)
        return self._deactivate(key)

    def items(self, session_id: str) -> tuple[TrustedSkillInstruction, ...]:
        """返回当前 Session 仍通过 Policy 复验的可信指令。"""

        self._validate_session_id(session_id)
        instructions = []
        invalid = []
        for key, active in self._active.items():
            if key[0] != session_id:
                continue
            if not self._registry.is_subject_current(active.loaded.subject):
                invalid.append(key)
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
            self._deactivate(key)
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
        if self._activation_store is not None:
            self._activation_store.delete_session(session_id)

    def _deactivate(self, key: tuple[str, str]) -> bool:
        removed = self._active.pop(key, None) is not None
        if self._activation_store is not None:
            removed = self._activation_store.delete(*key) or removed
        return removed

    def _verify(
        self,
        metadata: SkillMetadata,
        loaded: SkillLoadResult,
    ) -> SkillTrustDecision:
        decision = self._trust_policy.verify(metadata, loaded.subject)
        if (
            type(decision) is not SkillTrustDecision
            or type(decision.verified) is not bool
            or not isinstance(decision.policy_id, str)
            or not decision.policy_id.strip()
            or not isinstance(decision.subject, SkillVerificationSubject)
            or not decision.verified
            or decision.subject != loaded.subject
        ):
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
