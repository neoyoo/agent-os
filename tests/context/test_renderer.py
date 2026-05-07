import inspect
from pathlib import Path

from agentos.context_protocol import CONTEXT_PROTOCOL_TOOL_NAMES
from agentos.context.projection import (
    CapabilityPlane,
    MCPServerDeclaration,
    RuntimeContract,
    SkillDeclaration,
    ToolDeclaration,
    ToolGroup,
)
from agentos.context.renderer import ContextRenderer
from agentos.context.schema import WorkingStateField, WorkingStateSchema
from agentos.context.state import CompressedSegment, ContextState


def test_default_renderer_matches_golden_context_projection() -> None:
    state = ContextState(
        working_state_schema=WorkingStateSchema(
            fields=[
                WorkingStateField(
                    name="task_goal",
                    type="str",
                    purpose="当前任务目标和完成标准",
                ),
                WorkingStateField(
                    name="constraints",
                    type="list[str]",
                    purpose="用户、项目或安全约束",
                ),
                WorkingStateField(
                    name="next_steps",
                    type="list[str]",
                    purpose="下一步要做的具体动作",
                ),
            ],
        ),
        working_state={
            "task_goal": "实现 context renderer 的第一版。",
            "constraints": [
                "默认 prompt 不展示 runtime metadata。",
                "只实现 context 模块边界内的渲染。",
            ],
            "next_steps": ["补 working state 工具。"],
        },
        compressed_history=[
            CompressedSegment(
                id="seg_1",
                topic="visible context boundary",
                summary="默认 prompt 只暴露行动所需的上下文。",
            ),
        ],
        memory_context=[
            "用户偏好中文讨论架构，协议标识符保留英文。",
        ],
    )
    golden_path = Path(__file__).with_name("goldens") / "default_context.md"

    assert ContextRenderer().render(state) == golden_path.read_text()


def test_default_renderer_emits_context_sections_in_order() -> None:
    state = ContextState(
        working_state_schema=WorkingStateSchema(
            fields=[
                WorkingStateField(
                    name="task_goal",
                    type="str",
                    purpose="Current task goal and completion criteria",
                ),
            ],
        ),
        working_state={"task_goal": "Build the context renderer first."},
        compressed_history=[
            CompressedSegment(
                id="seg_1",
                topic="architecture direction",
                summary="The SDK is designed from the LLM-visible context outward.",
            ),
        ],
        memory_context=[
            "The user prefers Chinese architecture discussion with English protocol identifiers.",
        ],
    )

    rendered = ContextRenderer().render(state)

    expected_sections = [
        "# Runtime Contract",
        "# Capability Plane",
        "# Context Management Rules",
        "# Declared Working State Schema",
        "# Working State",
        "# Compressed History",
        "# Memory Context",
    ]
    lines = rendered.splitlines()
    positions = [lines.index(section) for section in expected_sections]
    assert positions == sorted(positions)


def test_default_renderer_omits_empty_working_state_sections() -> None:
    rendered = ContextRenderer().render(ContextState())

    assert "# Declared Working State Schema" not in rendered
    assert "\n# Working State\n" not in rendered
    assert "<declared-schema>" not in rendered
    assert "\n<working-state>\n" not in rendered
    assert "# Runtime Contract" in rendered
    assert "# Capability Plane" in rendered


def test_default_renderer_renders_empty_working_state_when_schema_declared() -> None:
    rendered = ContextRenderer().render(
        ContextState(
            working_state_schema=WorkingStateSchema(
                fields=[
                    WorkingStateField(
                        name="task_goal",
                        type="str",
                        purpose="Current task goal and completion criteria",
                    ),
                ],
            ),
        ),
    )

    assert "# Declared Working State Schema" in rendered
    assert "# Working State" in rendered
    assert "<working-state>\n</working-state>" in rendered


