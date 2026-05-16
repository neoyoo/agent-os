import pytest

from agentos.hooks import HookContext, HookManager, HookRegistry, HookResult


def test_hook_registry_returns_only_matching_hook_points() -> None:
    registry = HookRegistry()
    registry.register("before_tool_call", lambda context: None)
    registry.register("after_tool_call", lambda context: None)

    assert len(registry.hooks_for("before_tool_call")) == 1
    assert len(registry.hooks_for("after_tool_call")) == 1


def test_hook_manager_dispatches_matching_hooks_only() -> None:
    calls: list[str] = []
    registry = HookRegistry()
    registry.register(
        "before_tool_call",
        lambda context: calls.append(str(context.payload["tool_name"])),
    )
    registry.register("after_tool_call", lambda context: calls.append("wrong"))

    result = HookManager(registry).dispatch(
        "before_tool_call",
        {"tool_name": "read_file"},
    )

    assert calls == ["read_file"]
    assert result == HookResult(action="allow", payload={"tool_name": "read_file"})


def test_hook_manager_default_failure_policy_records_and_continues() -> None:
    registry = HookRegistry()
    registry.register(
        "before_provider_call",
        lambda context: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    runtime = HookManager(registry)

    result = runtime.dispatch("before_provider_call", {"model": "test"})

    assert result == HookResult(action="allow", payload={"model": "test"})
    assert len(runtime.failures) == 1
    assert runtime.failures[0].hook_name == "before_provider_call"
    assert runtime.failures[0].error == "boom"


def test_hook_manager_can_deny_execution_without_using_runtime_events() -> None:
    calls: list[str] = []
    registry = HookRegistry()
    registry.register(
        "before_tool_call",
        lambda context: HookResult(
            action="deny",
            reason=f"blocked {context.payload['tool_name']}",
        ),
    )
    registry.register("before_tool_call", lambda context: calls.append("wrong"))

    result = HookManager(registry).dispatch(
        "before_tool_call",
        {"tool_name": "delete_file"},
    )

    assert result == HookResult(action="deny", reason="blocked delete_file")
    assert calls == []


def test_hook_manager_can_modify_payload_for_later_hooks() -> None:
    observed: list[dict[str, object]] = []
    registry = HookRegistry()
    registry.register(
        "before_tool_call",
        lambda context: HookResult(
            action="modify",
            payload={**dict(context.payload), "tool_name": "safe_read_file"},
        ),
    )
    registry.register(
        "before_tool_call",
        lambda context: observed.append(dict(context.payload)),
    )

    result = HookManager(registry).dispatch(
        "before_tool_call",
        {"tool_name": "read_file"},
    )

    assert result == HookResult(
        action="modify",
        payload={"tool_name": "safe_read_file"},
    )
    assert observed == [{"tool_name": "safe_read_file"}]


def test_hook_manager_can_raise_on_failure() -> None:
    registry = HookRegistry()
    registry.register(
        "before_provider_call",
        lambda context: (_ for _ in ()).throw(RuntimeError("boom")),
        failure_policy="raise",
    )
    runtime = HookManager(registry)

    with pytest.raises(RuntimeError, match="boom"):
        runtime.dispatch("before_provider_call")

    assert runtime.failures[0].hook_name == "before_provider_call"


def test_hook_context_payload_is_read_only() -> None:
    captured: list[HookContext] = []
    registry = HookRegistry()
    registry.register("before_tool_call", lambda context: captured.append(context))

    HookManager(registry).dispatch("before_tool_call", {"tool_name": "read_file"})

    with pytest.raises(TypeError):
        captured[0].payload["tool_name"] = "write_file"


def test_hook_manager_dispatches_lower_priority_first() -> None:
    calls: list[str] = []
    registry = HookRegistry()
    registry.register("before_tool_call", lambda context: calls.append("late"), priority=100)
    registry.register("before_tool_call", lambda context: calls.append("early"), priority=50)

    HookManager(registry).dispatch("before_tool_call")

    assert calls == ["early", "late"]


def test_hook_manager_decorator_registers_handler() -> None:
    calls: list[str] = []
    manager = HookManager(HookRegistry())

    @manager.on("after_provider_call", priority=25)
    def record(context: HookContext) -> None:
        calls.append(str(context.payload["model"]))

    manager.dispatch("after_provider_call", {"model": "fake"})

    assert calls == ["fake"]


def test_hook_registry_rejects_async_handlers() -> None:
    registry = HookRegistry()

    async def handler(context: HookContext) -> None:
        return None

    with pytest.raises(TypeError, match="async handler not supported"):
        registry.register("before_provider_call", handler)
