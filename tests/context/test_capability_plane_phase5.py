from agentos.context import ContextRenderer, ContextState
from agentos.context.projection import (
    CapabilityPlane,
    MCPServerDeclaration,
    SkillDeclaration,
    ToolDeclaration,
    ToolGroup,
)


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


def test_capability_plane_uses_flat_bullets_instead_of_nested_headings() -> None:
    rendered = ContextRenderer(
        capability_plane=CapabilityPlane(
            tool_groups=[
                ToolGroup(
                    name="File operations",
                    tools=[
                        ToolDeclaration(
                            name="read_file",
                            description="读取文件内容。",
                        ),
                    ],
                ),
            ],
            mcp_servers=[
                MCPServerDeclaration(
                    name="github",
                    description="管理 GitHub issue。",
                    tool_prefix="mcp__github__<tool>",
                ),
            ],
            skills=[
                SkillDeclaration(
                    name="schema-template",
                    when_to_use="需要声明 working state schema 时使用。",
                ),
            ],
        ),
    ).render(ContextState())

    assert "### Context protocol" not in rendered
    assert "### File operations" not in rendered
    assert "### github" not in rendered
    assert "### schema-template" not in rendered
    assert "- Context protocol: `declare_schema`" in rendered
    assert "- File operations: `read_file` — 读取文件内容。" in rendered
    assert "- `github` (`mcp__github__<tool>`) — 管理 GitHub issue。" in rendered
    assert (
        "- `schema-template` — 需要声明 working state schema 时使用。"
        in rendered
    )


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
