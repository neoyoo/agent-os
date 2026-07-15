from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from agentos.capabilities.skill_types import (
    SkillMetadata,
    SkillTrustDecision,
    _require_skill_name,
)


_CONTENT_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SkillVerificationSubject:
    """绑定 Skill 来源、名称、版本和正文摘要的验证主体。"""

    source_id: str
    skill_name: str
    source_revision: str
    content_digest: str

    def __post_init__(self) -> None:
        values = (self.source_id, self.source_revision)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("skill verification subject is invalid")
        try:
            _require_skill_name(self.skill_name)
        except ValueError as error:
            raise ValueError("skill verification subject is invalid") from error
        if (
            not isinstance(self.content_digest, str)
            or _CONTENT_DIGEST_RE.fullmatch(self.content_digest) is None
        ):
            raise ValueError("skill verification subject is invalid")

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

        if not isinstance(content, str):
            raise ValueError("skill verification subject content must be a string")

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
