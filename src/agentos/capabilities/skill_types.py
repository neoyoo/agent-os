from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agentos.capabilities.skill_trust import SkillVerificationSubject


SkillSource = Literal["builtin", "filesystem", "learned"]
SkillTrust = Literal["trusted", "untrusted"]


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """可进入 available-skills 投影的安全元数据。"""

    name: str
    description: str
    loadable: bool
    trust: SkillTrust


@dataclass(frozen=True, slots=True)
class SkillDescriptor:
    """Source 发现阶段返回的不含正文描述。"""

    metadata: SkillMetadata
    when_to_use: str


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """内置 Source 接收的完整 Skill 定义。"""

    name: str
    description: str
    when_to_use: str
    content: str
    source: SkillSource = "filesystem"
    trust: SkillTrust = "untrusted"
    source_revision: str = "1"

    def descriptor(self) -> SkillDescriptor:
        """返回不携带正文的发现描述。"""

        return SkillDescriptor(
            metadata=SkillMetadata(
                name=self.name,
                description=self.description,
                loadable=True,
                trust=self.trust,
            ),
            when_to_use=self.when_to_use,
        )


@dataclass(frozen=True, slots=True)
class SkillTrustDecision:
    """Trust Policy 对完整验证主体作出的决定。"""

    verified: bool
    policy_id: str
    subject: SkillVerificationSubject


@dataclass(frozen=True, slots=True)
class SkillLoadResult:
    """按需加载并绑定验证主体的 Skill 正文。"""

    name: str
    content: str
    metadata: SkillMetadata
    subject: SkillVerificationSubject

    def render_tool_result(
        self,
        resource_manifest: tuple[SkillResourceRef, ...] = (),
    ) -> str:
        """渲染为有界 Tool Result 文本。"""

        body = f"# Skill: {self.name}\n\n{self.content}"
        if not resource_manifest:
            return body
        resources = "\n".join(
            f"- `{resource.path}` ({resource.mime_type})"
            for resource in resource_manifest
        )
        return (
            f"{body}\n\n## Available resources\n{resources}\n\n"
            "Use `load_skill_resource` to load any of the above."
        )


@dataclass(frozen=True, slots=True)
class SkillResourceRef:
    """Skill 附带资源的轻量 manifest 项。"""

    path: str
    mime_type: str = "text/plain"


@dataclass(frozen=True, slots=True)
class SkillResourceLoadResult:
    """`load_skill_resource` 工具返回的结构化结果。"""

    skill_name: str
    path: str
    content: str
    mime_type: str = "text/plain"

    def render_tool_result(self) -> str:
        """渲染为写入 Tool Result 的文本。"""

        return self.content
