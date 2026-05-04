from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentos.compression.index import CompressionIndex
    from agentos.context.state import ContextState
    from agentos.messages.runtime import MessageRuntime
    from agentos.observability.events import EventLog


def render_debug_projection(
    *,
    context_state: ContextState,
    message_runtime: MessageRuntime,
    compression_index: CompressionIndex,
    event_log: EventLog,
    debug: bool = False,
) -> str:
    """渲染显式 debug-only 投影，可包含 runtime metadata。"""

    if not debug:
        raise ValueError("debug projection requires debug=True")

    lines = [
        "# Debug Projection",
        "",
        "## Runtime Metadata",
        "",
        "- session_id: see event records",
        "- message_id: active refs and message store ids",
        "- compression_id: compressed segment handles",
        "",
        "## Context State",
        "",
    ]
    lines.extend(
        f"- working_state.{key}: {value}"
        for key, value in context_state.working_state.items()
    )
    lines.extend(["", "## Active Message Refs", ""])
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
    return "\n".join(lines) + "\n"
