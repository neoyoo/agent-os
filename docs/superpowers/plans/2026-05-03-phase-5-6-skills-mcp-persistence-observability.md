# Phase 5-6 Skills MCP Persistence Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Phase 5 Skills + MCP and Phase 6 Persistence + Session Recovery + Observability for the context-first agentos SDK.

**Architecture:** Phase 5 extends `capabilities/` as the source of truth for skills, MCP server summaries, provider tool schemas and routing. Phase 6 adds `persistence/` and `observability/` without changing the default prompt boundary; runtime metadata is recorded internally and exposed only through explicit debug projection.

**Tech Stack:** Python 3.11 dataclasses, Protocol types, stdlib `json`, `sqlite3`, `pathlib`, `datetime`, pytest, uv. No required runtime dependency is added.

---

## Scope And References

Read these files before implementing any task:

- `AGENTS.md`
- `docs/design/sdk-architecture.md`
- `docs/design/llm-context-only-example.md`
- `docs/superpowers/specs/2026-05-03-phase-5-6-skills-mcp-persistence-observability-design.md`
- `../ai-knowledge/wiki/mcp-skills.md`
- `../ai-knowledge/wiki/tool-system.md`
- `../ai-knowledge/wiki/session-recovery.md`
- `../ai-knowledge/wiki/evaluation-observability.md`
- `../neoagent/neoagent/tools/builtin/skill_load.py`
- `../neoagent/neoagent/mcp/client.py`
- `../neoagent/neoagent/session.py`
- `../neoagent/neoagent/integrations/otel.py`

Implementation must keep public imports lowercase `agentos`.

## File Structure

Create:

- `src/agentos/capabilities/skills.py`: skill dataclasses, discovery, built-in schema template skill and `load_skill` tool registration.
- `src/agentos/capabilities/mcp.py`: MCP client Protocol, registry, provider schema adapter and execution adapter.
- `src/agentos/persistence/__init__.py`: persistence public exports.
- `src/agentos/persistence/base.py`: snapshot dataclasses and persistence Protocol.
- `src/agentos/persistence/serializers.py`: explicit JSON-safe serializers for session, context, messages and compression state.
- `src/agentos/persistence/memory.py`: in-memory persistence backend.
- `src/agentos/persistence/filesystem.py`: JSON file persistence backend.
- `src/agentos/persistence/sqlite.py`: SQLite persistence backend.
- `src/agentos/observability/__init__.py`: observability public exports.
- `src/agentos/observability/events.py`: event records, event log and event subscriber Protocol.
- `src/agentos/observability/traces.py`: trace records and event-to-trace projector.
- `src/agentos/observability/otel.py`: import-free OTel adapter around an injected tracer.
- `src/agentos/observability/langfuse.py`: import-free Langfuse adapter around an injected client.
- `src/agentos/context/debug_projection.py`: explicit debug metadata projection.

Modify:

- `src/agentos/capabilities/__init__.py`: export skill and MCP public names.
- `src/agentos/capabilities/tools.py`: extend `ToolKind` to `external`, `context`, `skill`, and `mcp`.
- `src/agentos/capabilities/registry.py`: include provider specs and capability summaries for Phase 5 kinds.
- `src/agentos/capabilities/router.py`: route `load_skill` and `mcp__<server>__<tool>` calls.
- `src/agentos/context/projection.py`: adjust skill declaration fields if needed.
- `src/agentos/context/renderer.py`: render skill summaries as available skills and keep default metadata guard.
- `src/agentos/context/runtime.py`: optionally emit typed context events.
- `src/agentos/messages/store.py`: add explicit restore constructor and next-id snapshot.
- `src/agentos/messages/window.py`: add readonly active-ref snapshot helpers.
- `src/agentos/messages/runtime.py`: add explicit restore constructor.
- `src/agentos/compression/index.py`: add readonly snapshot and restore constructor.
- `src/agentos/compression/runtime.py`: emit compression events and restore next segment number.
- `src/agentos/recall/runtime.py`: emit recall events.
- `src/agentos/runtime/event_bus.py`: add event subscriber support and Phase 6 event dataclasses.
- `src/agentos/runtime/query_loop.py`: include message/tool ids in emitted events and save snapshot through an optional persistence boundary.
- `src/agentos/runtime/session.py`: expose next turn number for snapshots and restore.
- `src/agentos/runtime/__init__.py`: export added event types if current package pattern requires it.
- `src/agentos/__init__.py`: keep public package version stable.
- `tests/architecture/test_public_api.py`: assert new Phase 5/6 public names and naming drift guards.
- `tests/context/goldens/default_context.md`: update only if renderer heading text changes.

Create tests:

- `tests/capabilities/test_skills.py`
- `tests/capabilities/test_mcp.py`
- `tests/capabilities/test_tool_registry_phase5.py`
- `tests/context/test_capability_plane_phase5.py`
- `tests/runtime/test_skill_mcp_tool_loop.py`
- `tests/persistence/test_serializers.py`
- `tests/persistence/test_filesystem.py`
- `tests/persistence/test_sqlite.py`
- `tests/observability/test_event_log.py`
- `tests/observability/test_traces.py`
- `tests/context/test_debug_projection.py`
- `tests/runtime/test_session_recovery.py`

---

## Task 1: Phase 5 Skill Tests

**Files:**

- Create: `tests/capabilities/test_skills.py`
- Create: `tests/context/test_capability_plane_phase5.py`
- Modify later: `src/agentos/capabilities/skills.py`
- Modify later: `src/agentos/context/renderer.py`

- [ ] **Step 1: Write failing skill discovery tests**

Add these tests to `tests/capabilities/test_skills.py`:

```python
from pathlib import Path

import pytest

from agentos.capabilities.skills import (
    SkillRegistry,
    builtin_schema_template_skill,
    register_skill_loader_tool,
)
from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.providers import ProviderToolCall


def write_skill(path: Path, frontmatter: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")


def test_skill_registry_discovers_flat_directory_and_learned_layouts(tmp_path: Path) -> None:
    write_skill(
        tmp_path / "debugging.md",
        "name: systematic-debugging\ndescription: Debug failures.\nwhen_to_use: 遇到 bug 或测试失败时使用。\n",
        "# Debugging\nRead errors first.\n",
    )
    write_skill(
        tmp_path / "review" / "SKILL.md",
        "name: code-review\ndescription: Review code.\nwhen_to_use: 审查已完成改动时使用。\n",
        "# Review\nFind bugs before summaries.\n",
    )
    write_skill(
        tmp_path / "learned" / "repo-style" / "SKILL.md",
        "name: repo-style\ndescription: Repo conventions.\nwhen_to_use: 修改本仓库代码前使用。\n",
        "# Repo Style\nUse agentos imports.\n",
    )

    registry = SkillRegistry.from_paths([tmp_path], allowed={"systematic-debugging", "code-review"})

    declarations = registry.capability_declarations()
    assert [(item.name, item.when_to_use) for item in declarations] == [
        ("repo-style", "修改本仓库代码前使用。"),
        ("systematic-debugging", "遇到 bug 或测试失败时使用。"),
        ("code-review", "审查已完成改动时使用。"),
    ]
    assert registry.load("repo-style").content == "# Repo Style\nUse agentos imports.\n"


def test_skill_loader_tool_returns_content_or_deterministic_error(tmp_path: Path) -> None:
    write_skill(
        tmp_path / "debugging.md",
        "name: systematic-debugging\ndescription: Debug failures.\nwhen_to_use: 遇到 bug 或测试失败时使用。\n",
        "# Debugging\nRead errors first.\n",
    )
    skills = SkillRegistry.from_paths([tmp_path])
    tools = ToolRegistry()
    register_skill_loader_tool(tools, skills)
    router = ToolCallRouter(tool_registry=tools)

    loaded = router.execute_tool_call(
        ProviderToolCall(
            id="call_1",
            name="load_skill",
            arguments={"skill_name": "systematic-debugging"},
        ),
    )
    missing = router.execute_tool_call(
        ProviderToolCall(
            id="call_2",
            name="load_skill",
            arguments={"skill_name": "unknown"},
        ),
    )

    assert loaded.tool_call_id == "call_1"
    assert "# Skill: systematic-debugging" in loaded.content
    assert "# Debugging" in loaded.content
    assert missing.content == (
        '{"error": "Skill \\'unknown\\' not found", '
        '"available_skills": ["systematic-debugging"]}'
    )


def test_builtin_schema_template_skill_is_available_but_not_special_cased() -> None:
    registry = SkillRegistry(builtin_skills=[builtin_schema_template_skill()])

    result = registry.load("schema-template")

    assert "declare_schema" in result.content
    assert "update_state" in result.content
    assert registry.capability_declarations()[0].name == "schema-template"


def test_skill_registry_rejects_duplicate_builtin_names() -> None:
    first = builtin_schema_template_skill()
    second = builtin_schema_template_skill()

    with pytest.raises(ValueError, match="duplicate skill"):
        SkillRegistry(builtin_skills=[first, second])
```

