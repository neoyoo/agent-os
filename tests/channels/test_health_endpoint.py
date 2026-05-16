from __future__ import annotations

import asyncio
import json

from agentos.channels.asgi import AsgiAgentApp
from agentos.channels.session import InMemoryAgentSessionProvider
from tests.channels.test_asgi_app import call_asgi, response_body, response_status
from tests.multi.helpers import build_agent_with_response


def test_health_endpoint_returns_ok_on_root_path() -> None:
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(lambda session_id: build_agent_with_response("ok")),
    )

    sent = asyncio.run(call_asgi(app, method="GET", path="/health"))

    assert response_status(sent) == 200
    assert json.loads(response_body(sent)) == {"status": "ok"}


def test_ready_endpoint_runs_custom_checks() -> None:
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(lambda session_id: build_agent_with_response("ok")),
        readiness_checks={"db": lambda: True, "provider": lambda: {"status": "ok"}},
    )

    sent = asyncio.run(call_asgi(app, method="GET", path="/ready"))

    assert response_status(sent) == 200
    assert json.loads(response_body(sent)) == {
        "status": "ready",
        "checks": {"db": "ok", "provider": "ok"},
    }


def test_ready_endpoint_returns_503_when_check_fails() -> None:
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(lambda session_id: build_agent_with_response("ok")),
        readiness_checks={"db": lambda: False},
    )

    sent = asyncio.run(call_asgi(app, method="GET", path="/ready"))

    assert response_status(sent) == 503
    assert json.loads(response_body(sent)) == {
        "status": "not_ready",
        "checks": {"db": "failed"},
    }
