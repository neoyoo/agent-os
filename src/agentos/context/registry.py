"""Context Protocol v1 的固定 Registry、Owner 和扩展注册边界。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from inspect import getattr_static, isfunction, signature
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from agentos.context.models import (
    ContextProtocolError,
    ContextSlotName,
    RuntimeContract,
    RuntimeDirective,
    SystemSectionName,
    TrustedSkillInstruction,
)

if TYPE_CHECKING:
    from agentos.context.xml import XmlTagSpec


@dataclass(frozen=True, slots=True)
class SystemSectionSpec:
    """固定 System Section 的 Owner、基数与信任元数据。"""

    owner: str
    cardinality: Literal["1", "0..1", "0..N"]
    required: bool
    trust: Literal["trusted"] = "trusted"


@dataclass(frozen=True, slots=True)
class ContextSlotSpec:
    """固定 Context Slot 的 Owner 与裁剪元数据。"""

    owner: str
    cardinality: Literal["0..1"] = "0..1"
    required: bool = False
    trim_rank: int = 0
    critical_below_2048: bool = False


SYSTEM_SECTION_REGISTRY: Mapping[SystemSectionName, SystemSectionSpec] = (
    MappingProxyType(
        {
            "runtime_contract": SystemSectionSpec("RuntimePolicy", "1", True),
            "interaction_protocol": SystemSectionSpec("RuntimePolicy", "1", True),
            "context_management_rules": SystemSectionSpec(
                "ContextProtocol",
                "1",
                True,
            ),
            "runtime_directives": SystemSectionSpec(
                "RuntimeDirectiveRuntime",
                "0..1",
                False,
            ),
            "trusted_skill_instructions": SystemSectionSpec(
                "SkillRuntime",
                "0..N",
                False,
            ),
            "workspace_contract": SystemSectionSpec(
                "WorkspaceRuntime",
                "0..1",
                False,
            ),
        },
    )
)

CONTEXT_SLOT_REGISTRY: Mapping[ContextSlotName, ContextSlotSpec] = MappingProxyType(
    {
        "declared-schema": ContextSlotSpec(
            "ContextRuntime",
            trim_rank=90,
            critical_below_2048=True,
        ),
        "working-state": ContextSlotSpec(
            "ContextRuntime",
            trim_rank=80,
            critical_below_2048=True,
        ),
        "active-plan": ContextSlotSpec(
            "PlannerRuntime",
            trim_rank=100,
            critical_below_2048=True,
        ),
        "inherited-state": ContextSlotSpec("ChapterRuntime", trim_rank=50),
        "compressed-history": ContextSlotSpec(
            "CompressionRuntime",
            trim_rank=30,
        ),
        "memory-context": ContextSlotSpec("MemoryRuntime", trim_rank=20),
        "available-skills": ContextSlotSpec("SkillRuntime", trim_rank=10),
        "artifact-catalog": ContextSlotSpec("ArtifactRuntime", trim_rank=40),
        "extensions": ContextSlotSpec("ContextExtensionRegistry", trim_rank=0),
    },
)


def validate_slot_owner(slot: str, owner: str) -> None:
    """拒绝未知 Slot 或与固定 Registry 不一致的 Owner。"""

    spec = CONTEXT_SLOT_REGISTRY.get(slot)  # type: ignore[arg-type]
    if spec is None:
        raise ContextProtocolError("unknown context slot")
    if owner != spec.owner:
        raise ContextProtocolError("context slot owner mismatch")


@runtime_checkable
class RuntimeContractProvider(Protocol):
    """只提供 RuntimePolicy 拥有的 RuntimeContract。"""

    def runtime_contract(self) -> RuntimeContract:
        """返回结构化 Runtime Contract。"""


@runtime_checkable
class InteractionProtocolProvider(Protocol):
    """只提供 RuntimePolicy 拥有的 Interaction Protocol 正文。"""

    def interaction_protocol(self) -> str:
        """返回 Interaction Protocol 正文。"""


@runtime_checkable
class ContextManagementRulesProvider(Protocol):
    """只提供 ContextProtocol 拥有的 Context Management Rules。"""

    def context_management_rules(self) -> str:
        """返回 Context Management Rules 正文。"""


@runtime_checkable
class RuntimeDirectiveProvider(Protocol):
    """只提供 RuntimeDirectiveRuntime 产生的受控指令。"""

    def runtime_directives(self) -> tuple[RuntimeDirective, ...]:
        """按稳定顺序返回 Runtime Directive。"""


@runtime_checkable
class TrustedSkillInstructionProvider(Protocol):
    """只提供 SkillRuntime 验证后的 Skill 指令。"""

    def items(self) -> tuple[TrustedSkillInstruction, ...]:
        """按稳定注册顺序返回受信 Skill 指令。"""


@runtime_checkable
class WorkspaceContractProvider(Protocol):
    """只提供 WorkspaceRuntime 拥有的 Workspace Contract。"""

    def workspace_contract(self) -> str:
        """返回 Workspace Contract 正文。"""


@dataclass(frozen=True, slots=True, init=False)
class SystemSectionRegistry:
    """保存通过 owner-specific Protocol 注入的可信 Section Provider。"""

    _runtime_contract_provider: RuntimeContractProvider | None = None
    _interaction_protocol_provider: InteractionProtocolProvider | None = None
    _context_management_rules_provider: ContextManagementRulesProvider | None = None
    _runtime_directives_provider: RuntimeDirectiveProvider | None = None
    _trusted_skill_instructions_provider: (
        TrustedSkillInstructionProvider | None
    ) = None
    _workspace_contract_provider: WorkspaceContractProvider | None = None

    def __init__(self) -> None:
        """创建不能从构造参数注入 Provider 的空 Registry。"""

        object.__setattr__(self, "_runtime_contract_provider", None)
        object.__setattr__(self, "_interaction_protocol_provider", None)
        object.__setattr__(self, "_context_management_rules_provider", None)
        object.__setattr__(self, "_runtime_directives_provider", None)
        object.__setattr__(self, "_trusted_skill_instructions_provider", None)
        object.__setattr__(self, "_workspace_contract_provider", None)

    @classmethod
    def from_trusted_providers(
        cls,
        *,
        runtime_contract: RuntimeContractProvider | None = None,
        interaction_protocol: InteractionProtocolProvider | None = None,
        context_management_rules: ContextManagementRulesProvider | None = None,
        runtime_directives: RuntimeDirectiveProvider | None = None,
        trusted_skill_instructions: TrustedSkillInstructionProvider | None = None,
        workspace_contract: WorkspaceContractProvider | None = None,
    ) -> SystemSectionRegistry:
        """校验并保存六类可信 Owner Provider，不读取其内容。"""

        registrations = (
            (
                "_runtime_contract_provider",
                runtime_contract,
                "runtime_contract",
                "runtime contract provider does not satisfy protocol",
            ),
            (
                "_interaction_protocol_provider",
                interaction_protocol,
                "interaction_protocol",
                "interaction protocol provider does not satisfy protocol",
            ),
            (
                "_context_management_rules_provider",
                context_management_rules,
                "context_management_rules",
                "context management rules provider does not satisfy protocol",
            ),
            (
                "_runtime_directives_provider",
                runtime_directives,
                "runtime_directives",
                "runtime directive provider does not satisfy protocol",
            ),
            (
                "_trusted_skill_instructions_provider",
                trusted_skill_instructions,
                "items",
                "trusted skill provider does not satisfy protocol",
            ),
            (
                "_workspace_contract_provider",
                workspace_contract,
                "workspace_contract",
                "workspace contract provider does not satisfy protocol",
            ),
        )
        registry = cls()
        for attribute, provider, method_name, message in registrations:
            _validate_provider(provider, method_name, message)
            object.__setattr__(registry, attribute, provider)
        return registry

    @property
    def runtime_contract_provider(self) -> RuntimeContractProvider | None:
        """返回 Runtime Contract Provider。"""

        return self._runtime_contract_provider

    @property
    def interaction_protocol_provider(self) -> InteractionProtocolProvider | None:
        """返回 Interaction Protocol Provider。"""

        return self._interaction_protocol_provider

    @property
    def context_management_rules_provider(
        self,
    ) -> ContextManagementRulesProvider | None:
        """返回 Context Management Rules Provider。"""

        return self._context_management_rules_provider

    @property
    def runtime_directives_provider(self) -> RuntimeDirectiveProvider | None:
        """返回 Runtime Directive Provider。"""

        return self._runtime_directives_provider

    @property
    def trusted_skill_instructions_provider(
        self,
    ) -> TrustedSkillInstructionProvider | None:
        """返回 Trusted Skill Instruction Provider。"""

        return self._trusted_skill_instructions_provider

    @property
    def workspace_contract_provider(self) -> WorkspaceContractProvider | None:
        """返回 Workspace Contract Provider。"""

        return self._workspace_contract_provider

    @property
    def registered_sections(self) -> tuple[SystemSectionName, ...]:
        """按固定协议顺序返回当前已注册 Section。"""

        providers = (
            self._runtime_contract_provider,
            self._interaction_protocol_provider,
            self._context_management_rules_provider,
            self._runtime_directives_provider,
            self._trusted_skill_instructions_provider,
            self._workspace_contract_provider,
        )
        return tuple(
            section
            for section, provider in zip(SYSTEM_SECTION_REGISTRY, providers, strict=True)
            if provider is not None
        )


def _validate_provider(
    provider: object | None,
    method_name: str,
    message: str,
) -> None:
    """校验 owner-specific Provider 的 bound zero-argument 方法形状。"""

    if provider is None:
        return
    try:
        method = getattr_static(provider, method_name)
        class_method = getattr_static(type(provider), method_name)
    except (AttributeError, TypeError):
        method = None
        class_method = None
    if (
        not isfunction(method)
        or method is not class_method
        or "__signature__" in method.__dict__
    ):
        raise ContextProtocolError(message) from None
    try:
        signature(method, follow_wrapped=False).bind(provider)
    except (TypeError, ValueError):
        valid_signature = False
    else:
        valid_signature = True
    if not valid_signature:
        raise ContextProtocolError(message) from None


@dataclass(frozen=True, slots=True)
class SystemEnvelopeBudgetPolicy:
    """SystemEnvelope 各 Section 的不可变 Token 上限。"""

    runtime_contract: int = 4_000
    interaction_protocol: int = 2_000
    context_management_rules: int = 4_000
    runtime_directives: int = 512
    trusted_skill_per_item: int = 4_000
    trusted_skill_total: int = 12_000
    workspace_contract: int = 6_000

    def __post_init__(self) -> None:
        """拒绝 bool、非整数和非正预算。"""

        values = (
            self.runtime_contract,
            self.interaction_protocol,
            self.context_management_rules,
            self.runtime_directives,
            self.trusted_skill_per_item,
            self.trusted_skill_total,
            self.workspace_contract,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in values
        ):
            raise ContextProtocolError(
                "system envelope budget values must be positive integers",
            )


_NAMESPACE_PATTERN = re.compile(r"[a-z][a-z0-9]*(\.[a-z][a-z0-9-]*)+")
_XML_NAME_PATTERN = re.compile(
    r"[:A-Z_a-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u02ff"
    r"\u0370-\u037d\u037f-\u1fff\u200c-\u200d\u2070-\u218f"
    r"\u2c00-\u2fef\u3001-\ud7ff\uf900-\ufdcf\ufdf0-\ufffd"
    r"\U00010000-\U000EFFFF]"
    r"[:A-Z_a-z\-.0-9\u00b7\u00c0-\u00d6\u00d8-\u00f6"
    r"\u00f8-\u037d\u037f-\u1fff\u200c-\u200d\u203f-\u2040"
    r"\u2070-\u218f\u2c00-\u2fef\u3001-\ud7ff\uf900-\ufdcf"
    r"\ufdf0-\ufffd\U00010000-\U000EFFFF]*",
)
_RESERVED_EXTENSION_TAGS = frozenset(
    {
        "context-snapshot",
        *CONTEXT_SLOT_REGISTRY,
        "xml",
        "xmlns",
    },
)


@dataclass(frozen=True, slots=True)
class ContextExtensionSpec:
    """一个 Extension namespace 的不可变 Schema 与预算元数据。"""

    namespace: str
    owner: str
    version: str
    tag_schemas: Mapping[tuple[str, ...], XmlTagSpec]
    max_tokens: int
    trim_rank: int

    def __post_init__(self) -> None:
        """校验元数据并防御性复制完整 tag path Mapping。"""

        if not isinstance(self.namespace, str) or _NAMESPACE_PATTERN.fullmatch(
            self.namespace,
        ) is None:
            raise ContextProtocolError("extension namespace is invalid")
        if not isinstance(self.owner, str) or not self.owner.strip():
            raise ContextProtocolError("extension owner must be a non-empty string")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ContextProtocolError("extension version must be a non-empty string")
        if not isinstance(self.tag_schemas, Mapping) or not self.tag_schemas:
            raise ContextProtocolError("extension tag_schemas must not be empty")
        if (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens <= 0
        ):
            raise ContextProtocolError(
                "extension max_tokens must be a positive integer",
            )
        if isinstance(self.trim_rank, bool) or not isinstance(self.trim_rank, int):
            raise ContextProtocolError("extension trim_rank must be an integer")

        copied = dict(self.tag_schemas)
        seen_paths: set[tuple[str, ...]] = set()
        for path in copied:
            if not _valid_extension_path(path):
                raise ContextProtocolError(
                    "extension tag schema path is invalid or reserved",
                )
            folded = tuple(segment.casefold() for segment in path)
            if folded in seen_paths:
                raise ContextProtocolError("duplicate extension tag schema path")
            seen_paths.add(folded)
        object.__setattr__(self, "tag_schemas", MappingProxyType(copied))


def _valid_extension_path(path: object) -> bool:
    """判断完整 Extension tag path 是否为非保留 XML Name tuple。"""

    if not isinstance(path, tuple) or not path:
        return False
    for segment in path:
        if not isinstance(segment, str) or _XML_NAME_PATTERN.fullmatch(segment) is None:
            return False
        if segment.casefold() in _RESERVED_EXTENSION_TAGS:
            return False
    return True


class ContextExtensionRegistry:
    """按注册顺序保存 namespace 到 ContextExtensionSpec 的唯一映射。"""

    __slots__ = ("_specs",)

    def __init__(self) -> None:
        self._specs: dict[str, ContextExtensionSpec] = {}

    def register(self, spec: ContextExtensionSpec) -> None:
        """注册一个类型化 Extension Spec，拒绝重复 namespace。"""

        if not isinstance(spec, ContextExtensionSpec):
            raise ContextProtocolError("extension registry requires spec")
        if spec.namespace in self._specs:
            raise ContextProtocolError("extension namespace is already registered")
        self._specs[spec.namespace] = spec

    def get(self, namespace: str) -> ContextExtensionSpec:
        """读取已注册 Extension，未知 namespace 使用稳定错误。"""

        if not isinstance(namespace, str) or namespace not in self._specs:
            raise ContextProtocolError(
                "extension namespace is not registered",
            ) from None
        return self._specs[namespace]

    @property
    def specs(self) -> Mapping[str, ContextExtensionSpec]:
        """返回不会随后续注册漂移的只读有序快照。"""

        return MappingProxyType(dict(self._specs))
