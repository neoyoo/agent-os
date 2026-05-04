from agentos.observability.traces import TraceRecord


class LangfuseAdapter:
    """通过注入 client 输出 TraceRecord，不引入 Langfuse 依赖。"""

    def __init__(self, client: object) -> None:
        """创建 adapter，client 需提供 trace(**kwargs)。"""

        self._client = client

    def record_many(self, records: list[TraceRecord]) -> None:
        """输出一组 trace records。"""

        for record in records:
            self._client.trace(
                name=record.name,
                session_id=record.session_id,
                metadata={
                    **record.attributes,
                    "trace.id": record.trace_id,
                    "span.id": record.span_id,
                    "turn.id": record.turn_id,
                },
            )
