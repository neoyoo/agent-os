from datetime import UTC, datetime
from pathlib import Path

from agentos.context import ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.runtime.checkpoint import RuntimeCheckpointSource
from agentos.runtime.session import SessionState


NOW = datetime(2026, 7, 17, 12, tzinfo=UTC)


def checkpoint_source(
    session_id: str = "session_1",
) -> RuntimeCheckpointSource:
    session = SessionState(session_id)
    session.new_turn("question")
    messages = MessageRuntime()
    messages.append_user("question")
    context = ContextRuntime(session_id=session_id)
    context.declare_schema(
        [WorkingStateField("task_goal", "string", "Current task")],
    )
    context.update_state("task_goal", "resume after restart")
    return RuntimeCheckpointSource(session, messages, context)


def database_path(root: Path) -> Path:
    return root / "agentos.db"