- [ ] **Step 2: Write failing renderer tests for skill summaries**

Add these tests to `tests/context/test_capability_plane_phase5.py`:

```python
from agentos.context import ContextRenderer, ContextState
from agentos.context.projection import CapabilityPlane, SkillDeclaration


def test_renderer_lists_skill_summaries_without_skill_bodies() -> None:
    rendered = ContextRenderer(
        capability_plane=CapabilityPlane(
            skills=[
                SkillDeclaration(
                    name="schema-template",
                    when_to_use="需要声明 working state schema 时使用。",
                ),
            ],
        ),
    ).render(ContextState())

    assert "schema-template" in rendered
    assert "需要声明 working state schema 时使用。" in rendered
    assert "declare_schema examples" not in rendered
    assert "# Skill: schema-template" not in rendered


def test_default_renderer_still_omits_runtime_metadata_with_phase5_capabilities() -> None:
    rendered = ContextRenderer(
        capability_plane=CapabilityPlane(
            skills=[
                SkillDeclaration(
                    name="schema-template",
                    when_to_use="需要声明 working state schema 时使用。",
                ),
            ],
        ),
    ).render(ContextState())

    for forbidden in [
        "session_id",
        "message_id",
        "trace_id",
        "span_id",
        "tool_call_id",
        "schema_id",
        "projection_id",
        "compression_id",
        "source",
        "relevance",
    ]:
        assert forbidden not in rendered
```

- [ ] **Step 3: Run tests and verify they fail because implementation is missing**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/capabilities/test_skills.py tests/context/test_capability_plane_phase5.py -q
```

Expected: failures mention missing `agentos.capabilities.skills` or missing skill exports.

---

## Task 2: Implement Skills

**Files:**

- Create: `src/agentos/capabilities/skills.py`
- Modify: `src/agentos/capabilities/__init__.py`
- Modify: `src/agentos/capabilities/tools.py`
- Modify: `src/agentos/capabilities/registry.py`
- Modify: `src/agentos/context/renderer.py` if heading text needs alignment.

- [ ] **Step 1: Implement skill dataclasses and parser**

Create `src/agentos/capabilities/skills.py` with these public objects and behavior:

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from agentos.capabilities.registry import ToolRegistry
from agentos.capabilities.tools import RegisteredTool
from agentos.context.projection import SkillDeclaration


SkillSource = Literal["builtin", "filesystem", "learned"]
_FRONTMATTER_RE = re.compile(r"^---\\s*\\n(.*?)\\n---\\s*\\n", re.DOTALL)
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """可按需加载的 skill 定义。"""

    name: str
    description: str
    when_to_use: str
    content: str
    source: SkillSource = "filesystem"
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class SkillLoadResult:
    """`load_skill` 工具返回的结构化结果。"""

    name: str
    content: str

    def render_tool_result(self) -> str:
        """渲染为写入 tool result 的文本。"""

        return f"# Skill: {self.name}\\n\\n{self.content}"
```

Implement parser rules:

- `_parse_frontmatter(raw: str) -> tuple[dict[str, str], str]`
- `_parse_skill_file(path: Path, source: SkillSource) -> SkillDefinition`
- `_validate_skill_name(name: str) -> None`
- `_discover_skill_files(skills_dir: Path) -> list[tuple[Path, SkillSource]]`

The frontmatter parser reads only single-line `key: value` pairs. Unsupported lines are ignored so malformed metadata does not crash discovery.

- [ ] **Step 2: Implement `SkillRegistry` and built-in skill**

Implement:

```python
class SkillRegistry:
    """保存可被 Capability Plane 摘要和 `load_skill` 使用的 skills。"""

    def __init__(self, builtin_skills: Iterable[SkillDefinition] = ()) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        for skill in builtin_skills:
            self.register(skill)

    @classmethod
    def from_paths(
        cls,
        skill_dirs: Iterable[Path],
        *,
        allowed: set[str] | None = None,
        builtin_skills: Iterable[SkillDefinition] = (),
    ) -> "SkillRegistry":
        registry = cls(builtin_skills=builtin_skills)
        for skill_dir in skill_dirs:
            for path, source in _discover_skill_files(Path(skill_dir)):
                skill = _parse_skill_file(path, source)
                if source != "learned" and allowed is not None and skill.name not in allowed:
                    continue
                if skill.name not in registry._skills:
                    registry.register(skill)
        return registry

    def register(self, skill: SkillDefinition) -> None:
        _validate_skill_name(skill.name)
        if skill.name in self._skills:
            raise ValueError(f"duplicate skill: {skill.name}")
        self._skills[skill.name] = skill

    def load(self, skill_name: str) -> SkillLoadResult:
        try:
            skill = self._skills[skill_name]
        except KeyError as error:
            raise KeyError(skill_name) from error
        return SkillLoadResult(name=skill.name, content=skill.content)

    def available_skill_names(self) -> list[str]:
        return sorted(self._skills)

    def capability_declarations(self) -> list[SkillDeclaration]:
        return [
            SkillDeclaration(name=skill.name, when_to_use=skill.when_to_use)
            for skill in self._skills.values()
        ]
```

Implement `builtin_schema_template_skill()` returning a `SkillDefinition` named `schema-template`. The content must mention `declare_schema`, `extend_schema`, `update_state`, `start_chapter`, `task_goal`, `constraints`, `key_decisions`, `verified_facts`, `open_questions`, and `next_steps`.

- [ ] **Step 3: Implement `load_skill` provider tool registration**

In `skills.py`, implement:

```python
def register_skill_loader_tool(
    tool_registry: ToolRegistry,
    skill_registry: SkillRegistry,
) -> None:
    """把 `load_skill` 注册成 provider-callable skill tool。"""

    def load_skill(arguments: dict[str, object]) -> str:
        skill_name = str(arguments.get("skill_name", ""))
        try:
            return skill_registry.load(skill_name).render_tool_result()
        except KeyError:
            return json.dumps(
                {
                    "error": f"Skill '{skill_name}' not found",
                    "available_skills": skill_registry.available_skill_names(),
                },
                ensure_ascii=False,
            )

    tool_registry.register(
        RegisteredTool(
            name="load_skill",
            description=(
                "Load a skill's full instructions by name. "
                "Use this before tasks that match an available skill summary."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Name of the skill to load.",
                    },
                },
                "required": ["skill_name"],
                "additionalProperties": False,
            },
            handler=load_skill,
            kind="skill",
        ),
    )
```

- [ ] **Step 4: Extend `ToolKind` and registry filters**

In `src/agentos/capabilities/tools.py`, change:

```python
ToolKind = Literal["external", "context", "skill", "mcp"]
```

In `src/agentos/capabilities/registry.py`, update `provider_tool_specs` to accept:

```python
def provider_tool_specs(
    self,
    kinds: set[ToolKind] | None = None,
) -> list[ProviderToolSpec]:
```

Default `kinds` should be `{"external", "skill", "mcp"}`. Keep context protocol tools supplied by `context_protocol_tool_specs()`.

- [ ] **Step 5: Export public names**

Update `src/agentos/capabilities/__init__.py` to export:

```python
from agentos.capabilities.skills import (
    SkillDefinition,
    SkillLoadResult,
    SkillRegistry,
    builtin_schema_template_skill,
    register_skill_loader_tool,
)
```

- [ ] **Step 6: Run skill tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/capabilities/test_skills.py tests/context/test_capability_plane_phase5.py -q
```

Expected: all tests in these two files pass.

---

## Task 3: Phase 5 MCP Tests

**Files:**

- Create: `tests/capabilities/test_mcp.py`
- Create: `tests/capabilities/test_tool_registry_phase5.py`
- Modify later: `src/agentos/capabilities/mcp.py`
- Modify later: `src/agentos/capabilities/router.py`

- [ ] **Step 1: Write failing MCP registry and adapter tests**

Add this to `tests/capabilities/test_mcp.py`:

```python
import pytest

