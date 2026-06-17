from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

import pytest

from agentos.multi import AgentCard


@dataclass
class FakeNacosInstance:
    service_name: str
    group_name: str
    cluster_name: str
    namespace_id: str | None
    ip: str
    port: int
    metadata: dict[str, str]
    healthy: bool
    ephemeral: bool


class FakeNacosClient:
    def __init__(self) -> None:
        self.registered: list[FakeNacosInstance] = []
        self.deregistered: list[dict[str, object]] = []
        self.instances: list[FakeNacosInstance] = []

    def register_instance(
        self,
        *,
        service_name: str,
        group_name: str,
        cluster_name: str,
        namespace_id: str | None = None,
        ip: str,
        port: int,
        metadata: Mapping[str, str],
        healthy: bool,
        ephemeral: bool,
    ) -> None:
        self.registered.append(
            FakeNacosInstance(
                service_name=service_name,
                group_name=group_name,
                cluster_name=cluster_name,
                namespace_id=namespace_id,
                ip=ip,
                port=port,
                metadata=dict(metadata),
                healthy=healthy,
                ephemeral=ephemeral,
            ),
        )

    def deregister_instance(
        self,
        *,
        service_name: str,
        group_name: str,
        cluster_name: str,
        namespace_id: str | None = None,
        ip: str,
        port: int,
        ephemeral: bool,
    ) -> None:
        self.deregistered.append(
            {
                "service_name": service_name,
                "group_name": group_name,
                "cluster_name": cluster_name,
                "namespace_id": namespace_id,
                "ip": ip,
                "port": port,
                "ephemeral": ephemeral,
            },
        )

    def list_instances(
        self,
        *,
        service_name: str,
        group_name: str,
        cluster_name: str | None = None,
        namespace_id: str | None = None,
        healthy_only: bool = True,
    ) -> list[FakeNacosInstance]:
        instances = [
            instance
            for instance in self.instances
            if instance.service_name == service_name
            and instance.group_name == group_name
            and (cluster_name is None or instance.cluster_name == cluster_name)
            and (namespace_id is None or instance.namespace_id == namespace_id)
        ]
        if healthy_only:
            return [instance for instance in instances if instance.healthy]
        return instances


def card(agent_id: str, *capabilities: str) -> AgentCard:
    return AgentCard(
        agent_id=agent_id,
        name=agent_id.title(),
        description=f"{agent_id} agent",
        capabilities=tuple(capabilities),
        version="2026.6",
        endpoint=f"https://agents.test/{agent_id}/a2a",
        status="idle",
        lifecycle="persistent",
        max_concurrent_tasks=3,
    )


def test_nacos_registry_adapter_registers_discovery_only_metadata() -> None:
    from agentos.registry import NacosAgentRegistryAdapter, NacosRegistryConfig

    client = FakeNacosClient()
    adapter = NacosAgentRegistryAdapter(
        client=client,
        config=NacosRegistryConfig(
            service_name_prefix="agentos",
            group_name="AGENTOS",
            cluster_name="prod-a",
        ),
    )

    evidence = adapter.register(
        card("reviewer", "code_review", "tests"),
        ip="10.0.0.7",
        port=8080,
        a2a_card_url="https://agents.test/reviewer/.well-known/agent-card.json",
        worker_service="planner-worker",
        health={"status": "ready", "checked_at": 1234},
        metadata={"zone": "az-a"},
    )

    assert evidence.operation == "register"
    assert evidence.agent_id == "reviewer"
    assert evidence.service_name == "agentos.reviewer"
    assert evidence.group_name == "AGENTOS"
    assert evidence.cluster_name == "prod-a"
    assert evidence.healthy is True
    assert evidence.metadata_keys == (
        "agentos.a2a_card_url",
        "agentos.agent_id",
        "agentos.capabilities",
        "agentos.description",
        "agentos.endpoint",
        "agentos.health",
        "agentos.lifecycle",
        "agentos.max_concurrent_tasks",
        "agentos.metadata",
        "agentos.name",
        "agentos.status",
        "agentos.version",
        "agentos.worker_service",
    )
    assert json.loads(json.dumps(evidence.to_dict())) == evidence.to_dict()

    registered = client.registered[0]
    assert registered.service_name == "agentos.reviewer"
    assert registered.group_name == "AGENTOS"
    assert registered.cluster_name == "prod-a"
    assert registered.ip == "10.0.0.7"
    assert registered.port == 8080
    assert registered.healthy is True
    assert registered.ephemeral is True
    assert registered.metadata["agentos.agent_id"] == "reviewer"
    assert registered.metadata["agentos.endpoint"] == "https://agents.test/reviewer/a2a"
    assert registered.metadata["agentos.version"] == "2026.6"
    assert json.loads(registered.metadata["agentos.capabilities"]) == [
        "code_review",
        "tests",
    ]
    assert json.loads(registered.metadata["agentos.health"]) == {
        "checked_at": 1234,
        "status": "ready",
    }
    assert registered.metadata["agentos.worker_service"] == "planner-worker"
    assert json.loads(registered.metadata["agentos.metadata"]) == {"zone": "az-a"}

    forbidden = {
        "task_id",
        "plan_id",
        "session_snapshot",
        "message_payload",
        "worker_process_state",
        "env",
        "secret",
        "credential",
    }
    assert forbidden.isdisjoint(registered.metadata)


