from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import re
from typing import ContextManager, Iterator, Mapping, MutableMapping, Protocol
from uuid import uuid4

_TRACEPARENT_PATTERN = re.compile(
    r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$",
)


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


@dataclass(frozen=True, slots=True)
class TraceIds:
    """当前 span 的 trace/span ids。"""

    trace_id: str | None
    span_id: str | None
    is_remote: bool = False


class TraceContextPropagator(Protocol):
    """trace context extract/inject 边界。"""

    def use_incoming_headers(
        self,
        headers: Mapping[str, str] | None,
    ) -> ContextManager[None]:
        """把 incoming headers 提取为当前 trace context。"""

    def inject_headers(self, headers: MutableMapping[str, str]) -> None:
        """把当前 trace context 写入 outgoing headers。"""

    def current_trace_ids(self) -> TraceIds:
        """返回当前 active span ids。"""


class Tracer(TraceContextPropagator, Protocol):
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
    trace_id: str
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

    @contextmanager
    def use_incoming_headers(
        self,
        headers: Mapping[str, str] | None,
    ) -> Iterator[None]:
        """忽略 incoming headers。"""

        yield

    def inject_headers(self, headers: MutableMapping[str, str]) -> None:
        """不写 outgoing headers。"""

    def current_trace_ids(self) -> TraceIds:
        """返回空 trace ids。"""

        return TraceIds(trace_id=None, span_id=None)


class InMemoryTracer:
    """记录 span 树的测试 tracer。"""

    def __init__(self) -> None:
        """创建空 span 记录器。"""

        self.records: list[InMemorySpanRecord] = []
        self._current_trace_id: ContextVar[str | None] = ContextVar(
            "agentos_current_trace_id",
            default=None,
        )
        self._current_span_id: ContextVar[str | None] = ContextVar(
            "agentos_current_span_id",
            default=None,
        )
        self._incoming_trace_id: ContextVar[str | None] = ContextVar(
            "agentos_incoming_trace_id",
            default=None,
        )
        self._incoming_parent_span_id: ContextVar[str | None] = ContextVar(
            "agentos_incoming_parent_span_id",
            default=None,
        )

    def start_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> "InMemorySpan":
        """创建 in-memory span。"""

        trace_id = (
            self._current_trace_id.get()
            or self._incoming_trace_id.get()
            or uuid4().hex
        )
        record = InMemorySpanRecord(
            name=name,
            trace_id=trace_id,
            span_id=uuid4().hex[:16],
            parent_span_id=(
                self._current_span_id.get()
                or self._incoming_parent_span_id.get()
            ),
            attributes=dict(attributes or {}),
        )
        self.records.append(record)
        return InMemorySpan(record, self._current_trace_id, self._current_span_id)

    @contextmanager
    def use_incoming_headers(
        self,
        headers: Mapping[str, str] | None,
    ) -> Iterator[None]:
        """提取 W3C traceparent 作为当前 incoming trace context。"""

        trace_id: str | None = None
        parent_span_id: str | None = None
        traceparent = None if headers is None else headers.get("traceparent")
        if traceparent is not None:
            match = _TRACEPARENT_PATTERN.match(traceparent)
            if match is not None:
                trace_id = match.group(1)
                parent_span_id = match.group(2)
        trace_token = self._incoming_trace_id.set(trace_id)
        parent_token = self._incoming_parent_span_id.set(parent_span_id)
        try:
            yield
        finally:
            self._incoming_trace_id.reset(trace_token)
            self._incoming_parent_span_id.reset(parent_token)

    def inject_headers(self, headers: MutableMapping[str, str]) -> None:
        """把当前 span ids 写入 W3C traceparent header。"""

        ids = self.current_trace_ids()
        if ids.trace_id is None or ids.span_id is None:
            return
        headers["traceparent"] = f"00-{ids.trace_id}-{ids.span_id}-01"

    def current_trace_ids(self) -> TraceIds:
        """返回当前 active span ids。"""

        return TraceIds(
            trace_id=self._current_trace_id.get(),
            span_id=self._current_span_id.get(),
        )


class InMemorySpan:
    """InMemoryTracer 创建的 span context manager。"""

    def __init__(
        self,
        record: InMemorySpanRecord,
        current_trace_id: ContextVar[str | None],
        current_span_id: ContextVar[str | None],
    ) -> None:
        """保存 span record 和当前 trace/span contextvars。"""

        self._record = record
        self._current_trace_id = current_trace_id
        self._current_span_id = current_span_id
        self._trace_token: object | None = None
        self._span_token: object | None = None

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
        """把当前 trace/span ids 压入 context。"""

        self._trace_token = self._current_trace_id.set(self._record.trace_id)
        self._span_token = self._current_span_id.set(self._record.span_id)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool | None:
        """退出 span context，并记录异常状态。"""

        if isinstance(exc, BaseException):
            self.record_exception(exc)
            self.set_status("error", str(exc))
        elif self._record.status == "unset":
            self.set_status("ok")
        if self._span_token is not None:
            self._current_span_id.reset(self._span_token)  # type: ignore[arg-type]
        if self._trace_token is not None:
            self._current_trace_id.reset(self._trace_token)  # type: ignore[arg-type]
        return None