from agentos.capabilities.mcp import (
    MCPRegistry,
    MCPServerRegistration,
    MCPToolAdapter,
    MCPToolInfo,
)
from agentos.providers import ProviderToolCall


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_tools(self) -> list[MCPToolInfo]:
        return [
            MCPToolInfo(
                name="create_issue",
                description="Create an issue.",
                input_schema={
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            ),
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> str:
        self.calls.append((tool_name, dict(arguments)))
        return f"{tool_name}:{arguments['title']}"


def test_mcp_registry_exports_provider_specs_and_server_summaries() -> None:
    client = FakeMCPClient()
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="github",
            description="Manage GitHub issues.",
            endpoint="stdio:npx github",
            client=client,
        ),
    )
    registry.refresh()

    specs = registry.provider_tool_specs()
    declarations = registry.capability_declarations()

    assert specs[0]["function"]["name"] == "mcp__github__create_issue"
    assert specs[0]["function"]["parameters"]["required"] == ["title"]
    assert declarations[0].name == "github"
    assert declarations[0].tool_prefix == "mcp__github__<tool>"


def test_mcp_tool_adapter_executes_prefixed_provider_call() -> None:
    client = FakeMCPClient()
    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="github",
            description="Manage GitHub issues.",
            client=client,
        ),
    )
    registry.refresh()
    adapter = MCPToolAdapter(registry)

    result = adapter.execute(
        ProviderToolCall(
            id="call_1",
            name="mcp__github__create_issue",
            arguments={"title": "Bug"},
        ),
    )

    assert result.tool_call_id == "call_1"
    assert result.content == "create_issue:Bug"
    assert client.calls == [("create_issue", {"title": "Bug"})]


def test_mcp_registry_rejects_invalid_server_names() -> None:
    registry = MCPRegistry()

    with pytest.raises(ValueError, match="invalid MCP server name"):
        registry.register(
            MCPServerRegistration(
                name="../github",
                description="Bad name.",
                client=FakeMCPClient(),
            ),
        )


def test_mcp_registry_rejects_duplicate_provider_names() -> None:
    class DuplicateClient(FakeMCPClient):
        def list_tools(self) -> list[MCPToolInfo]:
            return [
                MCPToolInfo(name="same", description="First.", input_schema={"type": "object"}),
                MCPToolInfo(name="same", description="Second.", input_schema={"type": "object"}),
            ]

    registry = MCPRegistry()
    registry.register(
        MCPServerRegistration(
            name="github",
            description="Manage GitHub issues.",
            client=DuplicateClient(),
        ),
    )

    with pytest.raises(ValueError, match="duplicate MCP tool"):
        registry.refresh()
```

- [ ] **Step 2: Write failing ToolRegistry Phase 5 tests**

Add this to `tests/capabilities/test_tool_registry_phase5.py`:

```python
from agentos.capabilities import RegisteredTool, ToolRegistry


def test_provider_tool_specs_include_external_skill_and_mcp_by_default() -> None:
    registry = ToolRegistry()
    for name, kind in [
        ("external_tool", "external"),
        ("load_skill", "skill"),
        ("mcp__github__create_issue", "mcp"),
        ("internal_context_tool", "context"),
    ]:
        registry.register(
            RegisteredTool(
                name=name,
                description=f"{name} description.",
                parameters={"type": "object"},
                handler=lambda arguments: "ok",
                kind=kind,
            ),
        )

    names = [spec["function"]["name"] for spec in registry.provider_tool_specs()]

    assert names == [
        "external_tool",
        "load_skill",
        "mcp__github__create_issue",
    ]
```

- [ ] **Step 3: Run MCP tests and verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/capabilities/test_mcp.py tests/capabilities/test_tool_registry_phase5.py -q
```

Expected: failures mention missing `agentos.capabilities.mcp` or unsupported tool kind.

---

## Task 4: Implement MCP And Router Integration

**Files:**

- Create: `src/agentos/capabilities/mcp.py`
- Modify: `src/agentos/capabilities/__init__.py`
- Modify: `src/agentos/capabilities/router.py`
- Modify: `src/agentos/capabilities/registry.py`

- [ ] **Step 1: Implement MCP public objects**

Create `src/agentos/capabilities/mcp.py` with:

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from agentos.capabilities.executor import ToolExecutionResult
from agentos.context.projection import MCPServerDeclaration
from agentos.providers import ProviderToolCall, ProviderToolSpec


_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True, slots=True)
class MCPToolInfo:
    """MCP server 暴露的单个工具元数据。"""

    name: str
    description: str
    input_schema: dict[str, object] = field(default_factory=dict)


class MCPClient(Protocol):
    """ToolCallRouter 视角下的 MCP client 边界。"""

    def list_tools(self) -> list[MCPToolInfo]:
        """返回当前 server 的工具列表。"""

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> str:
        """调用 server-local MCP tool 并返回文本结果。"""


@dataclass(frozen=True, slots=True)
class MCPServerRegistration:
    """一个 MCP server 的注册信息。"""

    name: str
    description: str
    client: MCPClient
    endpoint: str | None = None
    allowed_tools: set[str] | None = None
```

Implement `MCPRegistry`:

- `register(server: MCPServerRegistration) -> None`
- `refresh() -> None`
- `provider_tool_specs() -> list[ProviderToolSpec]`
- `capability_declarations() -> list[MCPServerDeclaration]`
- `resolve_provider_tool(provider_name: str) -> tuple[MCPServerRegistration, str]`

Implement `MCPToolAdapter.execute(tool_call: ProviderToolCall) -> ToolExecutionResult`.

- [ ] **Step 2: Register MCP provider tools into `ToolRegistry` where needed**

Keep `MCPRegistry` as the owner of MCP metadata. `ToolRegistry` should not duplicate MCP clients. If a caller needs a unified list of provider specs, use:

```python
[
    *context_protocol_tool_specs(),
    *tool_registry.provider_tool_specs(),
    *mcp_registry.provider_tool_specs(),
]
```

This avoids two sources of truth for MCP client routing.

- [ ] **Step 3: Extend `ToolCallRouter`**

Modify `ToolCallRouter` fields:

```python
mcp_adapter: MCPToolAdapter | None = None
```

Modify `tool_specs()`:

```python
specs = [*context_protocol_tool_specs(), *self.tool_registry.provider_tool_specs()]
if self.mcp_adapter is not None:
    specs.extend(self.mcp_adapter.provider_tool_specs())
return specs
```

Modify `execute_tool_call(...)`:

```python
self.security_policy.ensure_tool_allowed(tool_call.name)
if tool_call.name in CONTEXT_PROTOCOL_TOOL_NAMES:
    return self._execute_context_tool(tool_call)
if tool_call.name.startswith("mcp__"):
    if self.mcp_adapter is None:
        raise RuntimeError("mcp adapter is required for MCP tool calls")
    return self.mcp_adapter.execute(tool_call)
return self._tool_executor().execute(tool_call)
```

- [ ] **Step 4: Export MCP names**

Update `src/agentos/capabilities/__init__.py`:

```python
from agentos.capabilities.mcp import (
    MCPClient,
    MCPRegistry,
    MCPServerRegistration,
    MCPToolAdapter,
    MCPToolInfo,
)
```

- [ ] **Step 5: Run Phase 5 capability tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/capabilities/test_skills.py tests/capabilities/test_mcp.py tests/capabilities/test_tool_registry_phase5.py tests/context/test_capability_plane_phase5.py -q
```

Expected: all listed Phase 5 capability tests pass.

---

## Task 5: Phase 5 End-To-End Tool Loop Tests

**Files:**

- Create: `tests/runtime/test_skill_mcp_tool_loop.py`
- Modify later: `src/agentos/runtime/provider_request_builder.py` only if tools must come from router dynamically.
- Modify later: `src/agentos/runtime/query_loop.py` only if current tool schema path is insufficient.

- [ ] **Step 1: Write failing skill and MCP loop tests**

Add this file:

