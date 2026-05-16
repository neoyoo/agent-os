# Quickstart

This quickstart is backed by the public APIs in `src/agentos/builder.py`,
`src/agentos/runtime/agent.py`, `src/agentos/capabilities/tools.py`, and
`src/agentos/providers/fake.py`.

```python
from agentos import AgentBuilder
from agentos.capabilities import RegisteredTool
from agentos.providers import FakeProvider

def echo(args: dict[str, object]) -> str:
    return str(args["text"])

agent = (
    AgentBuilder()
    .provider(FakeProvider(["hello"]))
    .tools([
        RegisteredTool(
            name="echo",
            description="Echo text.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=echo,
        )
    ])
    .build()
)

print(agent.run("say hello").content)
```

For HTTP serving, wrap an `AgentSessionProvider` with `AsgiAgentApp`; see
`src/agentos/channels/asgi.py`.
