from __future__ import annotations

from typing import cast

from agentos._json_values import thaw_json
from agentos.capabilities.invocation import ToolInvocation
from agentos.context.runtime import ContextRuntime
from agentos.context.schema import WorkingStateField


CONTEXT_MUTATION_TOOL_NAMES = frozenset(
    {"declare_schema", "update_state", "extend_schema", "start_chapter"},
)


def apply_context_mutation(
    runtime: ContextRuntime,
    invocation: ToolInvocation,
) -> None:
    """Apply one closed Context Protocol mutation from a typed invocation."""

    if type(runtime) is not ContextRuntime:
        raise TypeError("context mutation requires ContextRuntime")
    if type(invocation) is not ToolInvocation:
        raise TypeError("context mutation requires ToolInvocation")
    arguments = cast(dict[str, object], thaw_json(invocation.arguments))
    if invocation.tool_name == "declare_schema":
        runtime.declare_schema(_working_state_fields(arguments))
    elif invocation.tool_name == "update_state":
        runtime.update_state(
            field_name=str(arguments["field_name"]),
            value=arguments["value"],  # type: ignore[arg-type]
        )
    elif invocation.tool_name == "extend_schema":
        runtime.extend_schema(_working_state_fields(arguments))
    elif invocation.tool_name == "start_chapter":
        fields = arguments.get("fields")
        runtime.start_chapter(
            None if fields is None else _working_state_fields(arguments),
        )
    else:
        raise ValueError("invocation is not a context mutation")


def _working_state_fields(arguments: dict[str, object]) -> list[WorkingStateField]:
    raw_fields = arguments.get("fields")
    if not isinstance(raw_fields, list):
        raise ValueError("context schema tools require a fields list")
    fields: list[WorkingStateField] = []
    for raw_field in raw_fields:
        if not isinstance(raw_field, dict):
            raise ValueError("working state field must be an object")
        fields.append(
            WorkingStateField(
                name=str(raw_field["name"]),
                type=str(raw_field["type"]),
                purpose=str(raw_field["purpose"]),
            ),
        )
    return fields


__all__ = ["CONTEXT_MUTATION_TOOL_NAMES", "apply_context_mutation"]
