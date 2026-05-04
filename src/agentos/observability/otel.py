from agentos.observability.traces import TraceRecord


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
