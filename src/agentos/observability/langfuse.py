import base64

from agentos.observability.traces import TraceRecord


def langfuse_otel_trace_endpoint(host: str) -> str:
    """返回 Langfuse OTLP HTTP trace endpoint。"""

    return f"{host.rstrip('/')}/api/public/otel/v1/traces"


def langfuse_otel_headers(public_key: str, secret_key: str) -> dict[str, str]:
    """返回 Langfuse OTLP 需要的认证 headers。"""

    auth = base64.b64encode(
        f"{public_key}:{secret_key}".encode("utf-8"),
    ).decode("ascii")
    return {
        "Authorization": f"Basic {auth}",
        "x-langfuse-ingestion-version": "4",
    }


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
