from __future__ import annotations

import pytest

from agentos.multi import AgentCard, TaskRequest


class FakeTransport:
    def __init__(self) -> None:
        self.posts: list[
            tuple[str, dict[str, object], float, dict[str, str] | None]
        ] = []
        self.gets: list[tuple[str, float]] = []

    def post_json(
        self,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, object]:
        self.posts.append((url, payload, timeout_seconds, headers))
        return {
            "task_id": payload["task_id"],
            "status": "completed",
            "summary": "remote done",
            "artifacts": {"url": url},
            "elapsed_seconds": 1.25,
        }

    def get_json(self, url: str, timeout_seconds: float) -> dict[str, object]:
        self.gets.append((url, timeout_seconds))
        return {"status": "ok", "detail": "ready"}


class FailingHealthTransport(FakeTransport):
    def get_json(self, url: str, timeout_seconds: float) -> dict[str, object]:
        self.gets.append((url, timeout_seconds))
        raise TimeoutError("remote timeout")


def remote_card(endpoint: str | None = "https://agents.test/worker") -> AgentCard:
    return AgentCard(
        agent_id="worker_1",
        name="Worker",
        description="Remote worker",
        capabilities=("search",),
        endpoint=endpoint,
    )


def test_a2a_adapter_sends_task_request_to_remote_endpoint() -> None:
    from agentos.channels import A2AAdapter

    transport = FakeTransport()
    adapter = A2AAdapter(transport=transport)
    request = TaskRequest(
        task_id="task_1",
        instruction="search docs",
        allowed_tool_names=("read_file",),
        timeout_seconds=12,
    )

    result = adapter.send_task(remote_card(), request)

    assert result.task_id == "task_1"
    assert result.status == "completed"
    assert result.summary == "remote done"
    assert transport.posts == [
        (
            "https://agents.test/worker/a2a/tasks",
            {
                "task_id": "task_1",
                "instruction": "search docs",
                "allowed_tool_names": ["read_file"],
                "timeout_seconds": 12,
            },
            12,
            None,
        ),
    ]


def test_a2a_adapter_sends_trace_context_as_headers() -> None:
    from agentos.channels import A2AAdapter

    transport = FakeTransport()
    adapter = A2AAdapter(transport=transport)
    request = TaskRequest(
        task_id="task_1",
        instruction="search docs",
        trace_context={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"},
    )

    adapter.send_task(remote_card(), request)

    assert transport.posts[0][3] == request.trace_context


def test_a2a_adapter_checks_remote_health() -> None:
    from agentos.channels import A2AAdapter

    transport = FakeTransport()
    adapter = A2AAdapter(transport=transport)

    health = adapter.check_health(remote_card(), timeout_seconds=3)

    assert health.status == "ok"
    assert health.detail == "ready"
    assert transport.gets == [("https://agents.test/worker/a2a/health", 3)]


def test_a2a_adapter_health_check_returns_unhealthy_on_network_error() -> None:
    from agentos.channels import A2AAdapter

    transport = FailingHealthTransport()
    adapter = A2AAdapter(transport=transport)

    health = adapter.check_health(remote_card(), timeout_seconds=3)

    assert health.status == "unhealthy"
    assert "remote timeout" in (health.detail or "")
    assert transport.gets == [("https://agents.test/worker/a2a/health", 3)]


def test_a2a_adapter_requires_endpoint() -> None:
    from agentos.channels import A2AAdapter

    adapter = A2AAdapter(transport=FakeTransport())

    with pytest.raises(ValueError, match="endpoint"):
        adapter.send_task(remote_card(endpoint=None), TaskRequest("task_1", "do it"))
