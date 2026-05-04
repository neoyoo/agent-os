import pytest

from agentos.context import (
    CompressedSegment,
    ContextProtocolError,
    ContextRuntime,
    ContextState,
    WorkingStateField,
)


def field(name: str, type_: str = "str", purpose: str = "测试字段") -> WorkingStateField:
    return WorkingStateField(name=name, type=type_, purpose=purpose)


def test_declare_schema_preserves_field_order() -> None:
    runtime = ContextRuntime()

    runtime.declare_schema(
        [
            field("task_goal"),
            field("constraints", "list[str]"),
            field("next_steps", "list[str]"),
        ],
    )

    assert [item.name for item in runtime.state.working_state_schema.fields] == [
        "task_goal",
        "constraints",
        "next_steps",
    ]


def test_declare_schema_rejects_second_declaration_in_same_chapter() -> None:
    runtime = ContextRuntime()
    runtime.declare_schema([field("task_goal")])

    with pytest.raises(ContextProtocolError, match="already declared"):
        runtime.declare_schema([field("constraints", "list[str]")])


def test_declare_schema_rejects_invalid_fields() -> None:
    runtime = ContextRuntime()

    with pytest.raises(ContextProtocolError, match="at least one field"):
        runtime.declare_schema([])

    with pytest.raises(ContextProtocolError, match="duplicate field"):
        runtime.start_chapter([field("task_goal"), field("task_goal")])

    with pytest.raises(ContextProtocolError, match="name, type, and purpose"):
        runtime.start_chapter([WorkingStateField(name="", type="str", purpose="bad")])


def test_update_state_requires_declared_field() -> None:
    runtime = ContextRuntime()

    with pytest.raises(ContextProtocolError, match="declare schema"):
        runtime.update_state("task_goal", "Build context runtime.")

    runtime.declare_schema([field("task_goal")])
    runtime.update_state("task_goal", "Build context runtime.")

    assert runtime.state.working_state == {
        "task_goal": "Build context runtime.",
    }

    with pytest.raises(ContextProtocolError, match="not declared"):
        runtime.update_state("unknown", "bad")


def test_working_state_snapshot_cannot_be_mutated_directly() -> None:
    runtime = ContextRuntime()
    runtime.declare_schema(
        [
            field("task_goal"),
            field("constraints", "list[str]"),
        ],
    )
    constraints = ["only through tools"]

    runtime.update_state("task_goal", "Build context runtime.")
    runtime.update_state("constraints", constraints)
    constraints.append("external mutation")

    assert runtime.state.working_state["constraints"] == ("only through tools",)
    with pytest.raises(TypeError):
        runtime.state.working_state["task_goal"] = "mutated"  # type: ignore[index]
    with pytest.raises(AttributeError):
        runtime.state.working_state["constraints"].append("mutated")  # type: ignore[attr-defined]
    assert runtime.state.working_state["task_goal"] == "Build context runtime."


def test_m3_projection_snapshots_cannot_be_mutated_directly() -> None:
    segment = CompressedSegment(
        id="seg_1",
        topic="previous work",
        summary="Renderer baseline was completed.",
    )
    runtime = ContextRuntime(
        state=ContextState(
            compressed_history=[segment],
            inherited_state=["继续 Phase 2。"],
            memory_context=["用户偏好中文讨论架构。"],
        ),
    )

    with pytest.raises(AttributeError):
        runtime.state.compressed_history.append(  # type: ignore[attr-defined]
            CompressedSegment(id="seg_2", topic="bad", summary="mutated"),
        )
    with pytest.raises(AttributeError):
        runtime.state.inherited_state.append("mutated")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        runtime.state.memory_context.append("mutated")  # type: ignore[attr-defined]

    assert runtime.state.compressed_history == (segment,)
    assert runtime.state.inherited_state == ("继续 Phase 2。",)
    assert runtime.state.memory_context == ("用户偏好中文讨论架构。",)


def test_extend_schema_appends_fields_and_preserves_state() -> None:
    runtime = ContextRuntime()
    runtime.declare_schema([field("task_goal")])
    runtime.update_state("task_goal", "Build context runtime.")

    runtime.extend_schema(
        [
            field("constraints", "list[str]"),
            field("next_steps", "list[str]"),
        ],
    )

    assert [item.name for item in runtime.state.working_state_schema.fields] == [
        "task_goal",
        "constraints",
        "next_steps",
    ]
    assert runtime.state.working_state == {
        "task_goal": "Build context runtime.",
    }


def test_extend_schema_rejects_duplicates() -> None:
    runtime = ContextRuntime()
    runtime.declare_schema([field("task_goal")])

    with pytest.raises(ContextProtocolError, match="already exists"):
        runtime.extend_schema([field("task_goal")])

    with pytest.raises(ContextProtocolError, match="duplicate field"):
        runtime.extend_schema([field("constraints"), field("constraints")])


def test_start_chapter_resets_schema_and_working_state_but_keeps_m3() -> None:
    state = ContextState(
        compressed_history=[
            CompressedSegment(
                id="seg_1",
                topic="previous work",
                summary="Renderer baseline was completed.",
            ),
        ],
        memory_context=["用户偏好中文讨论架构。"],
    )
    runtime = ContextRuntime(state=state)
    runtime.declare_schema([field("task_goal")])
    runtime.update_state("task_goal", "Build context runtime.")

    runtime.start_chapter([field("next_goal")])

    assert [item.name for item in runtime.state.working_state_schema.fields] == [
        "next_goal",
    ]
    assert runtime.state.working_state == {}
    assert runtime.state.compressed_history[0].id == "seg_1"
    assert runtime.state.memory_context == ("用户偏好中文讨论架构。",)


def test_start_chapter_can_clear_schema_without_declaring_a_new_one() -> None:
    runtime = ContextRuntime()
    runtime.declare_schema([field("task_goal")])
    runtime.update_state("task_goal", "Build context runtime.")

    runtime.start_chapter()

    assert runtime.state.working_state_schema.fields == ()
    assert runtime.state.working_state == {}


def test_context_runtime_does_not_expose_non_default_context_tools() -> None:
    runtime = ContextRuntime()

    assert not hasattr(runtime, "read_state")
    assert not hasattr(runtime, "abort_chapter")
    assert not hasattr(runtime, "mark_important")
