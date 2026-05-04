# Task 3 Context Runtime Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the minimal `ContextRuntime` that applies context protocol tools to `ContextState`.

**Architecture:** `context/runtime.py` owns context tool effects and mutates only `ContextState`. It depends on `context/schema.py` and `context/state.py`, and does not import messages, providers, compression, capabilities, persistence, or observability.

**Tech Stack:** Python 3.11, dataclasses, pytest, uv.

---

## Baseline

Task 1 and Task 2 were completed before this plan was written. They are documented in `docs/superpowers/specs/2026-05-03-phase-1-context-mainline-design.md`.

## File Structure

- Create: `src/agentos/context/runtime.py`
  - Defines `ContextProtocolError`.
  - Defines `ContextRuntime`.
  - Implements `declare_schema`, `update_state`, `extend_schema`, and `start_chapter`.
- Modify: `src/agentos/context/__init__.py`
  - Exports `ContextProtocolError` and `ContextRuntime`.
- Create: `tests/context/test_runtime.py`
  - Covers Task 3 behavior with focused unit tests.

## Task 1: Add Failing Runtime Tests

**Files:**
- Create: `tests/context/test_runtime.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/context/test_runtime.py` with:

```python
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
    assert runtime.state.memory_context == ["用户偏好中文讨论架构。"]


def test_start_chapter_can_clear_schema_without_declaring_a_new_one() -> None:
    runtime = ContextRuntime()
    runtime.declare_schema([field("task_goal")])
    runtime.update_state("task_goal", "Build context runtime.")

    runtime.start_chapter()

    assert runtime.state.working_state_schema.fields == []
    assert runtime.state.working_state == {}


def test_context_runtime_does_not_expose_non_default_context_tools() -> None:
    runtime = ContextRuntime()

    assert not hasattr(runtime, "read_state")
    assert not hasattr(runtime, "abort_chapter")
    assert not hasattr(runtime, "mark_important")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/context/test_runtime.py -q
```

Expected: fail during collection with `ImportError` because `ContextRuntime` and `ContextProtocolError` do not exist yet.

## Task 2: Implement Context Runtime

**Files:**
- Create: `src/agentos/context/runtime.py`
- Modify: `src/agentos/context/__init__.py`

- [ ] **Step 1: Create runtime implementation**

Create `src/agentos/context/runtime.py` with:

```python
from dataclasses import dataclass, field

from agentos.context.schema import WorkingStateField, WorkingStateSchema
from agentos.context.state import ContextState, WorkingStateValue


class ContextProtocolError(ValueError):
    """上下文协议工具调用不合法。"""


@dataclass(slots=True)
class ContextRuntime:
    """执行 context protocol tools，并维护 ContextState。"""

    state: ContextState = field(default_factory=ContextState)

    def declare_schema(self, fields: list[WorkingStateField]) -> None:
        """声明当前 chapter 的 working state schema。"""

        if self.state.working_state_schema.fields:
            raise ContextProtocolError(
                "working state schema already declared for this chapter",
            )
        self.state.working_state_schema = WorkingStateSchema(
            fields=self._validate_fields(fields),
        )

    def update_state(self, field_name: str, value: WorkingStateValue) -> None:
        """更新一个已声明的 working state 字段。"""

        declared_names = self._declared_field_names()
        if not declared_names:
            raise ContextProtocolError("declare schema before updating working state")
        if field_name not in declared_names:
            raise ContextProtocolError(f"working state field not declared: {field_name}")
        self.state.working_state[field_name] = value

    def extend_schema(self, fields: list[WorkingStateField]) -> None:
        """向当前 chapter 的 schema 追加字段。"""

        existing_fields = self.state.working_state_schema.fields
        if not existing_fields:
            raise ContextProtocolError("declare schema before extending it")

        existing_names = {item.name for item in existing_fields}
        new_fields = self._validate_fields(fields)
        for item in new_fields:
            if item.name in existing_names:
                raise ContextProtocolError(f"working state field already exists: {item.name}")

        self.state.working_state_schema = WorkingStateSchema(
            fields=[*existing_fields, *new_fields],
        )

    def start_chapter(self, fields: list[WorkingStateField] | None = None) -> None:
        """开启新 chapter，并重置 M2 working state。"""

        next_fields = [] if fields is None else self._validate_fields(fields)
        self.state.working_state_schema = WorkingStateSchema(fields=next_fields)
        self.state.working_state.clear()

    def _declared_field_names(self) -> set[str]:
        """返回当前 schema 中已声明的字段名。"""

        return {item.name for item in self.state.working_state_schema.fields}

    def _validate_fields(
        self,
        fields: list[WorkingStateField],
    ) -> list[WorkingStateField]:
        """校验 schema 字段并保留输入顺序。"""

        if not fields:
            raise ContextProtocolError("schema declaration requires at least one field")

        seen: set[str] = set()
        validated: list[WorkingStateField] = []
        for item in fields:
            if not item.name or not item.type or not item.purpose:
                raise ContextProtocolError(
                    "working state field requires name, type, and purpose",
                )
            if item.name in seen:
                raise ContextProtocolError(f"duplicate field in schema declaration: {item.name}")
            seen.add(item.name)
            validated.append(item)
        return validated
```

- [ ] **Step 2: Export runtime API**

Modify `src/agentos/context/__init__.py` so it imports and exports:

```python
from agentos.context.runtime import ContextProtocolError, ContextRuntime
```

and includes both names in `__all__`.

- [ ] **Step 3: Run runtime tests**

Run:

```bash
uv run --python 3.11 --extra dev pytest tests/context/test_runtime.py -q
```

Expected: all runtime tests pass.

## Task 3: Run Full Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run full test suite**

Run:

```bash
uv run --python 3.11 --extra dev pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run compile check**

Run:

```bash
uv run --python 3.11 --extra dev python -m compileall -q src tests
```

Expected: exit code `0`.

- [ ] **Step 3: Clean generated caches**

Run:

```bash
rm -rf src/agentos/__pycache__ src/agentos/context/__pycache__ tests/context/__pycache__ .pytest_cache
```

Expected: no generated cache files remain in `git status`.

- [ ] **Step 4: Stage changes**

Run:

```bash
git add src/agentos/context/runtime.py src/agentos/context/__init__.py tests/context/test_runtime.py
git status --short
```

Expected: new runtime files and test files are staged.

## Self Review

- Spec coverage: every Task 3 requirement in `docs/superpowers/specs/2026-05-03-phase-1-context-mainline-design.md` maps to a test or implementation step.
- Placeholder scan: this plan contains no placeholder requirements.
- Type consistency: `ContextRuntime`, `ContextProtocolError`, `WorkingStateField`, `WorkingStateSchema`, `ContextState`, and `WorkingStateValue` names match the existing source tree.
