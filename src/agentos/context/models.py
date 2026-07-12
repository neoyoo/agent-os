"""Context Protocol v1 的不可变值类型与稳定错误契约。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, TypeAlias

from agentos._internal_transcript import InternalTranscriptValue

if TYPE_CHECKING:
    from agentos.context.xml import XmlElement


CONTEXT_PROTOCOL = "agentos.context"
CONTEXT_PROTOCOL_VERSION = "1.0"

SystemSectionName: TypeAlias = Literal[
    "runtime_contract",
    "interaction_protocol",
    "context_management_rules",
    "runtime_directives",
    "trusted_skill_instructions",
    "workspace_contract",
]
ContextSlotName: TypeAlias = Literal[
    "declared-schema",
    "working-state",
    "active-plan",
    "inherited-state",
    "compressed-history",
    "memory-context",
    "available-skills",
    "artifact-catalog",
    "extensions",
]

_PROTOCOL_VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)",
)


class ContextProtocolError(ValueError):
    """Context Protocol v1 输入或组合不合法。"""


class ContextProtocolVersionError(ContextProtocolError):
    """Context Protocol 版本格式或 major 版本不受支持。"""


class ContextBudgetExceededError(ContextProtocolError):
    """受保护的 Context 内容无法放入配置预算。"""


class ContextSensitiveDataError(ContextProtocolError):
    """投影包含不得进入默认上下文的敏感表示。"""


@dataclass(frozen=True, slots=True)
class SystemEnvelope:
    """可信指令平面的确定性文本。"""

    text: str


@dataclass(frozen=True, slots=True)
class ContextSnapshot(InternalTranscriptValue):
    """动态上下文数据平面的确定性 XML。"""

    xml: str
    protocol: Literal["agentos.context"] = field(
        init=False,
        default=CONTEXT_PROTOCOL,
    )
    version: Literal["1.0"] = field(
        init=False,
        default=CONTEXT_PROTOCOL_VERSION,
    )


class RuntimeDirectiveKind(StrEnum):
    """Runtime 可生成的受控临时指令类型。"""

    BACKGROUND_TOOL_RUNNING = "background_tool_running"
    AWAITING_APPROVAL = "awaiting_approval"
    UNSUPPORTED_CONTENT_PART = "unsupported_content_part"


class AllowedContentPartKind(StrEnum):
    """不支持内容指令可引用的 ContentPart 类型。"""

    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"


@dataclass(frozen=True, slots=True)
class RuntimeContract:
    """Runtime 身份、安全护栏与附加规则。"""

    identity: str
    security_guardrails: tuple[str, ...]
    additional_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """复制可变序列并冻结为 tuple。"""

        object.__setattr__(
            self,
            "security_guardrails",
            _normalize_string_tuple(
                self.security_guardrails,
                field_name="security_guardrails",
            ),
        )
        object.__setattr__(
            self,
            "additional_rules",
            _normalize_string_tuple(
                self.additional_rules,
                field_name="additional_rules",
            ),
        )


@dataclass(frozen=True, slots=True)
class RuntimeDirective:
    """由枚举类型和固定参数组合描述的 Runtime 临时指令。"""

    kind: RuntimeDirectiveKind
    content_part_kind: AllowedContentPartKind | None = None

    def __post_init__(self) -> None:
        """拒绝自由字符串和不合法的指令参数组合。"""

        if not isinstance(self.kind, RuntimeDirectiveKind):
            raise ContextProtocolError(
                "runtime directive kind must be RuntimeDirectiveKind",
            )
        if self.kind is RuntimeDirectiveKind.UNSUPPORTED_CONTENT_PART:
            if not isinstance(self.content_part_kind, AllowedContentPartKind):
                raise ContextProtocolError(
                    "unsupported content part directive requires "
                    "AllowedContentPartKind",
                )
            return
        if self.content_part_kind is not None:
            raise ContextProtocolError(
                "runtime directive kind does not accept content part kind",
            )


@dataclass(frozen=True, slots=True)
class TrustedSkillInstruction:
    """已验证 Skill 可进入可信指令平面的正文。"""

    skill_id: str
    text: str


@dataclass(frozen=True, slots=True)
class ProjectionVariant:
    """同一 Slot 的完整、可独立序列化预算版本。"""

    element: XmlElement
    omitted_count: int = 0


@dataclass(frozen=True, slots=True)
class ContextSlotProjection:
    """Slot Owner 提交给 Snapshot Renderer 的不可变投影。"""

    slot: ContextSlotName
    owner: str
    variants: tuple[ProjectionVariant, ...]

    def __post_init__(self) -> None:
        """复制并冻结 Variant 序列，同时拒绝空投影。"""

        object.__setattr__(self, "variants", tuple(self.variants))
        if any(not isinstance(item, ProjectionVariant) for item in self.variants):
            raise ContextProtocolError(
                "context slot variants must contain only ProjectionVariant",
            )
        if not self.variants:
            raise ContextProtocolError("context slot requires at least one variant")


def _normalize_string_tuple(
    value: tuple[str, ...],
    *,
    field_name: Literal["security_guardrails", "additional_rules"],
) -> tuple[str, ...]:
    """拒绝字符串容器和非字符串元素后返回冻结 tuple。"""

    if isinstance(value, (str, bytes)):
        raise ContextProtocolError(
            f"runtime contract {field_name} must be a sequence of strings",
        )
    normalized = tuple(value)
    if any(not isinstance(item, str) for item in normalized):
        raise ContextProtocolError(
            f"runtime contract {field_name} must contain only strings",
        )
    return normalized


def validate_protocol_version(version: str) -> None:
    """接受 major 1 的严格 major.minor 版本字符串。"""

    match = _PROTOCOL_VERSION_PATTERN.fullmatch(version)
    if match is None or match.group(1) != "1":
        raise ContextProtocolVersionError(
            "unsupported major context protocol version",
        )
