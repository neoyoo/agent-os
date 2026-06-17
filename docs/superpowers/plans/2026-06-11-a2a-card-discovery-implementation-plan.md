# A2A Card Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 4A A2A Agent Card and discovery primitives while keeping the current `/a2a/tasks` bridge clearly internal.

**Architecture:** Keep internal `agentos.multi.AgentCard` as the SDK routing card. Add A2A protocol-card dataclasses, serializers, an adapter, a transport-backed resolver, and optional ASGI well-known publication in `agentos.channels`. `QueryLoop` remains deployment-agnostic.

**Tech Stack:** Python 3.11 dataclasses/protocols, existing ASGI app, existing `A2ATransport`, pytest.

---

## Scope Contract

This plan implements only Phase 4A from `docs/superpowers/specs/2026-06-11-a2a-card-discovery-design.md`.

Target conclusion:

```text
Internal agent-os AgentCard remains the SDK routing card.
A2A Agent Card is a protocol compatibility surface with its own serializer, well-known route, and resolver.
The existing /a2a/tasks bridge remains internal until task/message operation parity is implemented.
```

Deferred:

- Full A2A task/message operation parity.
- Streaming task updates.
- Push notifications.
- Signed cards and trust stores.
- OAuth/OIDC/JWKS enforcement.
- Team discussion runtime.

## File Structure

Modify:

- `src/agentos/channels/a2a.py`  
  Add A2A protocol card dataclasses, serializer/deserializer, internal-card adapter, and resolver.

- `src/agentos/channels/asgi.py`  
  Add optional `a2a_agent_card` constructor parameter and well-known routes.

- `src/agentos/channels/__init__.py`  
  Export A2A card names.

- `src/agentos/__init__.py`  
  Mirror A2A card names if top-level channel exports are mirrored.

- `tests/channels/test_a2a_card.py`  
  New tests for serialization, adapter, and resolver.

- `tests/channels/test_asgi_app.py`  
  Add well-known route tests.

- `tests/architecture/test_public_api.py`  
  Add public API assertions.

- `.claude/skills/agent-os/modules/agent-forms.md`  
  Update A2A readiness.

- `.claude/skills/agent-os/modules/multi-agent.md`  
  Update remote A2A dispatch section.

Do not modify:

- `src/agentos/runtime/query_loop.py`
- `src/agentos/runtime/async_query_loop.py`
- `src/agentos/multi/types.py` for Phase 4A

## Task 1: Add A2A Card Model And Serializers

**Files:**
- Modify: `src/agentos/channels/a2a.py`
- Create: `tests/channels/test_a2a_card.py`

- [ ] **Step 1: Write failing serialization tests**

Create `tests/channels/test_a2a_card.py`:

```python
from __future__ import annotations

from agentos.channels.a2a import (
    A2AAgentCapabilities,
    A2AAgentCard,
    A2AAgentProvider,
    A2AAgentSkill,
    a2a_card_from_dict,
    a2a_card_to_dict,
)


def test_a2a_agent_card_serializes_protocol_keys() -> None:
    card = A2AAgentCard(
        name="Research Agent",
        description="Researches documents.",
        url="https://agents.example/a2a",
        version="1.0.0",
        provider=A2AAgentProvider(
            organization="Example Inc.",
            url="https://example.com",
        ),
        capabilities=A2AAgentCapabilities(
            streaming=True,
            push_notifications=False,
            state_transition_history=True,
        ),
        skills=(
            A2AAgentSkill(
                id="research",
                name="Research",
                description="Find and summarize sources.",
                tags=("search", "summarize"),
                examples=("Find recent papers about agent protocols.",),
            ),
        ),
        security_schemes={
            "apiKey": {"type": "apiKey", "in": "header", "name": "x-api-key"},
        },
        security=({"apiKey": ()},),
    )

    payload = a2a_card_to_dict(card)

    assert payload["protocolVersion"] == "0.3.0"
    assert payload["defaultInputModes"] == ["text/plain"]
    assert payload["defaultOutputModes"] == ["text/plain"]
    assert payload["capabilities"] == {
        "streaming": True,
        "pushNotifications": False,
        "stateTransitionHistory": True,
    }
    assert payload["provider"] == {
        "organization": "Example Inc.",
        "url": "https://example.com",
    }
    assert payload["skills"] == [
        {
            "id": "research",
            "name": "Research",
            "description": "Find and summarize sources.",
            "tags": ["search", "summarize"],
            "examples": ["Find recent papers about agent protocols."],
        },
    ]
    assert a2a_card_from_dict(payload) == card
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/channels/test_a2a_card.py::test_a2a_agent_card_serializes_protocol_keys -q
```

