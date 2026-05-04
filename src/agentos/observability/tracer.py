from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Mapping, Protocol
from uuid import uuid4


class Span(Protocol):
    """agentos 内部 span 边界，不依赖 OpenTelemetry 类型。"""

    def set_attribute(self, key: str, value: object) -> None:
        """设置一个 span attribute。"""

    def set_attributes(self, attributes: Mapping[str, object]) -> None:
        """批量设置 span attributes。"""

    def add_event(
        self,
        name: str,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        """追加 span event。"""

    def record_exception(self, error: BaseException) -> None:
        """记录异常 event。"""

    def set_status(self, status: str, description: str | None = None) -> None:
        """设置 span 状态。"""

    def __enter__(self) -> "Span":
        """进入 span context。"""

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool | None:
        """退出 span context。"""


class Tracer(Protocol):
    """agentos 内部 tracer 边界。"""

    def start_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> Span:
        """创建 span。"""


@dataclass(frozen=True, slots=True)
class InMemorySpanEvent:
    """测试用 span event 记录。"""

    name: str
    attributes: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class InMemorySpanRecord:
    """测试用 span 记录。"""

    name: str
    span_id: str
    parent_span_id: str | None
    attributes: dict[str, object] = field(default_factory=dict)
    events: list[InMemorySpanEvent] = field(default_factory=list)
    status: str = "unset"
    status_description: str | None = None


class NoOpSpan:
    """不记录任何数据的 span。"""

    def set_attribute(self, key: str, value: object) -> None:
        """忽略 attribute。"""

    def set_attributes(self, attributes: Mapping[str, object]) -> None:
        """忽略 attributes。"""

    def add_event(
        self,
        name: str,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        """忽略 event。"""

    def record_exception(self, error: BaseException) -> None:
        """忽略异常。"""

    def set_status(self, status: str, description: str | None = None) -> None:
        """忽略状态。"""

    def __enter__(self) -> "NoOpSpan":
        """返回自身。"""

        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool | None:
        """不吞异常。"""

        return None


class NoOpTracer:
    """不产生任何 span 的 tracer。"""

    def start_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> NoOpSpan:
        """返回 no-op span。"""

        return NoOpSpan()


class InMemoryTracer:
    """记录 span 树的测试 tracer。"""

    def __init__(self) -> None:
        """创建空 span 记录器。"""

        self.records: list[InMemorySpanRecord] = []
        self._current_span_id: ContextVar[str | None] = ContextVar(
            "agentos_current_span_id",
            default=None,
        )

    def start_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> "InMemorySpan":
        """创建 in-memory span。"""

        record = InMemorySpanRecord(
            name=name,
            span_id=uuid4().hex[:16],
            parent_span_id=self._current_span_id.get(),
            attributes=dict(attributes or {}),
        )
        self.records.append(record)
        return InMemorySpan(record, self._current_span_id)


class InMemorySpan:
    """InMemoryTracer 创建的 span context manager。"""

    def __init__(
        self,
        record: InMemorySpanRecord,
        current_span_id: ContextVar[str | None],
    ) -> None:
        """保存 span record 和当前 span contextvar。"""

        self._record = record
        self._current_span_id = current_span_id
        self._token: object | None = None

    def set_attribute(self, key: str, value: object) -> None:
        """设置一个 span attribute。"""

        self._record.attributes[key] = value

    def set_attributes(self, attributes: Mapping[str, object]) -> None:
        """批量设置 span attributes。"""

        self._record.attributes.update(dict(attributes))

    def add_event(
        self,
        name: str,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        """追加 span event。"""

        self._record.events.append(
            InMemorySpanEvent(name=name, attributes=dict(attributes or {})),
        )

    def record_exception(self, error: BaseException) -> None:
        """记录异常 event。"""

        self.add_event(
            "exception",
            {
                "exception.type": type(error).__name__,
                "exception.message": str(error),
            },
        )

    def set_status(self, status: str, description: str | None = None) -> None:
        """设置 span 状态。"""

        self._record.status = status
        self._record.status_description = description

    def __enter__(self) -> "InMemorySpan":
        """把当前 span id 压入 context。"""

        self._token = self._current_span_id.set(self._record.span_id)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool | None:
        """退出 span context，并记录异常状态。"""

        if isinstance(exc, BaseException):
            self.record_exception(exc)
            self.set_status("error", str(exc))
        elif self._record.status == "unset":
            self.set_status("ok")
        if self._token is not None:
            self._current_span_id.reset(self._token)  # type: ignore[arg-type]
        return None