```python
from pathlib import Path

from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.capabilities.mcp import MCPRegistry, MCPServerRegistration, MCPToolAdapter, MCPToolInfo
from agentos.capabilities.skills import SkillRegistry, register_skill_loader_tool
from agentos.context import ContextRenderer, ContextRuntime
from agentos.context.projection import CapabilityPlane
from agentos.messages import MessageRuntime
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import ProviderRequestBuilder, QueryLoop


class FakeMCPClient:
    def list_tools(self) -> list[MCPToolInfo]:
        return [
            MCPToolInfo(
                name="lookup",
                description="Lookup a value.",
                input_schema={
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            ),
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> str:
        return f"{tool_name}:{arguments['key']}"


def test_query_loop_loads_skill_body_through_tool_result(tmp_path: Path) -> None:
    (tmp_path / "review.md").write_text(
        "---\nname: code-review\ndescription: Review code.\nwhen_to_use: 审查代码时使用。\n---\n# Review Body\nFind bugs first.\n",
        encoding="utf-8",
    )
    skill_registry = SkillRegistry.from_paths([tmp_path])
    tool_registry = ToolRegistry()
    register_skill_loader_tool(tool_registry, skill_registry)
    messages = MessageRuntime()
    router = ToolCallRouter(tool_registry=tool_registry)
    provider = FakeProvider(
        [
            ProviderResponse(
                content="",
                tool_calls=[
                    ProviderToolCall(
                        id="call_skill",
                        name="load_skill",
                        arguments={"skill_name": "code-review"},
                    ),
                ],
            ),
            "I will follow the review skill.",
        ],
    )
    loop = QueryLoop(
        context_runtime=ContextRuntime(),
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(
                capability_plane=CapabilityPlane(
                    skills=skill_registry.capability_declarations(),
                ),
            ),
            message_runtime=messages,
            tools=router.tool_specs(),
        ),
        provider=provider,
        tool_call_router=router,
    )

    response = loop.run_turn("Review this code")

    assert response == "I will follow the review skill."
    assert "# Review Body" in provider.requests[1].messages[-1]["content"]
    assert "# Review Body" not in provider.requests[0].system


def test_query_loop_executes_mcp_tool_call() -> None:
    mcp_registry = MCPRegistry()
    mcp_registry.register(
        MCPServerRegistration(
            name="docs",
            description="Documentation lookup.",
            client=FakeMCPClient(),
        ),
    )
    mcp_registry.refresh()
    mcp_adapter = MCPToolAdapter(mcp_registry)
    router = ToolCallRouter(tool_registry=ToolRegistry(), mcp_adapter=mcp_adapter)
    messages = MessageRuntime()
    provider = FakeProvider(
        [
            ProviderResponse(
                content="",
                tool_calls=[
                    ProviderToolCall(
                        id="call_mcp",
                        name="mcp__docs__lookup",
                        arguments={"key": "phase5"},
                    ),
                ],
            ),
            "MCP result consumed.",
        ],
    )
    loop = QueryLoop(
        context_runtime=ContextRuntime(),
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(
                capability_plane=CapabilityPlane(
                    mcp_servers=mcp_registry.capability_declarations(),
                ),
            ),
            message_runtime=messages,
            tools=router.tool_specs(),
        ),
        provider=provider,
        tool_call_router=router,
    )

    response = loop.run_turn("Lookup docs")

    assert response == "MCP result consumed."
    assert provider.requests[0].tools[-1]["function"]["name"] == "mcp__docs__lookup"
    assert provider.requests[1].messages[-1]["content"] == "lookup:phase5"
```

- [ ] **Step 2: Run end-to-end tests and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_skill_mcp_tool_loop.py -q
```

Expected: failure points to router or provider tool spec integration gaps.

- [ ] **Step 3: Implement missing integration**

If `ProviderRequestBuilder` currently receives a static `tools` list, keep that design for Phase 5 by passing `router.tool_specs()` from the composition root. Do not make `QueryLoop` read registry internals.

If tests fail because router does not expose MCP specs, complete Task 4 Step 3.

- [ ] **Step 4: Run all Phase 5 tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/capabilities/test_skills.py tests/capabilities/test_mcp.py tests/capabilities/test_tool_registry_phase5.py tests/context/test_capability_plane_phase5.py tests/runtime/test_skill_mcp_tool_loop.py -q
```

Expected: all Phase 5 tests pass.

---

## Task 6: Persistence Snapshot Tests

**Files:**

- Create: `tests/persistence/test_serializers.py`
- Create later: `src/agentos/persistence/base.py`
- Create later: `src/agentos/persistence/serializers.py`
- Modify later: `src/agentos/messages/store.py`
- Modify later: `src/agentos/messages/window.py`
- Modify later: `src/agentos/messages/runtime.py`
- Modify later: `src/agentos/compression/index.py`
- Modify later: `src/agentos/runtime/session.py`

- [ ] **Step 1: Write failing serializer tests**

Add:

```python
from agentos.compression import CompressionIndex
from agentos.context import ContextState, WorkingStateField, WorkingStateSchema
from agentos.context.state import CompressedSegment
from agentos.messages import MessageRuntime, ToolCall
from agentos.persistence.base import SessionSnapshot
from agentos.persistence.serializers import (
    compression_index_from_dict,
    compression_index_to_dict,
    context_state_from_dict,
    context_state_to_dict,
    message_runtime_from_dict,
    message_runtime_to_dict,
    session_snapshot_from_dict,
    session_snapshot_to_dict,
    session_state_from_dict,
    session_state_to_dict,
)
from agentos.runtime import SessionState


def test_context_state_round_trips_without_exposing_mutable_lists() -> None:
    state = ContextState(
        working_state_schema=WorkingStateSchema(
            fields=[
                WorkingStateField(
                    name="task_goal",
                    type="str",
                    purpose="当前任务目标和完成标准",
                ),
            ],
        ),
        working_state={"task_goal": "Persist context."},
        compressed_history=[
            CompressedSegment(id="seg_1", topic="history", summary="Old details."),
        ],
        inherited_state=["Keep architecture boundaries."],
        memory_context=["User prefers Chinese."],
    )

    restored = context_state_from_dict(context_state_to_dict(state))

    assert restored.working_state["task_goal"] == "Persist context."
    assert restored.compressed_history == state.compressed_history
    assert restored.inherited_state == ("Keep architecture boundaries.",)
    assert restored.memory_context == ("User prefers Chinese.",)


def test_message_runtime_round_trips_originals_active_refs_and_next_id() -> None:
    runtime = MessageRuntime()
    user = runtime.append_user("Need docs")
    assistant = runtime.append_assistant(
        "",
        tool_calls=[
            ToolCall(id="call_1", name="load_skill", arguments={"skill_name": "code-review"}),
        ],
    )
    tool = runtime.append_tool_result("call_1", "Skill body")
    runtime.inject_temporary_recalled([user.id])

    restored = message_runtime_from_dict(message_runtime_to_dict(runtime))
    new_message = restored.append_user("Next")

    assert [message.id for message in restored.store.all()] == [
        user.id,
        assistant.id,
        tool.id,
        "msg_4",
    ]
    assert new_message.id == "msg_4"
    assert [ref.message_id for ref in restored.active_window.refs][:1] == [user.id]
    assert restored.active_window.refs[0].temporary is True


def test_compression_index_round_trips_segment_source_refs() -> None:
    index = CompressionIndex()
    index.record("seg_1", ["msg_1", "msg_2"])

    restored = compression_index_from_dict(compression_index_to_dict(index))

    assert restored.source_refs("seg_1") == ["msg_1", "msg_2"]


def test_session_snapshot_round_trips_full_runtime_state() -> None:
    session = SessionState(id="session_1")
    session.new_turn("hello")
    context_state = ContextState(working_state={"task_goal": "Recover session."})
    messages = MessageRuntime()
    messages.append_user("hello")
    index = CompressionIndex()
    index.record("seg_1", ["msg_1"])
    snapshot = SessionSnapshot(
        session_state=session,
        context_state=context_state,
        message_runtime=messages,
        compression_index=index,
        next_segment_number=2,
        event_records=(),
    )

    restored = session_snapshot_from_dict(session_snapshot_to_dict(snapshot))

    assert restored.session_state.id == "session_1"
    assert restored.session_state.new_turn("next").id == "turn_2"
    assert restored.context_state.working_state["task_goal"] == "Recover session."
    assert restored.message_runtime.store.get("msg_1").content == "hello"
    assert restored.compression_index.source_refs("seg_1") == ["msg_1"]
    assert restored.next_segment_number == 2
```

- [ ] **Step 2: Run serializer tests and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/persistence/test_serializers.py -q
```

Expected: failures mention missing `agentos.persistence`.

---

## Task 7: Implement Persistence Snapshots

**Files:**

- Create: `src/agentos/persistence/__init__.py`
- Create: `src/agentos/persistence/base.py`
- Create: `src/agentos/persistence/serializers.py`
- Modify: `src/agentos/messages/store.py`
- Modify: `src/agentos/messages/window.py`
- Modify: `src/agentos/messages/runtime.py`
- Modify: `src/agentos/compression/index.py`
- Modify: `src/agentos/runtime/session.py`

- [ ] **Step 1: Implement snapshot dataclasses and Protocol**

In `src/agentos/persistence/base.py`:

```python
from dataclasses import dataclass, field
from typing import Protocol

