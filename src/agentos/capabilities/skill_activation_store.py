from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentos.capabilities.skill_trust import SkillVerificationSubject


@dataclass(frozen=True, slots=True)
class SkillActivationRecord:
    """不包含 Skill 正文的持久激活引用。"""

    session_id: str
    subject: SkillVerificationSubject
    policy_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("skill activation session_id must not be empty")
        if not isinstance(self.subject, SkillVerificationSubject):
            raise TypeError("skill activation subject is invalid")
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("skill activation policy_id must not be empty")

    @property
    def skill_name(self) -> str:
        """返回验证主体绑定的稳定 Skill 名称。"""

        return self.subject.skill_name


class SkillActivationStore(Protocol):
    """SkillRuntime 使用的持久激活引用 Store Port。"""

    async def save(self, record: SkillActivationRecord) -> None: ...

    async def list(self, session_id: str) -> tuple[SkillActivationRecord, ...]: ...

    async def delete(self, session_id: str, skill_name: str) -> bool: ...

    async def delete_session(self, session_id: str) -> None: ...


__all__ = ["SkillActivationRecord", "SkillActivationStore"]
