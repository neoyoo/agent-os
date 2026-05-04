from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry


def test_provider_tool_specs_default_to_external_tools_only() -> None:
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

    assert names == ["external_tool"]


def test_provider_tool_specs_can_explicitly_include_skill_tools() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="load_skill",
            description="Load a skill.",
            parameters={"type": "object"},
            handler=lambda arguments: "ok",
            kind="skill",
        ),
    )

    names = [
        spec["function"]["name"]
        for spec in registry.provider_tool_specs(kinds={"skill"})
    ]

    assert names == ["load_skill"]


def test_tool_call_router_explicitly_composes_external_and_skill_specs() -> None:
    registry = ToolRegistry()
    for name, kind in [
        ("external_tool", "external"),
        ("load_skill", "skill"),
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

    names = [
        spec["function"]["name"]
        for spec in ToolCallRouter(tool_registry=registry).tool_specs()
    ]

    assert "declare_schema" in names
    assert "external_tool" in names
    assert "load_skill" in names
    assert "internal_context_tool" not in names


def test_provider_tool_specs_can_explicitly_include_all_provider_kinds() -> None:
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

    names = [
        spec["function"]["name"]
        for spec in registry.provider_tool_specs(kinds={"external", "skill", "mcp"})
    ]

    assert names == [
        "external_tool",
        "load_skill",
        "mcp__github__create_issue",
    ]
