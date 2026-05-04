from typing import Mapping

from agentos.observability.langfuse import (
    langfuse_otel_headers,
    langfuse_otel_trace_endpoint,
)
from agentos.observability.tracer import Span
from agentos.observability.traces import TraceRecord


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
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    except ImportError as error:
        raise RuntimeError(
            "OpenTelemetry is required. Install agent-os[observability].",
        ) from error

    resource_attributes: dict[str, object] = {"service.name": service_name}
    if environment is not None:
        resource_attributes["deployment.environment.name"] = environment
    provider = TracerProvider(resource=Resource.create(resource_attributes))
    provider.add_span_processor(
        SimpleSpanProcessor(
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


class OTelAdapter:
    """通过注入 tracer 输出 TraceRecord，不引入 opentelemetry 依赖。"""

    def __init__(self, tracer: object) -> None:
        """创建 adapter，tracer 需提供 start_span(name)。"""

        self._tracer = tracer

    def record_many(self, records: list[TraceRecord]) -> None:
        """输出一组 trace records。"""

        for record in records:
            span = self._tracer.start_span(record.name)
            for key, value in record.attributes.items():
                span.set_attribute(key, value)
            span.set_attribute("trace.id", record.trace_id)
            span.set_attribute("span.id", record.span_id)
            if record.session_id is not None:
                span.set_attribute("session.id", record.session_id)
            if record.turn_id is not None:
                span.set_attribute("turn.id", record.turn_id)
            span.end()
