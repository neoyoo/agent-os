"""远程 channel adapter 边界。"""

from agentos.channels.a2a import (
    A2AAdapter,
    A2ATransport,
    AgentHealth,
    AgentHealthStatus,
    UrllibA2ATransport,
)
from agentos.channels.a2a_server import (
    A2AServerAdapter,
    A2ATaskRunner,
    AgentA2ATaskRunner,
)
from agentos.channels.asgi import AsgiAgentApp
from agentos.channels.auth import (
    AllowAllChannelAuthPolicy,
    ChannelAuthError,
    ChannelAuthPolicy,
)
from agentos.channels.http import HttpAgentChannel
from agentos.channels.rate_limit import (
    RateLimitDecision,
    RateLimiter,
    SlidingWindowRateLimiter,
)
from agentos.channels.session import (
    AgentSessionProvider,
    InMemoryAgentSessionProvider,
)
from agentos.channels.sse import SseAgentChannel
from agentos.channels.types import (
    ChannelError,
    ChannelTurnRequest,
    ChannelTurnResult,
)

__all__ = [
    "A2AAdapter",
    "A2AServerAdapter",
    "A2ATaskRunner",
    "A2ATransport",
    "AgentA2ATaskRunner",
    "AgentHealth",
    "AgentHealthStatus",
    "AgentSessionProvider",
    "AllowAllChannelAuthPolicy",
    "AsgiAgentApp",
    "ChannelAuthError",
    "ChannelAuthPolicy",
    "ChannelError",
    "ChannelTurnRequest",
    "ChannelTurnResult",
    "HttpAgentChannel",
    "InMemoryAgentSessionProvider",
    "RateLimitDecision",
    "RateLimiter",
    "SseAgentChannel",
    "SlidingWindowRateLimiter",
    "UrllibA2ATransport",
]