def test_default_renderer_renders_inherited_state_only_when_present() -> None:
    empty_rendered = ContextRenderer().render(ContextState())
    assert "\n# Inherited State\n" not in empty_rendered

    rendered = ContextRenderer().render(
        ContextState(
            inherited_state=[
                "继续 Phase 2，不改变 provider 协议。",
                "默认 prompt 仍不能展示 runtime metadata。",
            ],
            compressed_history=[
                CompressedSegment(
                    id="seg_1",
                    topic="previous chapter",
                    summary="上一 chapter 完成了 context renderer。",
                ),
            ],
        ),
    )

    expected_sections = [
        "# Inherited State",
        "# Compressed History",
    ]
    lines = rendered.splitlines()
    positions = [lines.index(section) for section in expected_sections]
    assert positions == sorted(positions)
    assert "<inherited-state>" in rendered
    assert "<item>继续 Phase 2，不改变 provider 协议。</item>" in rendered


def test_default_renderer_renders_runtime_notices_as_last_transient_section() -> None:
    rendered = ContextRenderer().render(
        ContextState(
            runtime_notices=[
                "Task task_abc completed. Call check_agent_tasks to retrieve results.",
            ],
        ),
    )

    assert "# Runtime Notice" in rendered
    assert rendered.rstrip().endswith(
        "<notice>Task task_abc completed. "
        "Call check_agent_tasks to retrieve results.</notice>\n"
        "</runtime-notices>",
    )
    assert "\n# Working State\n" not in rendered


def test_default_renderer_lists_context_protocol_tools() -> None:
    rendered = ContextRenderer().render(ContextState())

    for tool_name in [
        "declare_schema",
        "update_state",
        "extend_schema",
        "start_chapter",
        "recall_context",
    ]:
        assert f"`{tool_name}`" in rendered

    for excluded_tool_name in ["read_state", "abort_chapter", "mark_important"]:
        assert excluded_tool_name not in rendered


def test_default_renderer_explains_chapter_change_granularity() -> None:
    rendered = ContextRenderer().render(ContextState())

    assert "任务局部修正使用 `update_state`" in rendered
    assert "schema 不足使用 `extend_schema`" in rendered
    assert "任务实质变更使用 `start_chapter`" in rendered


def test_context_management_rules_do_not_hardcode_protocol_tool_names() -> None:
    source = inspect.getsource(ContextRenderer._context_management_rules)

    for tool_name in CONTEXT_PROTOCOL_TOOL_NAMES:
        assert f"`{tool_name}`" not in source


def test_renderer_allows_project_runtime_contract_customization() -> None:
    rendered = ContextRenderer(
        runtime_contract=RuntimeContract(
            identity="你是一个专注 Agent OS SDK 的工程助手。",
            extra_guardrails=[
                "所有文件修改必须保持 context 模块边界。",
                "新增行为必须先有本地测试覆盖。",
            ],
        ),
    ).render(ContextState())

    assert "你是一个专注 Agent OS SDK 的工程助手。" in rendered
    assert "- 除非用户明确要求，否则不要覆盖或回滚用户的改动。" in rendered
    assert "- 所有文件修改必须保持 context 模块边界。" in rendered
    assert "- 新增行为必须先有本地测试覆盖。" in rendered
    assert "# Context Management Rules" in rendered


def test_renderer_allows_project_capability_plane_injection() -> None:
    rendered = ContextRenderer(
        capability_plane=CapabilityPlane(
            tool_groups=[
                ToolGroup(
                    name="Project intelligence",
                    tools=[
                        ToolDeclaration(
                            name="query_project_index",
                            description="查询项目索引。",
                        ),
                    ],
                ),
            ],
            mcp_servers=[
                MCPServerDeclaration(
                    name="github",
                    description="读取和更新 issue、pull request、comment 和 release。",
                    endpoint="https://api.github.com/...",
                ),
            ],
            skills=[
                SkillDeclaration(
                    name="systematic-debugging",
                    when_to_use="遇到 bug、测试失败或异常行为时使用。",
                ),
            ],
        ),
    ).render(ContextState())

    assert "完整工具 schema 由 runtime 通过 provider `tools` 参数提供" in rendered
    assert (
        "- Context protocol: `declare_schema` — 声明当前 chapter 的 working state 字段。"
        in rendered
    )
    assert "- Project intelligence: `query_project_index` — 查询项目索引。" in rendered
    assert "- `github (https://api.github.com/...)`" in rendered
    assert "读取和更新 issue、pull request、comment 和 release。" in rendered
    assert "- `systematic-debugging` — 遇到 bug、测试失败或异常行为时使用。" in rendered


