from agentos.policies import ToolResultBudget
from agentos.policies.tool_result_budget import cap_tool_result_content
from agentos.tokens import HeuristicTokenCounter


def test_tool_result_budget_uses_override_before_default() -> None:
    budget = ToolResultBudget(default_max_tokens=100, overrides={"read_file": 3})

    assert budget.cap_for("read_file") == 3
    assert budget.cap_for("grep") == 100


def test_tool_result_budget_env_overrides_all(monkeypatch) -> None:
    monkeypatch.setenv("AGENTOS_TOOL_RESULT_MAX_TOKENS", "7")
    budget = ToolResultBudget(default_max_tokens=100, overrides={"read_file": 3})

    assert budget.cap_for("read_file") == 7


def test_tool_result_budget_ignores_invalid_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENTOS_TOOL_RESULT_MAX_TOKENS", "invalid")
    budget = ToolResultBudget(default_max_tokens=100, overrides={"read_file": 3})

    assert budget.cap_for("read_file") == 3


def test_cap_tool_result_replaces_oversized_content_with_small_nudge() -> None:
    capped = cap_tool_result_content(
        tool_name="read_file",
        content="x" * 100,
        budget=ToolResultBudget(default_max_tokens=5),
        token_counter=HeuristicTokenCounter(char_per_token=1),
    )

    assert capped.capped is True
    assert capped.actual_tokens == 100
    assert capped.cap == 5
    assert "read_file" in capped.content
    assert "100" in capped.content
    assert "xxxxx" not in capped.content


def test_cap_tool_result_preserves_content_within_budget() -> None:
    capped = cap_tool_result_content(
        tool_name="read_file",
        content="small",
        budget=ToolResultBudget(default_max_tokens=10),
        token_counter=HeuristicTokenCounter(char_per_token=1),
    )

    assert capped.capped is False
    assert capped.content == "small"
