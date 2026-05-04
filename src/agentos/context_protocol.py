from copy import deepcopy
from dataclasses import dataclass

from agentos.providers import ProviderToolSpec


@dataclass(frozen=True, slots=True)
class ContextProtocolToolDefinition:
    """context protocol tool 的单一来源声明。"""

    name: str
    description: str


CONTEXT_PROTOCOL_TOOL_DEFINITIONS = [
    ContextProtocolToolDefinition(
        name="declare_schema",
        description="声明当前 chapter 的 working state 字段。",
    ),
    ContextProtocolToolDefinition(
        name="update_state",
        description="更新一个 working state 字段。",
    ),
    ContextProtocolToolDefinition(
        name="extend_schema",
        description="当当前 schema 不足时添加字段。",
    ),
    ContextProtocolToolDefinition(
        name="start_chapter",
        description="当任务发生实质变化时开启新 chapter。",
    ),
    ContextProtocolToolDefinition(
        name="recall_context",
        description="当压缩摘要不够时恢复对应的压缩片段。",
    ),
]
"""默认 context protocol tool 的 LLM 可见声明。"""

CONTEXT_PROTOCOL_TOOL_NAMES = frozenset(
    definition.name for definition in CONTEXT_PROTOCOL_TOOL_DEFINITIONS
)
"""默认 context protocol tool 名称集合，供渲染、provider schema 和路由复用。"""

_WORKING_STATE_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "working state 字段名。",
        },
        "type": {
            "type": "string",
            "description": "字段类型，例如 str 或 list[str]。",
        },
        "purpose": {
            "type": "string",
            "description": "字段用途和更新标准。",
        },
    },
    "required": ["name", "type", "purpose"],
}

_CONTEXT_PROTOCOL_TOOL_SPECS: list[ProviderToolSpec] = [
    {
        "type": "function",
        "function": {
            "name": "declare_schema",
            "description": "声明当前 chapter 的 working state 字段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "array",
                        "items": _WORKING_STATE_FIELD_SCHEMA,
                    },
                },
                "required": ["fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_state",
            "description": "更新一个已声明的 working state 字段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "field_name": {
                        "type": "string",
                        "description": "要更新的 working state 字段名。",
                    },
                    "value": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "字段的新值。",
                    },
                },
                "required": ["field_name", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extend_schema",
            "description": "当当前 schema 不足时添加字段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "array",
                        "items": _WORKING_STATE_FIELD_SCHEMA,
                    },
                },
                "required": ["fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_chapter",
            "description": "当任务发生实质变化时开启新 chapter。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "array",
                        "items": _WORKING_STATE_FIELD_SCHEMA,
                        "description": "新 chapter 可选的 working state schema。",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_context",
            "description": "当压缩摘要不够时恢复对应的压缩片段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "handle": {
                        "type": "string",
                        "description": "压缩历史片段 handle，例如 seg_1。",
                    },
                },
                "required": ["handle"],
            },
        },
    },
]


def context_protocol_tool_specs() -> list[ProviderToolSpec]:
    """返回 context protocol tools 的 provider schema 副本。"""

    return deepcopy(_CONTEXT_PROTOCOL_TOOL_SPECS)