Expected: FAIL with `ImportError` for missing A2A card names.

- [ ] **Step 3: Implement dataclasses and serializers**

Append to `src/agentos/channels/a2a.py`:

```python
from dataclasses import field
from collections.abc import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class A2AAgentSkill:
    """A2A protocol skill declaration."""

    id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class A2AAgentCapabilities:
    """A2A protocol capability flags."""

    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = False


@dataclass(frozen=True, slots=True)
class A2AAgentProvider:
    """A2A protocol provider declaration."""

    organization: str
    url: str | None = None


@dataclass(frozen=True, slots=True)
class A2AAgentCard:
    """A2A protocol Agent Card."""

    name: str
    description: str
    url: str
    version: str
    protocol_version: str = "0.3.0"
    provider: A2AAgentProvider | None = None
    capabilities: A2AAgentCapabilities = field(default_factory=A2AAgentCapabilities)
    skills: tuple[A2AAgentSkill, ...] = ()
    default_input_modes: tuple[str, ...] = ("text/plain",)
    default_output_modes: tuple[str, ...] = ("text/plain",)
    security_schemes: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    security: tuple[Mapping[str, tuple[str, ...]], ...] = ()
```

Add serializer helpers:

```python
def a2a_card_to_dict(card: A2AAgentCard) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocolVersion": card.protocol_version,
        "name": card.name,
        "description": card.description,
        "url": card.url,
        "version": card.version,
        "capabilities": {
            "streaming": card.capabilities.streaming,
            "pushNotifications": card.capabilities.push_notifications,
            "stateTransitionHistory": card.capabilities.state_transition_history,
        },
        "defaultInputModes": list(card.default_input_modes),
        "defaultOutputModes": list(card.default_output_modes),
        "skills": [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "tags": list(skill.tags),
                "examples": list(skill.examples),
            }
            for skill in card.skills
        ],
    }
    if card.provider is not None:
        provider: dict[str, object] = {"organization": card.provider.organization}
        if card.provider.url is not None:
            provider["url"] = card.provider.url
        payload["provider"] = provider
    if card.security_schemes:
        payload["securitySchemes"] = {
            name: dict(spec)
            for name, spec in card.security_schemes.items()
        }
    if card.security:
        payload["security"] = [
            {name: list(scopes) for name, scopes in item.items()}
            for item in card.security
        ]
    return payload


def a2a_card_from_dict(payload: Mapping[str, object]) -> A2AAgentCard:
    capabilities_payload = payload.get("capabilities", {})
    capabilities = (
        capabilities_payload
        if isinstance(capabilities_payload, Mapping)
        else {}
    )
    provider_payload = payload.get("provider")
    provider = None
    if isinstance(provider_payload, Mapping):
        provider = A2AAgentProvider(
            organization=str(provider_payload.get("organization", "")),
            url=(
                None
                if provider_payload.get("url") is None
                else str(provider_payload.get("url"))
            ),
        )
    skills = []
    for item in payload.get("skills", []):
        if not isinstance(item, Mapping):
            continue
        skills.append(
            A2AAgentSkill(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                description=str(item.get("description", "")),
                tags=tuple(str(tag) for tag in item.get("tags", []) if isinstance(tag, str)),
                examples=tuple(
                    str(example)
                    for example in item.get("examples", [])
                    if isinstance(example, str)
                ),
            ),
        )
    security_schemes_payload = payload.get("securitySchemes", {})
    security_schemes = (
        {
            str(name): dict(spec)
            for name, spec in security_schemes_payload.items()
            if isinstance(spec, Mapping)
        }
        if isinstance(security_schemes_payload, Mapping)
        else {}
    )
    security_items = []
    for item in payload.get("security", []):
        if not isinstance(item, Mapping):
            continue
        security_items.append(
            {
                str(name): tuple(str(scope) for scope in scopes)
                for name, scopes in item.items()
                if isinstance(scopes, list)
            },
        )
    return A2AAgentCard(
        protocol_version=str(payload.get("protocolVersion", "0.3.0")),
        name=str(payload["name"]),
        description=str(payload["description"]),
        url=str(payload["url"]),
        version=str(payload["version"]),
        provider=provider,
        capabilities=A2AAgentCapabilities(
            streaming=bool(capabilities.get("streaming", False)),
            push_notifications=bool(capabilities.get("pushNotifications", False)),
            state_transition_history=bool(
                capabilities.get("stateTransitionHistory", False),
            ),
        ),
        skills=tuple(skills),
        default_input_modes=tuple(
            str(value) for value in payload.get("defaultInputModes", ["text/plain"])
        ),
        default_output_modes=tuple(
            str(value) for value in payload.get("defaultOutputModes", ["text/plain"])
        ),
        security_schemes=security_schemes,
        security=tuple(security_items),
    )
```