from agentos.compression import CompressionIndex
from agentos.context import ContextState
from agentos.messages import MessageRuntime
from agentos.runtime import SessionState


SNAPSHOT_VERSION = 1


class SnapshotVersionError(ValueError):
    """持久化 snapshot 版本不兼容。"""


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """可持久化恢复的 agentos session 状态。"""

    session_state: SessionState
    context_state: ContextState
    message_runtime: MessageRuntime
    compression_index: CompressionIndex
    next_segment_number: int = 1
    event_records: tuple[object, ...] = field(default_factory=tuple)
    version: int = SNAPSHOT_VERSION


class SessionPersistence(Protocol):
    """session snapshot 存储边界。"""

    def save(self, snapshot: SessionSnapshot) -> None:
        """保存一个 session snapshot。"""

    def load(self, session_id: str) -> SessionSnapshot:
        """读取一个 session snapshot。"""

    def list_ids(self) -> list[str]:
        """列出已保存的 session ids。"""

    def delete(self, session_id: str) -> None:
        """删除一个 session snapshot。"""
```

- [ ] **Step 2: Add explicit restore helpers to runtime objects**

Implement these narrow helpers:

- `MessageStore.from_messages(messages: list[Message], next_id: int) -> MessageStore`
- `MessageStore.next_id_number() -> int`
- `ActiveWindow.snapshot_refs() -> tuple[MessageRef, ...]`
- `ActiveWindow.from_refs(refs: list[MessageRef]) -> ActiveWindow`
- `MessageRuntime.from_parts(store: MessageStore, active_window: ActiveWindow) -> MessageRuntime`
- `CompressionIndex.snapshot() -> dict[str, tuple[str, ...]]`
- `CompressionIndex.from_snapshot(snapshot: dict[str, list[str] | tuple[str, ...]]) -> CompressionIndex`
- `SessionState.next_turn_number() -> int`
- `SessionState.from_snapshot(id: str, status: SessionStatus, next_turn_number: int) -> SessionState`

Each helper should copy input collections so restored objects do not share mutable lists with caller-owned snapshots.

- [ ] **Step 3: Implement serializer functions**

In `src/agentos/persistence/serializers.py`, implement:

- `working_state_schema_to_dict(...)`
- `working_state_schema_from_dict(...)`
- `context_state_to_dict(...)`
- `context_state_from_dict(...)`
- `message_to_dict(...)`
- `message_from_dict(...)`
- `message_runtime_to_dict(...)`
- `message_runtime_from_dict(...)`
- `compression_index_to_dict(...)`
- `compression_index_from_dict(...)`
- `session_state_to_dict(...)`
- `session_state_from_dict(...)`
- `session_snapshot_to_dict(...)`
- `session_snapshot_from_dict(...)`

Serialization shapes:

```python
context = {
    "working_state_schema": {"fields": [{"name": "...", "type": "...", "purpose": "..."}]},
    "working_state": {"task_goal": "Persist context."},
    "compressed_history": [{"id": "seg_1", "topic": "history", "summary": "Old details."}],
    "inherited_state": ["Keep architecture boundaries."],
    "memory_context": ["User prefers Chinese."],
}

messages = {
    "store": {
        "next_id": 4,
        "messages": [
            {
                "id": "msg_1",
                "role": "user",
                "content": "hello",
                "tool_calls": [],
                "tool_call_id": None,
            },
        ],
    },
    "active_window": {
        "refs": [{"message_id": "msg_1", "temporary": False}],
    },
}
```

- [ ] **Step 4: Export persistence names**

In `src/agentos/persistence/__init__.py`, export:

```python
from agentos.persistence.base import (
    SNAPSHOT_VERSION,
    SessionPersistence,
    SessionSnapshot,
    SnapshotVersionError,
)
```

- [ ] **Step 5: Run serializer tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/persistence/test_serializers.py -q
```

Expected: serializer tests pass.

---

## Task 8: File And SQLite Persistence

**Files:**

- Create: `tests/persistence/test_filesystem.py`
- Create: `tests/persistence/test_sqlite.py`
- Create: `src/agentos/persistence/memory.py`
- Create: `src/agentos/persistence/filesystem.py`
- Create: `src/agentos/persistence/sqlite.py`
- Modify: `src/agentos/persistence/__init__.py`

- [ ] **Step 1: Write failing persistence backend tests**

Add `tests/persistence/test_filesystem.py`:

```python
from pathlib import Path

import pytest

from agentos.context import ContextState
from agentos.messages import MessageRuntime
from agentos.compression import CompressionIndex
from agentos.persistence import FileSystemPersistence, SessionSnapshot
from agentos.runtime import SessionState


def make_snapshot(session_id: str = "session_1") -> SessionSnapshot:
    messages = MessageRuntime()
    messages.append_user("hello")
    return SessionSnapshot(
        session_state=SessionState(id=session_id),
        context_state=ContextState(working_state={"task_goal": "Persist."}),
        message_runtime=messages,
        compression_index=CompressionIndex(),
    )


def test_file_system_persistence_round_trips_snapshot(tmp_path: Path) -> None:
    store = FileSystemPersistence(tmp_path)
    store.save(make_snapshot())

    restored = store.load("session_1")

    assert restored.session_state.id == "session_1"
    assert restored.message_runtime.store.get("msg_1").content == "hello"
    assert store.list_ids() == ["session_1"]


def test_file_system_persistence_rejects_path_traversal(tmp_path: Path) -> None:
    store = FileSystemPersistence(tmp_path)

    with pytest.raises(ValueError, match="invalid session id"):
        store.load("../outside")
```

Add `tests/persistence/test_sqlite.py`:

```python
from pathlib import Path

from agentos.compression import CompressionIndex
from agentos.context import ContextState
from agentos.messages import MessageRuntime
from agentos.observability.events import EventLog
from agentos.persistence import SQLitePersistence, SessionSnapshot
from agentos.runtime import SessionState, TurnStartedEvent


def make_snapshot(session_id: str = "session_1") -> SessionSnapshot:
    messages = MessageRuntime()
    messages.append_user("hello")
    event_log = EventLog()
    event_log.record(TurnStartedEvent(session_id=session_id, turn_id="turn_1", user_input="hello"))
    return SessionSnapshot(
        session_state=SessionState(id=session_id),
        context_state=ContextState(),
        message_runtime=messages,
        compression_index=CompressionIndex(),
        event_records=tuple(event_log.records),
    )


def test_sqlite_persistence_round_trips_latest_snapshot_and_events(tmp_path: Path) -> None:
    store = SQLitePersistence(tmp_path / "sessions.sqlite3")
    store.save(make_snapshot())

    restored = store.load("session_1")

    assert restored.message_runtime.store.get("msg_1").content == "hello"
    assert restored.event_records[0].event_type == "TurnStartedEvent"
    assert store.list_ids() == ["session_1"]


def test_sqlite_persistence_delete_removes_snapshot(tmp_path: Path) -> None:
    store = SQLitePersistence(tmp_path / "sessions.sqlite3")
    store.save(make_snapshot())
    store.delete("session_1")

    assert store.list_ids() == []
```

