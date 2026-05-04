from contextlib import contextmanager
from typing import Iterator, Mapping, MutableMapping

from agentos.observability.langfuse import (
    langfuse_otel_headers,
    langfuse_otel_trace_endpoint,
)
from agentos.observability.tracer import Span, TraceIds


class _OTelSpan:
    """把 OpenTelemetry span context manager 适配成 agentos Span。"""

    def __init__(self, context_manager: object) -> None:
        """保存 OTel context manager。"""

        self._context_manager = context_manager
        self._span: object | None = None

    def set_attribute(self, key: str, value: object) -> None:
        """设置 OTel span attribute。"""

        if self._span is not None:
            self._span.set_attribute(key, value)  # type: ignore[attr-defined]

    def set_attributes(self, attributes: Mapping[str, object]) -> None:
        """批量设置 OTel span attributes。"""

        for key, value in attributes.items():
            self.set_attribute(key, value)

    def add_event(
        self,
        name: str,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        """追加 OTel span event。"""

        if self._span is not None:
            self._span.add_event(name, dict(attributes or {}))  # type: ignore[attr-defined]

    def record_exception(self, error: BaseException) -> None:
        """记录 OTel exception。"""

        if self._span is not None:
            self._span.record_exception(error)  # type: ignore[attr-defined]

    def set_status(self, status: str, description: str | None = None) -> None:
        """设置 OTel span status。"""

        if self._span is None:
            return
        try:
            from opentelemetry.trace import Status, StatusCode
        except ImportError as error:
            raise RuntimeError(
                "OpenTelemetry is required. Install agent-os[observability].",
            ) from error
        status_code = StatusCode.ERROR if status == "error" else StatusCode.OK
        self._span.set_status(Status(status_code, description))  # type: ignore[attr-defined]

    def __enter__(self) -> "_OTelSpan":
        """进入 OTel span context。"""

        self._span = self._context_manager.__enter__()  # type: ignore[attr-defined]
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool | None:
        """退出 OTel span context。"""

        return self._context_manager.__exit__(exc_type, exc, tb)  # type: ignore[attr-defined]


class _OTelTracer:
    """把 OpenTelemetry tracer 适配成 agentos Tracer。"""

    def __init__(self, tracer: object, provider: object) -> None:
        """保存 OTel tracer 和 provider，防止 provider 被释放。"""

        self._tracer = tracer
        self._provider = provider

    def start_span(
        self,
        name: str,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> Span:
        """创建 OTel current span。"""

        return _OTelSpan(
            self._tracer.start_as_current_span(  # type: ignore[attr-defined]
                name,
                attributes=dict(attributes or {}),
            ),
        )

    @contextmanager
    def use_incoming_headers(
        self,
        headers: Mapping[str, str] | None,
    ) -> Iterator[None]:
        """提取 incoming W3C trace context。"""

        if not headers:
            yield
            return
        try:
            from opentelemetry import context, propagate
        except ImportError as error:
            raise RuntimeError(
                "OpenTelemetry is required. Install agent-os[observability].",
            ) from error
        extracted = propagate.extract(dict(headers))
        token = context.attach(extracted)
        try:
            yield
        finally:
            context.detach(token)

    def inject_headers(self, headers: MutableMapping[str, str]) -> None:
        """把当前 OTel context 注入 outgoing headers。"""

        try:
            from opentelemetry import propagate
        except ImportError as error:
            raise RuntimeError(
                "OpenTelemetry is required. Install agent-os[observability].",
            ) from error
        propagate.inject(headers)

    def current_trace_ids(self) -> TraceIds:
        """读取当前 OTel span context。"""

        try:
            from opentelemetry import trace
        except ImportError as error:
            raise RuntimeError(
                "OpenTelemetry is required. Install agent-os[observability].",
            ) from error
        span_context = trace.get_current_span().get_span_context()
        if not span_context.is_valid:
            return TraceIds(trace_id=None, span_id=None)
        return TraceIds(
            trace_id=format(span_context.trace_id, "032x"),
            span_id=format(span_context.span_id, "016x"),
            is_remote=getattr(span_context, "is_remote", False),
        )

    def shutdown(self) -> None:
        """关闭 OTel provider，flush pending spans。"""

        self._provider.shutdown()  # type: ignore[attr-defined]


def create_otel_tracer(
    *,
    endpoint: str,
    headers: dict[str, str] | None = None,
    service_name: str = "agentos",
    environment: str | None = None,
) -> _OTelTracer:
    """创建 OTLP HTTP tracer。"""

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as error:
        raise RuntimeError(
            "OpenTelemetry is required. Install agent-os[observability].",
        ) from error

    resource_attributes: dict[str, object] = {"service.name": service_name}
    if environment is not None:
        resource_attributes["deployment.environment.name"] = environment
    provider = TracerProvider(resource=Resource.create(resource_attributes))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, headers=headers or {}),
        ),
    )
    return _OTelTracer(provider.get_tracer("agentos"), provider)


def create_langfuse_otel_tracer(
    *,
    host: str,
    public_key: str,
    secret_key: str,
    service_name: str = "agentos",
    environment: str | None = None,
) -> _OTelTracer:
    """创建发送到 Langfuse OTLP endpoint 的 tracer。"""

    return create_otel_tracer(
        endpoint=langfuse_otel_trace_endpoint(host),
        headers=langfuse_otel_headers(public_key, secret_key),
        service_name=service_name,
        environment=environment,
    )
