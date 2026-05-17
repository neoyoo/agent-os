from __future__ import annotations

import asyncio
import json

from agentos.channels.asgi import AsgiAgentApp
from agentos.channels.rate_limit import SlidingWindowRateLimiter
from agentos.channels.session import InMemoryAgentSessionProvider
from tests.channels.test_asgi_app import call_asgi, response_body, response_status
from tests.multi.helpers import build_agent_with_response


def test_asgi_app_rate_limits_by_session_id() -> None:
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(lambda session_id: build_agent_with_response("ok")),
        rate_limiter=SlidingWindowRateLimiter(max_requests=1, window_seconds=60),
    )

    first = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"one"}',
        ),
    )
    second = asyncio.run(
        call_asgi(
            app,
            method="POST",
            path="/v1/sessions/session_1/turns",
            body=b'{"message":"two"}',
        ),
    )

    assert response_status(first) == 200
    assert response_status(second) == 429
    assert json.loads(response_body(second)) == {
        "status": "failed",
        "error": "rate limit exceeded",
    }
    headers = dict(second[0]["headers"])
    assert b"retry-after" in headers


def test_sliding_window_rate_limiter_evicts_idle_session_buckets() -> None:
    now = 0.0
    limiter = SlidingWindowRateLimiter(
        max_requests=1,
        window_seconds=10,
        now=lambda: now,
    )

    assert limiter.check("old_session").allowed
    now = 20.0
    assert limiter.check("active_session").allowed

    assert "old_session" not in limiter._requests
    assert "active_session" in limiter._requests