- [ ] **Step 4: Run serialization tests**

Run:

```bash
uv run pytest tests/channels/test_a2a_card.py::test_a2a_agent_card_serializes_protocol_keys -q
```

Expected: PASS.

## Task 2: Add Internal Card Adapter And Resolver

**Files:**
- Modify: `src/agentos/channels/a2a.py`
- Modify: `tests/channels/test_a2a_card.py`

- [ ] **Step 1: Add failing adapter/resolver tests**

Append to `tests/channels/test_a2a_card.py`:

```python
from agentos.channels.a2a import A2ACardResolver, a2a_card_from_agent_card
from agentos.multi import AgentCard


def test_a2a_card_from_internal_agent_card_maps_capabilities_to_skills() -> None:
    internal = AgentCard(
        agent_id="researcher",
        name="Researcher",
        description="Research specialist.",
        capabilities=("research", "summarize"),
        version="2.0.0",
        endpoint="https://agents.example/researcher",
    )

    card = a2a_card_from_agent_card(internal)

    assert card.name == "Researcher"
    assert card.url == "https://agents.example/researcher"
    assert card.version == "2.0.0"
    assert [skill.id for skill in card.skills] == ["research", "summarize"]


def test_a2a_card_resolver_loads_direct_and_well_known_cards() -> None:
    direct = A2AAgentCard(
        name="Direct",
        description="Direct card.",
        url="https://direct.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="direct", name="Direct", description="Direct."),),
    )
    remote = A2AAgentCard(
        name="Remote",
        description="Remote card.",
        url="https://remote.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="search", name="Search", description="Search."),),
    )

    class FakeTransport:
        def get_json(self, url: str, timeout_seconds: float) -> dict[str, object]:
            assert url == "https://remote.example/.well-known/agent-card.json"
            return a2a_card_to_dict(remote)

        def post_json(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise AssertionError("not used")

    resolver = A2ACardResolver(
        cards=(direct,),
        well_known_urls={"Remote": "https://remote.example"},
        transport=FakeTransport(),
    )

    assert resolver.resolve("Direct") == direct
    assert resolver.resolve("Remote") == remote
    assert resolver.discover(("search",)) == [remote]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/channels/test_a2a_card.py::test_a2a_card_from_internal_agent_card_maps_capabilities_to_skills tests/channels/test_a2a_card.py::test_a2a_card_resolver_loads_direct_and_well_known_cards -q
```

Expected: FAIL with missing names.

- [ ] **Step 3: Implement adapter and resolver**

Append to `src/agentos/channels/a2a.py`:

```python
def a2a_card_from_agent_card(
    card: AgentCard,
    *,
    url: str | None = None,
) -> A2AAgentCard:
    target_url = url or card.endpoint
    if target_url is None:
        raise ValueError(f"agent card has no endpoint: {card.agent_id}")
    return A2AAgentCard(
        name=card.name,
        description=card.description,
        url=target_url,
        version=card.version,
        skills=tuple(
            A2AAgentSkill(
                id=capability,
                name=capability,
                description=f"Capability: {capability}",
                tags=(capability,),
            )
            for capability in card.capabilities
        ),
    )


class A2ACardResolver:
    """Resolve A2A Agent Cards from direct config and well-known URLs."""

    def __init__(
        self,
        *,
        cards: Sequence[A2AAgentCard] = (),
        well_known_urls: Mapping[str, str] | None = None,
        transport: A2ATransport | None = None,
        timeout_seconds: float = 5,
    ) -> None:
        self._cards = {card.name: card for card in cards}
        self._well_known_urls = dict(well_known_urls or {})
        self._transport = transport or UrllibA2ATransport()
        self._timeout_seconds = timeout_seconds

    def resolve(self, name: str) -> A2AAgentCard | None:
        card = self._cards.get(name)
        if card is not None:
            return card
        url = self._well_known_urls.get(name)
        if url is None:
            return None
        card = a2a_card_from_dict(
            self._transport.get_json(
                self._well_known_card_url(url),
                self._timeout_seconds,
            ),
        )
        self._cards[name] = card
        return card

    def discover(self, required_skills: Sequence[str]) -> list[A2AAgentCard]:
        for name in list(self._well_known_urls):
            self.resolve(name)
        required = set(required_skills)
        return [
            card
            for card in self._cards.values()
            if required.issubset({skill.id for skill in card.skills})
        ]

    def _well_known_card_url(self, value: str) -> str:
        if value.endswith("/.well-known/agent-card.json"):
            return value
        return value.rstrip("/") + "/.well-known/agent-card.json"
```

- [ ] **Step 4: Run all A2A card tests**

Run:

```bash
uv run pytest tests/channels/test_a2a_card.py -q
```

Expected: PASS.

## Task 3: Add ASGI Well-Known Card Route

**Files:**
- Modify: `src/agentos/channels/asgi.py`
- Modify: `tests/channels/test_asgi_app.py`

- [ ] **Step 1: Add failing ASGI route tests**

Append to `tests/channels/test_asgi_app.py`:

```python
def test_asgi_app_serves_a2a_agent_card() -> None:
    from agentos.channels.a2a import A2AAgentCard, A2AAgentSkill
    from agentos.channels.asgi import AsgiAgentApp

    card = A2AAgentCard(
        name="Research",
        description="Research agent.",
        url="https://agents.example/a2a",
        version="1.0.0",
        skills=(A2AAgentSkill(id="research", name="Research", description="Research."),),
    )
    app = AsgiAgentApp(
        sessions=InMemoryAgentSessionProvider(
            lambda session_id: build_agent_with_response("unused"),
        ),
        a2a_agent_card=card,
    )

    sent = asyncio.run(
        call_asgi(
            app,
            method="GET",
            path="/.well-known/agent-card.json",
        ),
    )

    assert response_status(sent) == 200
    assert json.loads(response_body(sent))["name"] == "Research"


def test_asgi_app_returns_404_for_missing_a2a_agent_card() -> None:
    sent = asyncio.run(
        call_asgi(
            build_app(build_agent_with_response("unused")),
            method="GET",
            path="/.well-known/agent-card.json",
        ),
    )

    assert response_status(sent) == 404
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/channels/test_asgi_app.py::test_asgi_app_serves_a2a_agent_card tests/channels/test_asgi_app.py::test_asgi_app_returns_404_for_missing_a2a_agent_card -q
```

Expected: FAIL because `AsgiAgentApp` does not accept `a2a_agent_card`.

- [ ] **Step 3: Implement route**

Modify imports in `src/agentos/channels/asgi.py`:

```python
from agentos.channels.a2a import A2AAgentCard, a2a_card_to_dict
```

Add constructor field:

```python
a2a_agent_card: A2AAgentCard | None = None,
```

Store:

```python
self._a2a_agent_card = a2a_agent_card
```

Add before health/task routes:

```python
if method == "GET" and path in {
    "/.well-known/agent-card.json",
    "/a2a/agent-card",
}:
    if self._a2a_agent_card is None:
        await self._send_json(send, 404, {"status": "failed", "error": "not found"})
        return
    await self._send_json(send, 200, a2a_card_to_dict(self._a2a_agent_card))
    return
```

- [ ] **Step 4: Run ASGI route tests**

Run:

