from agentos.context import ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.runtime.checkpoint import RuntimeCheckpointSource
from agentos.runtime.session import SessionState


def test_checkpoint_source_excludes_temporary_refs_and_runtime_notices() -> None:
    session = SessionState("session_1")
    session.new_turn("question")
    messages = MessageRuntime()
    durable = messages.append_user("question")
    recalled = messages.store.append("user", "temporarily recalled")
    messages.active_window.append(durable.id)
    messages.active_window.append(recalled.id, temporary=True)
    context = ContextRuntime(session_id="session_1")
    context.declare_schema(
        [WorkingStateField("task_goal", "string", "Current task")],
    )
    context.update_state("task_goal", "resume safely")
    context.set_runtime_notices(("must not persist",))

    checkpoint = RuntimeCheckpointSource(
        session=session,
        messages=messages,
        context=context,
    ).capture()

    assert checkpoint.session_id == "session_1"
    assert checkpoint.active_refs == (durable.id,)
    assert [message.id for message in checkpoint.messages] == [durable.id, recalled.id]
    assert checkpoint.context.runtime_notices == ()
    assert checkpoint.context.working_state == {"task_goal": "resume safely"}
