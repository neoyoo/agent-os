import base64


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