def test_nacos_registry_adapter_passes_namespace_to_client_and_evidence() -> None:
    from agentos.registry import (
        NacosAgentCardResolver,
        NacosAgentRegistryAdapter,
        NacosRegistryConfig,
    )

    client = FakeNacosClient()
    config = NacosRegistryConfig(
        service_name="agentos-agents",
        group_name="AGENTOS",
        cluster_name="prod-a",
        namespace_id="tenant-prod",
    )
    adapter = NacosAgentRegistryAdapter(client=client, config=config)
    register_evidence = adapter.register(
        card("reviewer", "code_review"),
        ip="10.0.0.7",
        port=8080,
    )
    unregister_evidence = adapter.unregister(
        "reviewer",
        ip="10.0.0.7",
        port=8080,
    )
    client.instances = client.registered
    resolver = NacosAgentCardResolver(client=client, config=config)

    assert register_evidence.to_dict()["namespace_id"] == "tenant-prod"
    assert unregister_evidence.to_dict()["namespace_id"] == "tenant-prod"
    assert client.registered[0].namespace_id == "tenant-prod"
    assert client.deregistered[0]["namespace_id"] == "tenant-prod"
    assert resolver.resolve("reviewer") == card("reviewer", "code_review")
    assert resolver.last_evidence is not None
    assert resolver.last_evidence.to_dict()["namespace_id"] == "tenant-prod"


def test_nacos_agent_card_resolver_reads_healthy_instances_and_filters_capabilities() -> None:
    from agentos.registry import (
        NacosAgentCardResolver,
        NacosAgentRegistryAdapter,
        NacosRegistryConfig,
    )

    client = FakeNacosClient()
    config = NacosRegistryConfig(
        service_name_prefix="agentos",
        service_name="agentos-agents",
        group_name="AGENTOS",
        cluster_name="prod-a",
    )
    adapter = NacosAgentRegistryAdapter(client=client, config=config)
    adapter.register(card("reviewer", "code_review", "tests"), ip="10.0.0.7", port=8080)
    adapter.register(card("searcher", "search"), ip="10.0.0.8", port=8081)
    offline = FakeNacosInstance(
        service_name="agentos-agents",
        group_name="AGENTOS",
        cluster_name="prod-a",
        namespace_id=None,
        ip="10.0.0.9",
        port=8082,
        metadata=client.registered[0].metadata | {"agentos.agent_id": "offline"},
        healthy=False,
        ephemeral=True,
    )
    client.instances = [*client.registered, offline]
    resolver = NacosAgentCardResolver(client=client, config=config)

    assert resolver.resolve("reviewer") == card("reviewer", "code_review", "tests")
    assert resolver.resolve("offline") is None
    assert resolver.discover(("code_review",)) == [
        card("reviewer", "code_review", "tests"),
    ]
    assert resolver.discover(("missing",)) == []
    assert resolver.select(("search",)) == card("searcher", "search")


def test_nacos_registry_adapter_deregisters_the_same_service_instance() -> None:
    from agentos.registry import NacosAgentRegistryAdapter, NacosRegistryConfig

    client = FakeNacosClient()
    adapter = NacosAgentRegistryAdapter(
        client=client,
        config=NacosRegistryConfig(
            service_name_prefix="agentos",
            group_name="AGENTOS",
            cluster_name="prod-a",
            ephemeral=False,
        ),
    )

    evidence = adapter.unregister("reviewer", ip="10.0.0.7", port=8080)

    assert evidence.operation == "deregister"
    assert evidence.to_dict()["ephemeral"] is False
    assert client.deregistered == [
        {
            "service_name": "agentos.reviewer",
            "group_name": "AGENTOS",
            "cluster_name": "prod-a",
            "ip": "10.0.0.7",
            "port": 8080,
            "ephemeral": False,
            "namespace_id": None,
        },
    ]


def test_nacos_resolver_rejects_invalid_metadata() -> None:
    from agentos.registry import (
        NacosAgentCardResolver,
        NacosRegistryConfig,
        NacosRegistryError,
    )

    client = FakeNacosClient()
    client.instances = [
        FakeNacosInstance(
            service_name="agentos-agents",
            group_name="AGENTOS",
            cluster_name="prod-a",
            namespace_id=None,
            ip="10.0.0.7",
            port=8080,
            metadata={
                "agentos.agent_id": "broken",
                "agentos.name": "Broken",
                "agentos.description": "Broken metadata",
                "agentos.capabilities": "not-json",
                "agentos.version": "2026.6",
                "agentos.endpoint": "https://agents.test/broken/a2a",
                "agentos.status": "idle",
                "agentos.lifecycle": "persistent",
                "agentos.max_concurrent_tasks": "1",
            },
            healthy=True,
            ephemeral=True,
        ),
    ]
    resolver = NacosAgentCardResolver(
        client=client,
        config=NacosRegistryConfig(
            service_name="agentos-agents",
            group_name="AGENTOS",
            cluster_name="prod-a",
        ),
    )

    with pytest.raises(NacosRegistryError, match="agentos.capabilities"):
        resolver.discover(())


def test_nacos_registry_adapter_rejects_secret_like_metadata_keys() -> None:
    from agentos.registry import NacosAgentRegistryAdapter, NacosRegistryError

    adapter = NacosAgentRegistryAdapter(client=FakeNacosClient())

    with pytest.raises(NacosRegistryError, match="credentials"):
        adapter.register(
            card("reviewer", "code_review"),
            ip="10.0.0.7",
            port=8080,
            metadata={"credentials": "raw-token"},
        )

    with pytest.raises(NacosRegistryError, match="secret_key"):
        adapter.register(
            card("reviewer", "code_review"),
            ip="10.0.0.7",
            port=8080,
            health={"checks": {"secret_key": "raw-secret"}},
        )
