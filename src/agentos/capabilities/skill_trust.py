from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from agentos.capabilities.skill_types import SkillMetadata, SkillTrustDecision


@dataclass(frozen=True, slots=True)
class SkillVerificationSubject:
    """绑定 Skill 来源、名称、版本和正文摘要的验证主体。"""

    source_id: str
    skill_name: str
    source_revision: str
    content_digest: str

    @classmethod
    def from_content(
        cls,
        *,
        source_id: str,
        skill_name: str,
        source_revision: str,
        content: str,
    ) -> "SkillVerificationSubject":
        """从 UTF-8 正文生成稳定验证主体。"""

        return cls(
            source_id=source_id,
            skill_name=skill_name,
            source_revision=source_revision,
            content_digest=sha256(content.encode("utf-8")).hexdigest(),
        )


class SkillTrustPolicy(Protocol):
    """验证 Skill 是否允许进入可信指令平面。"""

    def verify(
        self,
        metadata: SkillMetadata,
        subject: SkillVerificationSubject,
    ) -> SkillTrustDecision:
        """返回逐字段绑定该 Skill 的验证决定。"""
