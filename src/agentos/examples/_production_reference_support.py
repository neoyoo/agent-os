from __future__ import annotations

from collections.abc import Mapping, Sequence

from agentos.channels import (
    InMemorySseEventBuffer,
    InMemorySseTurnControlStore,
    RedisSseEventBuffer,
    RedisSseTurnControlStore,
)
from agentos.deployment_profiles import DeploymentLiveBackendVerificationProfile
from agentos.deployment_types import BackendVerificationRecord
from agentos.multi import AgentCard


def reference_sse_event_buffer(
    lease_store: object,
    *,
    stream_resume_evidence_ready: bool = False,
) -> object:
    """按 Lease Store 选择 Reference SSE Event Buffer。"""

    redis_url = getattr(lease_store, "backend_url", None)
    redis_client = getattr(lease_store, "_client", None)
    if isinstance(redis_url, str) and redis_url:
        buffer = RedisSseEventBuffer(redis_url, client=redis_client)
        if stream_resume_evidence_ready:
            buffer.agentos_shared_backend_evidenced = True
            buffer.agentos_cross_node_resume_evidenced = True
        return buffer
    return InMemorySseEventBuffer()


def reference_sse_turn_control(
    lease_store: object,
    *,
    stream_resume_evidence_ready: bool = False,
) -> object:
    """按 Lease Store 选择 Reference SSE Turn Control Store。"""

    redis_url = getattr(lease_store, "backend_url", None)
    redis_client = getattr(lease_store, "_client", None)
    if isinstance(redis_url, str) and redis_url:
        store = RedisSseTurnControlStore(redis_url, client=redis_client)
        if stream_resume_evidence_ready:
            store.agentos_shared_backend_evidenced = True
        return store
    return InMemorySseTurnControlStore()


def contains_reference_no_network_fixture(value: object) -> bool:
    """判断 Adapter 是否持有 Reference 无网络 Fixture。"""

    if bool(getattr(value, "agentos_reference_no_network", False)):
        return True
    if value.__class__.__name__ in {
        "ReferenceNacosClient",
        "ReferenceRedisClient",
        "ReferencePostgresConnection",
    }:
        return True
    for attribute_name in (
        "_client",
        "client",
        "_connection",
        "connection",
        "_pool",
        "pool",
        "_active_connection",
    ):
        nested = getattr(value, attribute_name, None)
        if nested is not None and bool(
            getattr(nested, "agentos_reference_no_network", False),
        ):
            return True
    return False


def backend_verification_profile(
    *,
    records: Sequence[BackendVerificationRecord] | None = None,
) -> DeploymentLiveBackendVerificationProfile:
    """从可选记录构建 Backend Verification Profile。"""

    if records is None:
        return DeploymentLiveBackendVerificationProfile(records=())
    return DeploymentLiveBackendVerificationProfile(
        records=tuple(records),
    )


def reference_agent_card() -> dict[str, object]:
    """返回 Production Reference Agent Card 的 JSON-safe 投影。"""

    card = AgentCard(
        agent_id="production-reference-web-agent",
        name="Production Reference Web Agent",
        description="AgentOS production reference web agent.",
        capabilities=("web", "readiness", "planner"),
        version="0.1.0",
        endpoint="https://agentos.example.invalid",
    )
    return {
        "agent_id": card.agent_id,
        "name": card.name,
        "capabilities": card.capabilities,
        "version": card.version,
        "endpoint": card.endpoint,
    }


def json_safe_mapping(values: Mapping[str, object]) -> dict[str, object]:
    """递归规范化 Mapping 为 JSON-safe 值。"""

    return {str(key): json_safe_value(value) for key, value in values.items()}


def json_safe_value(value: object) -> object:
    """递归规范化 Reference Evidence 值。"""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, tuple | list):
        return [json_safe_value(item) for item in value]
    if isinstance(value, Mapping):
        return json_safe_mapping(value)
    return repr(value)