```bash
uv run pytest tests/channels/test_asgi_app.py::test_asgi_app_serves_a2a_agent_card tests/channels/test_asgi_app.py::test_asgi_app_returns_404_for_missing_a2a_agent_card -q
```

Expected: PASS.

## Task 4: Public API Exports

**Files:**
- Modify: `src/agentos/channels/__init__.py`
- Modify: `src/agentos/__init__.py`
- Modify: `tests/architecture/test_public_api.py`

- [ ] **Step 1: Add failing public API tests**

In `tests/architecture/test_public_api.py`, extend `test_remote_registry_and_channel_public_api_exports` channel and top-level lists with:

```python
        "A2AAgentCapabilities",
        "A2AAgentCard",
        "A2AAgentProvider",
        "A2AAgentSkill",
        "A2ACardResolver",
        "a2a_card_from_agent_card",
        "a2a_card_from_dict",
        "a2a_card_to_dict",
```

- [ ] **Step 2: Run public API test to verify failure**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py::test_remote_registry_and_channel_public_api_exports -q
```

Expected: FAIL because names are not exported.

- [ ] **Step 3: Export names**

Import the names from `agentos.channels.a2a` in `src/agentos/channels/__init__.py` and add them to `__all__`.

Mirror the names in `src/agentos/__init__.py`.

- [ ] **Step 4: Run public API tests**

Run:

```bash
uv run pytest tests/architecture/test_public_api.py -q
```

Expected: PASS.

## Task 5: Documentation Alignment

**Files:**
- Modify: `.claude/skills/agent-os/modules/agent-forms.md`
- Modify: `.claude/skills/agent-os/modules/multi-agent.md`

- [ ] **Step 1: Update agent forms docs**

For `Agent Registry / Discovery Agent`, change available/missing to mention:

```markdown
**Available**: internal `AgentCard`, `PersistentAgentRegistry`, `ServiceResolver`, plus A2A protocol card serialization and well-known publication primitives.
**Missing**: full A2A task/message operation parity, streaming task updates, push notifications, signed cards/trust store, and auth enforcement.
```

For `Minimal A2A Task Bridge`, add:

```markdown
**Available**: outbound/internal task bridge plus A2A Agent Card publication/discovery primitives.
```

- [ ] **Step 2: Update multi-agent docs**

In Remote A2A Dispatch, add:

```markdown
Phase 4A adds A2A Agent Card serialization, adapter, resolver, and `/.well-known/agent-card.json` publication. This improves discovery but does not make `/a2a/tasks` full A2A operation parity.
```

- [ ] **Step 3: Run drift search**

Run:

```bash
rg "full A2A compliance|Agent Card discovery.*missing|well-known.*missing|A2A Agent Card" .claude docs src tests
```

Expected: docs should say card/discovery primitives exist and operation parity is still missing.

## Task 6: Verification

**Files:** all changed files

- [ ] **Step 1: Run targeted tests**

Run:

```bash
uv run pytest tests/channels/test_a2a_card.py tests/channels/test_asgi_app.py tests/architecture/test_public_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run compileall**

Run:

```bash
uv run python -m compileall -q src tests
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run full suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run final diff check**

Run:

```bash
git diff --check
```

Expected: no whitespace errors. Windows line-ending warnings are acceptable if no error lines are printed.

- [ ] **Step 5: Boundary search**

Run:

```bash
rg "agentos.channels|agentos.registry|agentos.workspace|runtime.profile|Redis|Postgres|A2A" src/agentos/runtime/query_loop.py src/agentos/runtime/async_query_loop.py
```

Expected: no matches.

## Self-Review

Spec coverage:

- Protocol card model and serializers: Task 1.
- Internal card adapter and resolver: Task 2.
- Well-known publication: Task 3.
- Public API: Task 4.
- Docs alignment: Task 5.
- QueryLoop invariant and test suite: Task 6.

Placeholder scan:

- No implementation step contains red-flag placeholders or unspecified tests.

Type consistency:

- Names match the Phase 4 design spec: `A2AAgentCard`, `A2AAgentSkill`, `A2AAgentCapabilities`, `A2AAgentProvider`, `A2ACardResolver`, `a2a_card_to_dict`, `a2a_card_from_dict`, `a2a_card_from_agent_card`.
