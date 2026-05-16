# Architecture

Source map: `docs/design/sdk-architecture.md`, `src/agentos/runtime/query_loop.py`,
`src/agentos/context/renderer.py`, `src/agentos/messages/runtime.py`,
`src/agentos/capabilities/router.py`, and `src/agentos/providers/base.py`.

```mermaid
flowchart TD
  User[User input] --> Agent[Agent facade]
  Agent --> QueryLoop[QueryLoop]
  QueryLoop --> Messages[MessageRuntime]
  QueryLoop --> Context[ContextRuntime]
  Context --> Renderer[ContextRenderer]
  Messages --> Builder[ProviderRequestBuilder]
  Renderer --> Builder
  Builder --> Provider[Provider]
  Provider --> QueryLoop
  QueryLoop --> Router[ToolCallRouter]
  Router --> Tools[Registered tools / context tools / MCP]
```

Production boundaries added by the hardening pass:

- `runtime/retry.py` provides provider retry and circuit breaker policy.
- `channels/rate_limit.py` provides channel-layer sliding-window limiting.
- `observability/logging.py` provides opt-in JSON logging.
- `multi/reconciler.py` and `multi/redis_continuation.py` provide distributed
  multi-agent notification recovery boundaries.
