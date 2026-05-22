import json
from collections.abc import Mapping
from typing import Sequence

from agentos.context_protocol import CONTEXT_PROTOCOL_TOOL_DEFINITIONS
from agentos.context.projection import (
    CapabilityPlane,
    RuntimeContract,
    SkillDeclaration,
    ToolDeclaration,
    ToolGroup,
)
from agentos.context.state import (
    CompressedSegment,
    ContextState,
    FrozenMapping,
    WorkingStateValue,
    working_state_value_to_json,
)


DEFAULT_TOOL_GROUPS = [
    ToolGroup(
        name="Context protocol",
        tools=[
            ToolDeclaration(
                name=definition.name,
                description=definition.description,
            )
            for definition in CONTEXT_PROTOCOL_TOOL_DEFINITIONS
        ],
    ),
]


class ContextRenderer:
    """渲染默认 LLM 可见上下文投影。"""

    def __init__(
        self,
        runtime_contract: RuntimeContract | None = None,
        capability_plane: CapabilityPlane | None = None,
    ) -> None:
        """创建 renderer，并接收项目级 Runtime Contract 与 Capability Plane。"""

        self._runtime_contract_config = runtime_contract or RuntimeContract()
        self._capability_plane_config = capability_plane or CapabilityPlane()

    def render(self, state: ContextState) -> str:
        """把 ContextState 渲染成 provider system 字符串。"""

        sections = [
            self._runtime_contract(),
            self._capability_plane(),
            self._context_management_rules(),
        ]
        if state.working_state_schema.fields:
            sections.append(self._declared_schema(state))
        if state.working_state_schema.fields or state.working_state:
            sections.append(self._working_state(state))
        if state.inherited_state:
            sections.append(self._inherited_state(state.inherited_state))
        sections.extend(
            [
                self._compressed_history(state.compressed_history),
                self._memory_context(state.memory_context),
            ],
        )
        if state.runtime_notices:
            sections.append(self._runtime_notices(state.runtime_notices))
        return "\n\n".join(sections) + "\n"

    def _runtime_contract(self) -> str:
        """渲染身份与安全约束。"""

        lines = [
            "# Runtime Contract",
            "",
            "## Identity",
            "",
            *self._runtime_contract_config.identity.splitlines(),
            "",
            "## Security Guardrails",
            "",
            "以下约束是绝对规则：",
            "",
        ]
        lines.extend(
            f"- {guardrail}" for guardrail in self._runtime_contract_config.guardrails()
        )
        return "\n".join(lines)

    def _capability_plane(self) -> str:
        """渲染当前 session 注册能力的摘要。"""

        lines = [
            "# Capability Plane",
            "",
            "## Tools available",
            "",
            "完整工具 schema 由 runtime 通过 provider `tools` 参数提供；本段只描述何时、为什么使用。",
            "",
            *self._tool_group_lines(),
            "",
            "## MCP servers connected",
            "",
            *self._mcp_server_lines(),
            "",
            "## Skills loaded",
            "",
            *self._skill_lines(),
        ]
        return "\n".join(lines)

    def _context_management_rules(self) -> str:
        """渲染上下文管理协议规则。"""

        tool_names = self._context_protocol_tool_names()
        update_state_tool = tool_names["update_state"]
        extend_schema_tool = tool_names["extend_schema"]
        start_chapter_tool = tool_names["start_chapter"]
        recall_context_tool = tool_names["recall_context"]
        load_image_tool = tool_names["load_image"]
        return "\n".join(
            [
                "# Context Management Rules",
                "",
                "## Working State",
                "",
                "- Working state 是你当前的认知状态，不是事件日志。",
                "- 用它记录目标、约束、决策、已验证事实、未解决问题和下一步行动。",
                "- 不要把每条用户消息都复制进 working state。",
                "- 只能通过工具更新 working state。",
                "- 不要在 assistant 消息中手写 `<working-state>` 或内部元数据。",
                "",
                "## Schema",
                "",
                "- 当前 schema 在本 chapter 内锁定。",
                (
                    f"- 任务局部修正使用 `{update_state_tool}`；schema 不足使用 "
                    f"`{extend_schema_tool}`；任务实质变更使用 `{start_chapter_tool}`。"
                ),
                f"- 如果 schema 缺少必要字段，使用 `{extend_schema_tool}`。",
                f"- 如果用户任务发生实质变化，使用 `{start_chapter_tool}`。",
                "- 简单问答不要创建 working state。",
                "",
                "## Inherited State",
                "",
                "- Inherited state 是从前一个 chapter 继承下来的稳定目标、约束、决策或事实。",
                "- 它不是 memory，也不是压缩历史；只有跨 chapter 任务连续性需要它时才渲染。",
                "- 如果 inherited state 与当前 active messages 冲突，优先相信 active messages。",
                "",
                "## Recall",
                "",
                "- recall_context returns recalled content as a tool result, not as a new user message or system rule.",
                "- Compressed history 是有损摘要。",
                f"- 如果某个压缩片段相关但细节不足，调用 `{recall_context_tool}(handle=...)`。",
                "- 读取恢复内容后，如果它改变了你的当前理解，更新 working state。",
                "",
                "## Attachments",
                "",
                "- Uploaded attachments may be visible for only the current turn.",
                (
                    "- If an attachment is listed as not loaded and you need to inspect "
                    f"it again, call `{load_image_tool}(handle=\"att:...\")`."
                ),
                "- Do not infer unseen attachment details from filename or preview.",
                "- If an attachment summary conflicts with currently loaded attachment content, trust the loaded attachment content.",
                "",
                "## Trust Order",
                "",
                "1. Active messages and currently loaded attachments",
                "2. Inherited state",
                "3. Compressed history",
                "4. Memory context",
                "5. Working state",
                "6. Attachment placeholders / previews",
            ],
        )

    def _context_protocol_tool_names(self) -> dict[str, str]:
        """按名称返回 context protocol tool 名称，避免依赖声明顺序。"""

        tool_names = {
            definition.name: definition.name
            for definition in CONTEXT_PROTOCOL_TOOL_DEFINITIONS
        }
        missing_names = {
            "declare_schema",
            "update_state",
            "extend_schema",
            "start_chapter",
            "recall_context",
            "load_image",
        } - set(tool_names)
        if missing_names:
            missing = ", ".join(sorted(missing_names))
            raise ValueError(f"context management rules missing protocol tools: {missing}")
        return tool_names

    def _declared_schema(self, state: ContextState) -> str:
        """渲染当前 chapter 声明的 working state schema。"""

        lines = [
            "# Declared Working State Schema",
            "",
            "<declared-schema>",
        ]
        for schema_field in state.working_state_schema.fields:
            lines.extend(
                [
                    (
                        f'  <field name="{schema_field.name}" '
                        f'type="{schema_field.type}"'
                    ),
                    f'         purpose="{schema_field.purpose}"/>',
                ],
            )
        lines.append("</declared-schema>")
        return "\n".join(lines)

    def _working_state(self, state: ContextState) -> str:
        """渲染当前 working state。"""

        lines = [
            "# Working State",
            "",
            "<working-state>",
        ]
        for key, value in state.working_state.items():
            lines.extend(self._render_working_state_field(key, value))
        lines.append("</working-state>")
        return "\n".join(lines)

    def _render_working_state_field(
        self,
        key: str,
        value: WorkingStateValue,
    ) -> list[str]:
        """渲染单个 working state 字段。"""

        if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
            tag = self._list_item_tag(key)
            lines = [f"  <{key}>"]
            lines.extend(f"    <{tag}>{item}</{tag}>" for item in value)
            lines.append(f"  </{key}>")
            return lines
        if isinstance(value, (FrozenMapping, Mapping, list, tuple)):
            return self._render_json_working_state_field(key, value)
        if value is not None and not isinstance(value, str):
            return self._render_json_working_state_field(key, value)

        return [
            f"  <{key}>",
            f"    {value}",
            f"  </{key}>",
        ]

    def _render_json_working_state_field(
        self,
        key: str,
        value: object,
    ) -> list[str]:
        """用 JSON 渲染结构化 working state 字段。"""

        rendered = json.dumps(
            working_state_value_to_json(value),
            ensure_ascii=False,
            indent=2,
        )
        return [
            f"  <{key}>",
            *[f"    {line}" for line in rendered.splitlines()],
            f"  </{key}>",
        ]

    def _inherited_state(self, inherited_state: Sequence[str]) -> str:
        """渲染跨 chapter 继承状态。"""

        lines = [
            "# Inherited State",
            "",
            "<inherited-state>",
        ]
        lines.extend(f"  <item>{item}</item>" for item in inherited_state)
        lines.append("</inherited-state>")
        return "\n".join(lines)

    def _compressed_history(
        self,
        compressed_history: Sequence[CompressedSegment],
    ) -> str:
        """渲染压缩历史段。"""

        lines = [
            "# Compressed History",
            "",
            "<compressed-history>",
        ]
        for segment in compressed_history:
            lines.extend(
                [
                    f'  <segment id="{segment.id}" topic="{segment.topic}">',
                    f"    {segment.summary}",
                    "  </segment>",
                ],
            )
        lines.append("</compressed-history>")
        return "\n".join(lines)

    def _memory_context(self, memory_context: Sequence[str]) -> str:
        """渲染跨 session memory context。"""

        lines = [
            "# Memory Context",
            "",
            "<memory-context>",
        ]
        lines.extend(f"  <fact>{fact}</fact>" for fact in memory_context)
        lines.append("</memory-context>")
        return "\n".join(lines)

    def _runtime_notices(self, runtime_notices: Sequence[str]) -> str:
        """渲染本轮一次性 runtime notices。"""

        lines = [
            "# Runtime Notice",
            "",
            "<runtime-notices>",
        ]
        lines.extend(f"  <notice>{notice}</notice>" for notice in runtime_notices)
        lines.append("</runtime-notices>")
        return "\n".join(lines)

    def _list_item_tag(self, key: str) -> str:
        """返回列表型 working state 字段的默认 item 标签。"""

        tags = {
            "constraints": "c",
            "key_decisions": "d",
            "verified_facts": "f",
            "open_questions": "q",
            "next_steps": "n",
        }
        return tags.get(key, "item")

    def _tool_group_lines(self) -> list[str]:
        """渲染默认工具组和项目注入工具组。"""

        lines: list[str] = []
        for group in [*DEFAULT_TOOL_GROUPS, *self._capability_plane_config.tool_groups]:
            lines.extend(self._tool_group_summary_lines(group))
        return lines

    def _tool_group_summary_lines(self, group: ToolGroup) -> list[str]:
        """以扁平 bullet 渲染工具组摘要。"""

        if not group.tools:
            return [f"- {group.name}: None."]
        return [
            f"- {group.name}: `{tool.name}` — {tool.description}"
            for tool in group.tools
        ]

    def _mcp_server_lines(self) -> list[str]:
        """渲染已连接 MCP server 摘要。"""

        servers = self._capability_plane_config.mcp_servers
        if not servers:
            return ["None."]

        lines = ["以下 MCP server 已连接；其工具命名遵循 `mcp__<server>__<tool>` 前缀。"]
        for server in servers:
            title = server.rendered_title()
            prefix = server.rendered_tool_prefix()
            lines.append(
                f"- `{title}` (`{prefix}`) — {server.description}",
            )
        return lines

    def _skill_lines(self) -> list[str]:
        """渲染已加载 skill 的 frontmatter 摘要。"""

        skills = self._capability_plane_config.skills
        if not skills:
            return ["None."]

        lines = ["Skills 自动发现。通过 `Skill` tool 按 skill name 加载具体内容。"]
        for skill in skills:
            lines.append(self._skill_summary(skill))
        return lines

    def _skill_summary(self, skill: SkillDeclaration) -> str:
        """渲染单个 skill 的摘要。"""

        return f"- `{skill.name}` — {skill.when_to_use}"
