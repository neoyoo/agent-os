"""ContextSnapshot 的校验、排序、预算选择与安全渲染。"""

from __future__ import annotations

from dataclasses import dataclass

from agentos.context.models import (
    CONTEXT_PROTOCOL,
    CONTEXT_PROTOCOL_VERSION,
    ContextBudgetExceededError,
    ContextProtocolError,
    ContextSlotProjection,
    ContextSnapshot,
    ProjectionVariant,
)
from agentos.context.registry import (
    CONTEXT_SLOT_REGISTRY,
    ContextExtensionRegistry,
    validate_slot_owner,
)
from agentos.context.sensitive import SensitiveRepresentationValidator
from agentos.context.xml import XmlElement, render_xml
from agentos.tokens import TokenCounter

_ROOT_ATTRIBUTES = (
    ("protocol", CONTEXT_PROTOCOL),
    ("version", CONTEXT_PROTOCOL_VERSION),
    ("origin", "runtime"),
    ("authority", "context-data"),
    ("persistence", "ephemeral"),
    ("visibility", "internal"),
)


@dataclass(frozen=True, slots=True)
class SnapshotBudget:
    """一次 ContextSnapshot 渲染可使用的 Token 预算。"""

    max_tokens: int
    critical_only: bool = False

    def __post_init__(self) -> None:
        """拒绝 bool、非整数、负数和非 bool 模式标志。"""

        if (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens < 0
            or type(self.critical_only) is not bool
        ):
            raise ContextProtocolError("snapshot budget is invalid")

    @classmethod
    def from_request_budget(cls, remaining_input_tokens: int) -> SnapshotBudget:
        """按 Context Protocol v1 固定比例生成 Snapshot 预算。"""

        if (
            isinstance(remaining_input_tokens, bool)
            or not isinstance(remaining_input_tokens, int)
            or remaining_input_tokens < 0
        ):
            raise ContextProtocolError("remaining input token budget is invalid")
        return cls(
            max_tokens=min(remaining_input_tokens * 25 // 100, 12_000),
            critical_only=remaining_input_tokens < 2_048,
        )


class ContextSnapshotRenderer:
    """校验 Slot Projection 并生成确定性的 ContextSnapshot。"""

    __slots__ = ("_extension_registry", "_token_counter")

    def __init__(
        self,
        token_counter: TokenCounter,
        extension_registry: ContextExtensionRegistry | None = None,
    ) -> None:
        """注入 TokenCounter 和可选 Extension Registry。"""

        if extension_registry is not None and not isinstance(
            extension_registry,
            ContextExtensionRegistry,
        ):
            raise ContextProtocolError("context extension registry is invalid")
        self._token_counter = token_counter
        self._extension_registry = extension_registry

    def render(
        self,
        projections: object,
        budget: SnapshotBudget | None = None,
    ) -> ContextSnapshot:
        """验证、排序并序列化一组完整 Slot Projection。"""

        if budget is not None and type(budget) is not SnapshotBudget:
            raise ContextProtocolError("snapshot budget is invalid")
        normalized = self._normalize_projections(projections)
        if budget is not None:
            return self._render_budgeted(normalized, budget)
        children = tuple(item.variants[0].element for item in normalized)
        return ContextSnapshot(xml=self._render_children(children))

    def _render_budgeted(
        self,
        projections: tuple[ContextSlotProjection, ...],
        budget: SnapshotBudget,
    ) -> ContextSnapshot:
        selected: dict[str, int | None] = {
            item.slot: (
                0
                if not budget.critical_only
                or CONTEXT_SLOT_REGISTRY[item.slot].critical_below_2048
                else None
            )
            for item in projections
        }
        while True:
            xml = self._render_selection(projections, selected)
            if self._count_tokens(xml) <= budget.max_tokens:
                return ContextSnapshot(xml=xml)
            candidate = self._next_trim_candidate(projections, selected)
            if candidate is None:
                raise ContextBudgetExceededError(
                    "context snapshot exceeds token budget",
                )
            current = selected[candidate.slot]
            if current is None:
                raise ContextProtocolError("snapshot trim state is invalid")
            if current + 1 < len(candidate.variants):
                selected[candidate.slot] = current + 1
            else:
                selected[candidate.slot] = None

    def _next_trim_candidate(
        self,
        projections: tuple[ContextSlotProjection, ...],
        selected: dict[str, int | None],
    ) -> ContextSlotProjection | None:
        candidates = []
        for item in projections:
            current = selected[item.slot]
            if current is None:
                continue
            spec = CONTEXT_SLOT_REGISTRY[item.slot]
            if current + 1 < len(item.variants) or not spec.critical_below_2048:
                candidates.append(item)
        return min(
            candidates,
            key=lambda item: CONTEXT_SLOT_REGISTRY[item.slot].trim_rank,
            default=None,
        )

    def _render_selection(
        self,
        projections: tuple[ContextSlotProjection, ...],
        selected: dict[str, int | None],
    ) -> str:
        children: list[XmlElement] = []
        for item in projections:
            index = selected[item.slot]
            if index is None:
                remaining = len(item.variants[0].element.children)
                truncated = True
            else:
                variant = item.variants[index]
                children.append(variant.element)
                remaining = variant.omitted_count
                truncated = index > 0 or remaining > 0
            if truncated:
                children.append(_truncation_marker(item.slot, remaining))
        return self._render_children(tuple(children))

    def _count_tokens(self, xml: str) -> int:
        failed = False
        try:
            count = self._token_counter.count_text(xml)
        except Exception:
            failed = True
            count = None
        if failed:
            raise ContextProtocolError("token counter failed")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ContextProtocolError("token counter returned invalid count")
        return count

    def _normalize_projections(
        self,
        projections: object,
    ) -> tuple[ContextSlotProjection, ...]:
        if isinstance(projections, (str, bytes)):
            raise ContextProtocolError("context slot projection is invalid")
        failed = False
        try:
            submitted = tuple(projections)  # type: ignore[arg-type]
        except Exception:
            failed = True
            submitted = ()
        if failed:
            raise ContextProtocolError("context slot projection is invalid")

        by_slot: dict[str, ContextSlotProjection] = {}
        for item in submitted:
            if type(item) is not ContextSlotProjection:
                raise ContextProtocolError("context slot projection is invalid")
            if item.slot in by_slot:
                raise ContextProtocolError("duplicate context slot")
            validate_slot_owner(item.slot, item.owner)
            self._validate_variants(item)
            by_slot[item.slot] = item
        return tuple(
            item
            for slot in CONTEXT_SLOT_REGISTRY
            if (item := by_slot.get(slot)) is not None
        )

    def _validate_variants(self, projection: ContextSlotProjection) -> None:
        if type(projection.variants) is not tuple or not projection.variants:
            raise ContextProtocolError("projection variant is invalid")
        if projection.slot == "declared-schema" and (
            len(projection.variants) != 1
            or projection.variants[0].omitted_count != 0
        ):
            raise ContextProtocolError(
                "declared-schema requires exactly one complete variant",
            )
        previous_omitted = 0
        for variant in projection.variants:
            if type(variant) is not ProjectionVariant:
                raise ContextProtocolError("projection variant is invalid")
            if type(variant.element) is not XmlElement:
                raise ContextProtocolError("projection variant element is invalid")
            if variant.element.tag != projection.slot:
                raise ContextProtocolError("context slot root tag mismatch")
            if (
                isinstance(variant.omitted_count, bool)
                or not isinstance(variant.omitted_count, int)
                or variant.omitted_count < 0
            ):
                raise ContextProtocolError("projection omitted count is invalid")
            if (
                variant is projection.variants[0]
                and variant.omitted_count != 0
                or variant.omitted_count < previous_omitted
            ):
                raise ContextProtocolError("projection omitted counts are invalid")
            previous_omitted = variant.omitted_count
            SensitiveRepresentationValidator().validate(
                variant.element,
                slot=projection.slot,
            )
            self._render_children((variant.element,))
            if projection.slot == "extensions":
                self._validate_extensions(variant.element)

    def _validate_extensions(self, element: XmlElement) -> None:
        registry = self._extension_registry
        if registry is None:
            return
        namespaces = tuple(
            dict(child.attributes)["namespace"] for child in element.children
        )
        if len(namespaces) != len(set(namespaces)):
            raise ContextProtocolError("duplicate extension namespace")
        for child, namespace in zip(element.children, namespaces, strict=True):
            isolated = XmlElement("extensions", children=(child,))
            if self._count_tokens(self._render_children((isolated,))) > (
                registry.get(namespace).max_tokens
            ):
                raise ContextBudgetExceededError(
                    "context extension exceeds token budget",
                )

    def _render_children(self, children: tuple[XmlElement, ...]) -> str:
        specs = self._extension_registry.specs if self._extension_registry else None
        return render_xml(
            XmlElement(
                tag="context-snapshot",
                attributes=_ROOT_ATTRIBUTES,
                children=children,
            ),
            extension_specs=specs,
        )


def _truncation_marker(slot: str, remaining: int) -> XmlElement:
    return XmlElement(
        "truncated",
        (
            ("slot", slot),
            ("reason", "token-budget"),
            ("remaining", str(remaining)),
        ),
    )