def test_default_renderer_does_not_expose_runtime_metadata() -> None:
    rendered = ContextRenderer().render(
        ContextState(
            working_state_schema=WorkingStateSchema(
                fields=[
                    WorkingStateField(
                        name="verified_facts",
                        type="list[str]",
                        purpose="Facts verified by reading or running code",
                    ),
                ],
            ),
            working_state={"verified_facts": ["Default prompts omit runtime metadata."]},
            compressed_history=[
                CompressedSegment(
                    id="seg_1",
                    topic="visible context boundary",
                    summary="The prompt only exposes actionable handles and concise context.",
                ),
            ],
        ),
    )

    forbidden_terms = [
        "session_id",
        "turn_id",
        "message_id",
        "trace_id",
        "span_id",
        "tool_call_id",
        "schema_id",
        "projection_id",
        "compression_id",
        "source",
        "relevance",
    ]
    for term in forbidden_terms:
        assert term not in rendered


def test_default_renderer_renders_declared_schema_and_working_state() -> None:
    state = ContextState(
        working_state_schema=WorkingStateSchema(
            fields=[
                WorkingStateField(
                    name="constraints",
                    type="list[str]",
                    purpose="User, project, or safety constraints",
                ),
            ],
        ),
        working_state={
            "constraints": [
                "Do not render runtime metadata into the default prompt.",
                "Keep context ownership inside the context package.",
            ],
        },
    )

    rendered = ContextRenderer().render(state)

    assert '<field name="constraints" type="list[str]"' in rendered
    assert 'purpose="User, project, or safety constraints"' in rendered
    assert "<constraints>" in rendered
    assert "<c>Do not render runtime metadata into the default prompt.</c>" in rendered
    assert "<c>Keep context ownership inside the context package.</c>" in rendered


def test_default_renderer_preserves_declared_schema_field_order() -> None:
    state = ContextState(
        working_state_schema=WorkingStateSchema(
            fields=[
                WorkingStateField(
                    name="task_goal",
                    type="str",
                    purpose="Current task goal and completion criteria",
                ),
                WorkingStateField(
                    name="constraints",
                    type="list[str]",
                    purpose="User, project, or safety constraints",
                ),
                WorkingStateField(
                    name="next_steps",
                    type="list[str]",
                    purpose="Concrete next actions",
                ),
            ],
        ),
    )

    rendered = ContextRenderer().render(state)

    task_goal_position = rendered.index('name="task_goal"')
    constraints_position = rendered.index('name="constraints"')
    next_steps_position = rendered.index('name="next_steps"')
    assert task_goal_position < constraints_position < next_steps_position


def test_default_renderer_orders_inherited_state_between_working_state_and_compressed_history() -> None:
    state = ContextState(
        working_state_schema=WorkingStateSchema(
            fields=[
                WorkingStateField(
                    name="task_goal",
                    type="str",
                    purpose="Current task goal and completion criteria",
                ),
            ],
        ),
        inherited_state=["Continue the current architecture direction."],
        compressed_history=[
            CompressedSegment(
                id="seg_1",
                topic="previous chapter",
                summary="Previous chapter finished renderer.",
            ),
        ],
    )

    rendered = ContextRenderer().render(state)

    expected_sections = [
        "# Runtime Contract",
        "# Capability Plane",
        "# Context Management Rules",
        "# Declared Working State Schema",
        "# Working State",
        "# Inherited State",
        "# Compressed History",
        "# Memory Context",
    ]
    lines = rendered.splitlines()
    positions = [lines.index(section) for section in expected_sections]
    assert positions == sorted(positions)