- [ ] **Step 2: Run backend tests and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/persistence/test_filesystem.py tests/persistence/test_sqlite.py -q
```

Expected: failures mention missing persistence backends or missing `EventLog`.

- [ ] **Step 3: Implement `MemoryPersistence`**

In `src/agentos/persistence/memory.py`, implement dict-backed `save`, `load`, `list_ids`, and `delete`. Store serialized dicts internally and reconstruct on load so tests catch serializer errors.

- [ ] **Step 4: Implement `FileSystemPersistence`**

In `src/agentos/persistence/filesystem.py`:

- Create base directory in `__init__`.
- Resolve paths with `(base_dir / f"{session_id}.json").resolve()`.
- Check `path.is_relative_to(base_dir.resolve())`.
- Write through a temporary `*.tmp` file and replace with `Path.replace`.
- Read JSON with UTF-8.
- Raise `KeyError(session_id)` for missing files.

- [ ] **Step 5: Implement `SQLitePersistence`**

In `src/agentos/persistence/sqlite.py`, create schema:

```sql
CREATE TABLE IF NOT EXISTS snapshots (
  session_id TEXT PRIMARY KEY,
  version INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_records (
  session_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (session_id, sequence)
);
```

`save(snapshot)` must upsert `snapshots` and replace event records for that session using one SQLite transaction.

- [ ] **Step 6: Export backends**

Update `src/agentos/persistence/__init__.py`:

```python
from agentos.persistence.filesystem import FileSystemPersistence
from agentos.persistence.memory import MemoryPersistence
from agentos.persistence.sqlite import SQLitePersistence
```

- [ ] **Step 7: Run persistence backend tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/persistence/test_serializers.py tests/persistence/test_filesystem.py tests/persistence/test_sqlite.py -q
```

Expected: all persistence tests pass.

---

## Task 9: Observability Event Log Tests

**Files:**

- Create: `tests/observability/test_event_log.py`
- Create later: `src/agentos/observability/events.py`
- Modify later: `src/agentos/runtime/event_bus.py`
- Modify later: `src/agentos/context/runtime.py`
- Modify later: `src/agentos/compression/runtime.py`
- Modify later: `src/agentos/recall/runtime.py`
- Modify later: `src/agentos/runtime/query_loop.py`

- [ ] **Step 1: Write failing event log tests**

Add:

```python
from agentos.compression import CompressionRuntime
from agentos.context import ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.observability.events import EventLog
from agentos.policies import BudgetPolicy
from agentos.recall import RecallRuntime
from agentos.runtime import (
    EventBus,
    ProviderResponseReceivedEvent,
    TurnStartedEvent,
)


def test_event_log_records_typed_events_in_order() -> None:
    log = EventLog()
    bus = EventBus(subscribers=[log])

    bus.emit(TurnStartedEvent(session_id="s1", turn_id="turn_1", user_input="hello"))
    bus.emit(ProviderResponseReceivedEvent(session_id="s1", turn_id="turn_1"))

    assert [record.sequence for record in log.records] == [1, 2]
    assert [record.event_type for record in log.records] == [
        "TurnStartedEvent",
        "ProviderResponseReceivedEvent",
    ]
    assert log.records[0].payload["user_input"] == "hello"


def test_context_compression_and_recall_emit_traceable_events() -> None:
    log = EventLog()
    bus = EventBus(subscribers=[log])
    context = ContextRuntime(event_bus=bus, session_id="s1")
    messages = MessageRuntime()
    messages.append_user("old detail")
    messages.append_assistant("old answer")
    messages.append_user("current task")
    compression = CompressionRuntime(
        context_runtime=context,
        message_runtime=messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
        event_bus=bus,
        session_id="s1",
    )
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="str",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    context.update_state("task_goal", "Trace runtime events.")
    segment = compression.maybe_compress()
    RecallRuntime(
        compression_index=compression.index,
        message_runtime=messages,
        event_bus=bus,
        session_id="s1",
    ).recall_context(segment.id)

    event_types = [record.event_type for record in log.records]
    assert "WorkingStateSchemaDeclaredEvent" in event_types
    assert "WorkingStateUpdatedEvent" in event_types
    assert "CompressionCompletedEvent" in event_types
    assert "RecallContextInjectedEvent" in event_types
```

- [ ] **Step 2: Run event log tests and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_event_log.py -q
```

Expected: failures mention missing observability package or missing event dataclasses.

---

## Task 10: Implement Event Log And Instrumentation

**Files:**

- Create: `src/agentos/observability/__init__.py`
- Create: `src/agentos/observability/events.py`
- Modify: `src/agentos/runtime/event_bus.py`
- Modify: `src/agentos/context/runtime.py`
- Modify: `src/agentos/compression/runtime.py`
- Modify: `src/agentos/recall/runtime.py`
- Modify: `src/agentos/runtime/query_loop.py`

- [ ] **Step 1: Implement EventRecord and EventLog**

In `src/agentos/observability/events.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from agentos.events import AgentEvent, EventSubscriber


@dataclass(frozen=True, slots=True)
class EventRecord:
    """append-only runtime event 记录。"""

    sequence: int
    event_type: str
    session_id: str | None
    turn_id: str | None
    payload: dict[str, object]
    created_at: str


@dataclass(slots=True)
class EventLog:
    """内存中的 append-only event log。"""

    records: list[EventRecord] = field(default_factory=list)

    def record(self, event: AgentEvent) -> None:
        payload = asdict(event) if is_dataclass(event) else {}
        self.records.append(
            EventRecord(
                sequence=len(self.records) + 1,
                event_type=type(event).__name__,
                session_id=event.session_id,
                turn_id=event.turn_id,
                payload=payload,
                created_at=datetime.now(timezone.utc).isoformat(),
            ),
        )
```

Add `event_record_to_dict(...)` and `event_record_from_dict(...)` for persistence serializers.

- [ ] **Step 2: Extend EventBus with subscribers**

In `src/agentos/runtime/event_bus.py`, add:

```python
subscribers: list[EventSubscriber] = field(default_factory=list)
subscriber_errors: list[str] = field(default_factory=list)
```

Update `emit` so each subscriber `record(event)` is called after `self.events.append(event)` and before hook dispatch. Subscriber exceptions are appended to `subscriber_errors` and do not stop execution.

Use a local Protocol in `runtime/event_bus.py` to avoid importing `observability` into `runtime`.

- [ ] **Step 3: Add Phase 6 event dataclasses**

Add typed dataclasses in `runtime/event_bus.py`:

- `WorkingStateSchemaDeclaredEvent(fields: tuple[str, ...])`
- `WorkingStateUpdatedEvent(field_name: str)`
- `WorkingStateSchemaExtendedEvent(fields: tuple[str, ...])`
- `ChapterStartedEvent(fields: tuple[str, ...])`
- `InheritedStateSetEvent(item_count: int)`
- `MemoryContextSetEvent(item_count: int)`
- `CompressedSegmentAppendedEvent(segment_id: str)`
- `CompressionSkippedEvent(reason: str)`
- `CompressionCompletedEvent(segment_id: str, source_message_ids: tuple[str, ...])`
- `RecallContextRequestedEvent(handle: str)`
- `RecallContextFailedEvent(handle: str, error: str)`
- `RecallContextInjectedEvent(handle: str, message_ids: tuple[str, ...])`
- `SnapshotSavedEvent(snapshot_session_id: str)`
- `SnapshotLoadedEvent(snapshot_session_id: str)`

Also extend existing events:

- `UserMessageAppendedEvent(message_id: str = "")`
- `AssistantMessageAppendedEvent(message_id: str = "")`
- `ToolResultAppendedEvent(tool_name: str = "", tool_call_id: str = "", message_id: str = "")`
- `ToolCallRequestedEvent(tool_name: str = "", tool_call_id: str = "")`
- `ToolExecutionStartedEvent(tool_name: str = "", tool_call_id: str = "")`
- `ToolExecutionCompletedEvent(tool_name: str = "", tool_call_id: str = "")`

- [ ] **Step 4: Instrument context, compression and recall**

Add optional fields where needed:

```python
event_bus: EventBus | None = None
session_id: str | None = None
turn_id: str | None = None
```

`ContextRuntime` emits events after successful state mutation only.

`CompressionRuntime` emits:

- `CompressionSkippedEvent(reason="temporary_recalled_refs")` when temporary recalled refs block compression.
- `CompressionSkippedEvent(reason="under_budget")` when no ids are selected.
- `CompressionSkippedEvent(reason="would_clear_window")` when selected ids would clear all active messages.
- `CompressedSegmentAppendedEvent` after context state append.
- `CompressionCompletedEvent` after index record and active refs removal.

`RecallRuntime` emits:

- `RecallContextRequestedEvent` before lookup.
- `RecallContextFailedEvent` before raising unknown handle.
- `RecallContextInjectedEvent` after temporary refs are injected.

- [ ] **Step 5: Include ids in QueryLoop events**

Update `QueryLoop.run_turn` and `_run_provider_loop` so message append returns are captured:

```python
user = self.message_runtime.append_user(user_message)
self._emit(UserMessageAppendedEvent(message_id=user.id, **self._event_context(turn)))
```

Do the same for assistant and tool result messages. Use provider `tool_call.id` for `tool_call_id`.

- [ ] **Step 6: Export observability names**

In `src/agentos/observability/__init__.py`:

```python
from agentos.observability.events import EventLog, EventRecord, EventSubscriber
```

- [ ] **Step 7: Run event log tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_event_log.py tests/runtime/test_typed_events.py tests/runtime/test_tool_loop.py -q
```

Expected: all listed runtime and observability event tests pass.

---

## Task 11: Trace Adapters And Debug Projection Tests

**Files:**

- Create: `tests/observability/test_traces.py`
- Create: `tests/context/test_debug_projection.py`
- Create later: `src/agentos/observability/traces.py`
- Create later: `src/agentos/observability/otel.py`
- Create later: `src/agentos/observability/langfuse.py`
- Create later: `src/agentos/context/debug_projection.py`

- [ ] **Step 1: Write failing trace adapter tests**

Add `tests/observability/test_traces.py`:

```python
from agentos.observability.events import EventLog
from agentos.observability.langfuse import LangfuseAdapter
from agentos.observability.otel import OTelAdapter
from agentos.observability.traces import EventTraceProjector
from agentos.runtime import EventBus, ToolExecutionCompletedEvent, ToolExecutionStartedEvent


class FakeSpan:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, object] = {}
        self.ended = False

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def end(self) -> None:
        self.ended = True


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []

    def start_span(self, name: str) -> FakeSpan:
        span = FakeSpan(name)
        self.spans.append(span)
        return span


class FakeLangfuseClient:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def trace(self, **kwargs: object) -> None:
        self.events.append(dict(kwargs))


def test_event_trace_projector_records_tool_trace_without_message_content() -> None:
    log = EventLog()
    bus = EventBus(subscribers=[log])
    bus.emit(ToolExecutionStartedEvent(session_id="s1", turn_id="turn_1", tool_name="read_file", tool_call_id="call_1"))
    bus.emit(ToolExecutionCompletedEvent(session_id="s1", turn_id="turn_1", tool_name="read_file", tool_call_id="call_1"))

    records = EventTraceProjector().project(log.records)

    assert records[0].name == "tool.read_file"
    assert records[0].attributes["tool.name"] == "read_file"
    assert "content" not in records[0].attributes


def test_otel_adapter_uses_injected_tracer() -> None:
    tracer = FakeTracer()
    adapter = OTelAdapter(tracer=tracer)
    log = EventLog()
    bus = EventBus(subscribers=[log])
    bus.emit(ToolExecutionCompletedEvent(session_id="s1", turn_id="turn_1", tool_name="read_file", tool_call_id="call_1"))

    adapter.record_many(EventTraceProjector().project(log.records))

    assert tracer.spans[0].name == "tool.read_file"
    assert tracer.spans[0].attributes["tool.call_id"] == "call_1"
    assert tracer.spans[0].ended is True


def test_langfuse_adapter_uses_injected_client() -> None:
    client = FakeLangfuseClient()
    adapter = LangfuseAdapter(client=client)
    log = EventLog()
    bus = EventBus(subscribers=[log])
    bus.emit(ToolExecutionCompletedEvent(session_id="s1", turn_id="turn_1", tool_name="read_file", tool_call_id="call_1"))

    adapter.record_many(EventTraceProjector().project(log.records))

    assert client.events[0]["name"] == "tool.read_file"
    assert client.events[0]["metadata"]["tool.call_id"] == "call_1"
```

- [ ] **Step 2: Write failing debug projection tests**

Add `tests/context/test_debug_projection.py`:

```python
from agentos.compression import CompressionIndex
from agentos.context import ContextRenderer, ContextState
from agentos.context.debug_projection import render_debug_projection
from agentos.messages import MessageRuntime
from agentos.observability.events import EventLog
from agentos.runtime import EventBus, TurnStartedEvent


def test_debug_projection_exposes_runtime_metadata_explicitly() -> None:
    messages = MessageRuntime()
    message = messages.append_user("hello")
    index = CompressionIndex()
    index.record("seg_1", [message.id])
    log = EventLog()
    EventBus(subscribers=[log]).emit(
        TurnStartedEvent(session_id="session_1", turn_id="turn_1", user_input="hello"),
    )

    rendered = render_debug_projection(
        context_state=ContextState(working_state={"task_goal": "Debug."}),
        message_runtime=messages,
        compression_index=index,
        event_log=log,
    )

    assert "session_id" in rendered
    assert "message_id" in rendered
    assert "compression_id" in rendered
    assert "seg_1" in rendered


def test_default_renderer_does_not_call_debug_projection() -> None:
    rendered = ContextRenderer().render(ContextState(working_state={"task_goal": "Debug."}))

    assert "session_id" not in rendered
    assert "message_id" not in rendered
    assert "compression_id" not in rendered
```

- [ ] **Step 3: Run trace and debug tests and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_traces.py tests/context/test_debug_projection.py -q
```

Expected: failures mention missing trace adapter and debug projection modules.

---

## Task 12: Implement Trace Adapters And Debug Projection

**Files:**

- Create: `src/agentos/observability/traces.py`
- Create: `src/agentos/observability/otel.py`
- Create: `src/agentos/observability/langfuse.py`
- Create: `src/agentos/context/debug_projection.py`
- Modify: `src/agentos/observability/__init__.py`
- Modify: `src/agentos/context/__init__.py`

- [ ] **Step 1: Implement trace projection**

In `observability/traces.py`:

```python
from dataclasses import dataclass, field
from typing import Protocol

from agentos.observability.events import EventRecord


@dataclass(frozen=True, slots=True)
class TraceRecord:
    """由 EventRecord 归一化得到的 trace 记录。"""

    name: str
    session_id: str | None
    turn_id: str | None
    attributes: dict[str, object] = field(default_factory=dict)


class TraceSink(Protocol):
    """trace 输出边界。"""

    def record_many(self, records: list[TraceRecord]) -> None:
        """输出一组 trace 记录。"""


class EventTraceProjector:
    """把 event log 转换成 provider/tool/context trace。"""

    def project(self, records: list[EventRecord] | tuple[EventRecord, ...]) -> list[TraceRecord]:
        traces: list[TraceRecord] = []
        for record in records:
            payload = record.payload
            if record.event_type.startswith("ToolExecution"):
                tool_name = str(payload.get("tool_name", "unknown"))
                traces.append(
                    TraceRecord(
                        name=f"tool.{tool_name}",
                        session_id=record.session_id,
                        turn_id=record.turn_id,
                        attributes={
                            "event.type": record.event_type,
                            "tool.name": tool_name,
                            "tool.call_id": str(payload.get("tool_call_id", "")),
                        },
                    ),
                )
            elif record.event_type.startswith("Compression"):
                traces.append(
                    TraceRecord(
                        name="compression",
                        session_id=record.session_id,
                        turn_id=record.turn_id,
                        attributes={"event.type": record.event_type},
                    ),
                )
        return traces
```

- [ ] **Step 2: Implement import-free OTel adapter**

In `observability/otel.py`, accept a tracer object with `start_span(name)` and spans with `set_attribute(...)` and `end()`:

```python
class OTelAdapter:
    """通过注入 tracer 输出 TraceRecord，不引入 opentelemetry 依赖。"""

    def __init__(self, tracer: object) -> None:
        self._tracer = tracer

    def record_many(self, records: list[TraceRecord]) -> None:
        for record in records:
            span = self._tracer.start_span(record.name)
            for key, value in record.attributes.items():
                span.set_attribute(key, value)
            if record.session_id is not None:
                span.set_attribute("session.id", record.session_id)
            if record.turn_id is not None:
                span.set_attribute("turn.id", record.turn_id)
            span.end()
```

- [ ] **Step 3: Implement import-free Langfuse adapter**

In `observability/langfuse.py`, accept a client object with `trace(**kwargs)`:

```python
class LangfuseAdapter:
    """通过注入 client 输出 TraceRecord，不引入 Langfuse 依赖。"""

    def __init__(self, client: object) -> None:
        self._client = client

    def record_many(self, records: list[TraceRecord]) -> None:
        for record in records:
            self._client.trace(
                name=record.name,
                session_id=record.session_id,
                metadata={
                    **record.attributes,
                    "turn.id": record.turn_id,
                },
            )
```

- [ ] **Step 4: Implement debug projection**

In `context/debug_projection.py`, implement deterministic Markdown:

```python
def render_debug_projection(
    *,
    context_state: ContextState,
    message_runtime: MessageRuntime,
    compression_index: CompressionIndex,
    event_log: EventLog,
) -> str:
    lines = [
        "# Debug Projection",
        "",
        "## Runtime Metadata",
        "",
        "- session_id: see event records",
        "- message_id: active refs and message store ids",
        "- compression_id: compressed segment handles",
        "",
        "## Active Message Refs",
        "",
    ]
    for ref in message_runtime.active_window.refs:
        lines.append(f"- message_id={ref.message_id} temporary={ref.temporary}")
    lines.extend(["", "## Compression Index", ""])
    for segment_id, source_refs in compression_index.snapshot().items():
        lines.append(f"- compression_id={segment_id} source_refs={list(source_refs)}")
    lines.extend(["", "## Event Records", ""])
    for record in event_log.records:
        lines.append(
            f"- seq={record.sequence} event={record.event_type} "
            f"session_id={record.session_id} turn_id={record.turn_id}"
        )
    return "\\n".join(lines) + "\\n"
```

- [ ] **Step 5: Export trace and debug names**

Update `observability/__init__.py` with `TraceRecord`, `TraceSink`, `EventTraceProjector`, `OTelAdapter`, and `LangfuseAdapter`.

Update `context/__init__.py` with `render_debug_projection` only if current package export style exposes renderer helpers.

- [ ] **Step 6: Run trace and debug tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/observability/test_traces.py tests/context/test_debug_projection.py tests/context/test_renderer.py -q
```

Expected: trace and debug tests pass, and existing renderer metadata guard still passes.

---

## Task 13: Session Recovery Integration

**Files:**

- Create: `tests/runtime/test_session_recovery.py`
- Modify: `src/agentos/compression/runtime.py`
- Modify: `src/agentos/runtime/query_loop.py` only if optional persistence hook is added.
- Modify: `src/agentos/persistence/serializers.py`

- [ ] **Step 1: Write failing recovery integration test**

Add:

```python
from agentos.compression import CompressionRuntime
from agentos.context import ContextRenderer, ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.observability.events import EventLog
from agentos.persistence import MemoryPersistence, SessionSnapshot
from agentos.policies import BudgetPolicy
from agentos.providers import FakeProvider
from agentos.recall import RecallRuntime
from agentos.runtime import EventBus, ProviderRequestBuilder, QueryLoop, SessionState


def test_session_snapshot_restores_context_messages_compression_and_recall() -> None:
    event_log = EventLog()
    bus = EventBus(subscribers=[event_log])
    context = ContextRuntime(event_bus=bus, session_id="session_1")
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="str",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    context.update_state("task_goal", "Recover session.")
    messages = MessageRuntime()
    provider = FakeProvider(["first answer", "second answer"])
    compression = CompressionRuntime(
        context_runtime=context,
        message_runtime=messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
        event_bus=bus,
        session_id="session_1",
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=[],
        ),
        provider=provider,
        compression_runtime=compression,
        event_bus=bus,
        session_state=SessionState(id="session_1"),
    )
    loop.run_turn("old detail")
    loop.run_turn("current task")
    snapshot = SessionSnapshot(
        session_state=loop.session_state,
        context_state=context.snapshot(),
        message_runtime=messages,
        compression_index=compression.index,
        next_segment_number=compression.next_segment_number(),
        event_records=tuple(event_log.records),
    )
    persistence = MemoryPersistence()
    persistence.save(snapshot)

    restored = persistence.load("session_1")
    restored_context = ContextRuntime(state=restored.context_state)
    restored_messages = restored.message_runtime
    restored_compression = CompressionRuntime(
        context_runtime=restored_context,
        message_runtime=restored_messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
        index=restored.compression_index,
        next_segment_number=restored.next_segment_number,
    )
    RecallRuntime(
        compression_index=restored_compression.index,
        message_runtime=restored_messages,
    ).recall_context("seg_1")

    request = ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=restored_messages,
        tools=[],
    ).build(restored_context)

    assert request.messages[0]["content"] == "old detail"
    assert request.messages[-1]["content"] == "second answer"
    assert restored.session_state.new_turn("after restore").id == "turn_3"
    assert restored.event_records[0].event_type == "WorkingStateSchemaDeclaredEvent"
```

- [ ] **Step 2: Run recovery test and verify failure**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_session_recovery.py -q
```

Expected: failure points to missing restore constructor, event instrumentation, or compression cursor restoration.

- [ ] **Step 3: Implement compression cursor restore**

Modify `CompressionRuntime` constructor to accept:

```python
next_segment_number: int = 1
```

Set `_next_segment_number` from that value. Add:

```python
def next_segment_number(self) -> int:
    """返回下一次压缩会使用的 segment 序号。"""

    return self._next_segment_number
```

- [ ] **Step 4: Run recovery and persistence tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/runtime/test_session_recovery.py tests/persistence -q
```

Expected: recovery and persistence suites pass.

---

## Task 14: Public API And Architecture Guards

**Files:**

- Modify: `tests/architecture/test_public_api.py`
- Modify: package `__init__.py` files needed for exports.

- [ ] **Step 1: Extend public API tests**

Add assertions:

```python
def test_phase5_phase6_public_api_exports() -> None:
    capabilities = importlib.import_module("agentos.capabilities")
    persistence = importlib.import_module("agentos.persistence")
    observability = importlib.import_module("agentos.observability")

    for name in [
        "SkillDefinition",
        "SkillRegistry",
        "SkillLoadResult",
        "MCPToolInfo",
        "MCPClient",
        "MCPRegistry",
        "MCPToolAdapter",
    ]:
        assert hasattr(capabilities, name)

    for name in [
        "SessionSnapshot",
        "SessionPersistence",
        "MemoryPersistence",
        "FileSystemPersistence",
        "SQLitePersistence",
    ]:
        assert hasattr(persistence, name)

    for name in [
        "EventLog",
        "EventRecord",
        "TraceRecord",
        "EventTraceProjector",
        "OTelAdapter",
        "LangfuseAdapter",
    ]:
        assert hasattr(observability, name)
```

Add naming drift test:

```python
def test_no_public_snake_case_package_alias() -> None:
    legacy_snake_name = "agent" + "_os"

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(legacy_snake_name)
```

- [ ] **Step 2: Run architecture tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/architecture/test_public_api.py -q
```

Expected: architecture tests pass.

---

## Task 15: Full Verification And Drift Search

**Files:**

- No new files unless verification reveals a concrete failure.

- [ ] **Step 1: Run targeted Phase 5/6 suite**

Run:

```bash
uv run --python 3.11 --extra dev pytest \
  tests/capabilities/test_skills.py \
  tests/capabilities/test_mcp.py \
  tests/capabilities/test_tool_registry_phase5.py \
  tests/context/test_capability_plane_phase5.py \
  tests/runtime/test_skill_mcp_tool_loop.py \
  tests/persistence/test_serializers.py \
  tests/persistence/test_filesystem.py \
  tests/persistence/test_sqlite.py \
  tests/observability/test_event_log.py \
  tests/observability/test_traces.py \
  tests/context/test_debug_projection.py \
  tests/runtime/test_session_recovery.py \
  -q
```

Expected: all Phase 5/6 tests pass.

- [ ] **Step 2: Run full test suite**

Run:

```bash
uv run --python 3.11 --extra dev pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Compile source and tests**

Run:

```bash
uv run --python 3.11 --extra dev python -m compileall -q src tests
```

Expected: command exits with status 0 and no output.

- [ ] **Step 4: Check diff whitespace**

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 5: Search for forbidden package names**

Run:

```bash
rg -n "agent[O]s|agent[_]os" src tests docs pyproject.toml README.md
```

Expected: no matches.

- [ ] **Step 6: Search default prompt metadata drift**

Run:

```bash
rg -n "session_id|turn_id|message_id|trace_id|span_id|tool_call_id|schema_id|projection_id|compression_id|source|relevance" tests/context/goldens src/agentos/context/renderer.py
```

Expected: no matches in default renderer or golden files. Debug projection files are intentionally outside this command.

- [ ] **Step 7: Produce completion checklist**

Before claiming Phase 5/6 complete, produce a table with:

- Design requirement.
- Implementation files.
- Test file or verification command.
- Status: complete, deferred, or not applicable.

If any row is not complete, final status must be "partially complete".

## Self-Review

- Spec coverage: every Phase 5 and Phase 6 acceptance item from the design spec maps to at least one task.
- Test-first coverage: each behavior-changing task begins with failing tests and a command that must fail before implementation.
- Module boundary coverage: skills and MCP stay in `capabilities/`; persistence stays in `persistence/`; observability stays in `observability/`; default rendering stays metadata-free in `context/renderer.py`.
- Public API coverage: `agentos` exports are checked by architecture tests.
- Verification coverage: targeted tests, full suite, compileall, diff check, package-name drift search and default prompt metadata drift search are required before completion.
