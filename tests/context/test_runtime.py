from collections.abc import Iterator, Mapping

import pytest

from agentos.context import (
    CompressedSegment,
    ContextProtocolError,
    ContextRuntime,
    ContextState,
    WorkingStateField,
    WorkingStateSchema,
)


def field(
    name: str,
    type_: str = "string",
    purpose: str = "测试字段",
) -> WorkingStateField:
    return WorkingStateField(name=name, type=type_, purpose=purpose)


class ChangingList(list[object]):
    """每次迭代返回不同版本，用于验证输入只被冻结一次。"""

    def __init__(self, *versions: list[object]) -> None:
        super().__init__()
        self._versions = versions
        self.iterations = 0

    def __iter__(self) -> Iterator[object]:
        index = min(self.iterations, len(self._versions) - 1)
        self.iterations += 1
        return iter(self._versions[index])


class ChangingMapping(Mapping[str, object]):
    """每次 items 调用返回不同版本，用于验证输入只被冻结一次。"""

    def __init__(self, *versions: dict[str, object]) -> None:
        self._versions = versions
        self.items_calls = 0

    def __getitem__(self, key: str) -> object:
        return self._versions[0][key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._versions[0])

    def __len__(self) -> int:
        return len(self._versions[0])

    def items(self):  # type: ignore[no-untyped-def]
        index = min(self.items_calls, len(self._versions) - 1)
        self.items_calls += 1
        return self._versions[index].items()


class RecordingEventBus:
    """记录 Runtime 发出的事件。"""

    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> None:
        self.events.append(event)


def test_declare_schema_preserves_field_order() -> None:
    runtime = ContextRuntime()

    runtime.declare_schema(
        [
            field("task_goal"),
            field("constraints", "list[string]"),
            field("next_steps", "list[string]"),
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
        runtime.declare_schema([field("constraints", "list[string]")])


def test_declare_schema_rejects_invalid_fields() -> None:
    runtime = ContextRuntime()

    with pytest.raises(ContextProtocolError, match="at least one field"):
        runtime.declare_schema([])

    with pytest.raises(ContextProtocolError, match="duplicate field"):
        runtime.start_chapter([field("task_goal"), field("task_goal")])

    with pytest.raises(ContextProtocolError, match="field name"):
        runtime.start_chapter(
            [WorkingStateField(name="", type="string", purpose="bad")],
        )


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
            field("constraints", "list[string]"),
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


def test_update_state_preserves_json_object_values() -> None:
    runtime = ContextRuntime()
    runtime.declare_schema([field("candidate", "object")])
    candidate = {
        "material": "C45",
        "geometry": {"diameter": 12.5, "holes": 4},
        "features": ["threaded", "coated"],
        "approved": True,
        "notes": None,
    }

    runtime.update_state("candidate", candidate)
    candidate["material"] = "mutated"
    candidate["geometry"]["diameter"] = 99.0
    candidate["features"].append("external mutation")

    snapshot = runtime.state.working_state
    assert snapshot["candidate"] == {
        "material": "C45",
        "geometry": {"diameter": 12.5, "holes": 4},
        "features": ["threaded", "coated"],
        "approved": True,
        "notes": None,
    }
    with pytest.raises(TypeError):
        snapshot["candidate"]["geometry"]["diameter"] = 99.0  # type: ignore[index]


def test_update_state_freezes_changing_list_once_before_validation() -> None:
    bus = RecordingEventBus()
    runtime = ContextRuntime(event_bus=bus)  # type: ignore[arg-type]
    runtime.declare_schema([field("counts", "list[integer]")])
    events_before_update = tuple(bus.events)
    value = ChangingList([1], ["bad"])

    runtime.update_state("counts", value)

    assert value.iterations == 1
    assert runtime.state.working_state["counts"] == (1,)
    assert len(bus.events) == len(events_before_update) + 1


def test_update_state_freezes_changing_mapping_once_without_type_error() -> None:
    bus = RecordingEventBus()
    runtime = ContextRuntime(event_bus=bus)  # type: ignore[arg-type]
    runtime.declare_schema([field("metadata", "object")])
    events_before_update = tuple(bus.events)
    value = ChangingMapping({"count": 1}, {"count": object()})

    runtime.update_state("metadata", value)

    assert value.items_calls == 1
    assert runtime.state.working_state["metadata"] == {"count": 1}
    assert len(bus.events) == len(events_before_update) + 1


@pytest.mark.parametrize("container_kind", ["list", "dict"])
def test_update_state_rejects_cycles_without_mutation_or_event(
    container_kind: str,
) -> None:
    bus = RecordingEventBus()
    runtime = ContextRuntime(event_bus=bus)  # type: ignore[arg-type]
    if container_kind == "list":
        runtime.declare_schema([field("value", "list[object]")])
        value: object = []
        value.append(value)  # type: ignore[union-attr]
    else:
        runtime.declare_schema([field("value", "object")])
        value = {}
        value["self"] = value  # type: ignore[index]
    events_before_update = tuple(bus.events)

    with pytest.raises(ContextProtocolError, match="cycles"):
        runtime.update_state("value", value)  # type: ignore[arg-type]

    assert runtime.state.working_state == {}
    assert tuple(bus.events) == events_before_update


def test_state_setter_rejects_non_string_field_name_without_mutation() -> None:
    state = ContextState()

    with pytest.raises(ContextProtocolError, match="field name must be a string"):
        state.set_working_state_value(1, "bad")  # type: ignore[arg-type]

    assert state.working_state == {}


def test_working_state_schema_cannot_be_replaced_directly() -> None:
    runtime = ContextRuntime()
    runtime.declare_schema([field("task_goal")])

    with pytest.raises(AttributeError):
        runtime.state.working_state_schema = WorkingStateSchema(  # type: ignore[misc]
            fields=[field("constraints")],
        )

    assert [item.name for item in runtime.state.working_state_schema.fields] == [
        "task_goal",
    ]


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


def test_runtime_notices_are_transient_projection_state() -> None:
    runtime = ContextRuntime()

    runtime.set_runtime_notices(("Task task_1 completed.",))
    snapshot = runtime.snapshot()
    runtime.clear_runtime_notices()

    assert snapshot.runtime_notices == ("Task task_1 completed.",)
    assert runtime.snapshot().runtime_notices == ()


def test_extend_schema_appends_fields_and_preserves_state() -> None:
    runtime = ContextRuntime()
    runtime.declare_schema([field("task_goal")])
    runtime.update_state("task_goal", "Build context runtime.")

    runtime.extend_schema(
        [
            field("constraints", "list[string]"),
            field("next_steps", "list[string]"),
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
