from agentos.messages import Message, ToolCall
from agentos.policies import TokenBudgetPolicy
from agentos.tokens import HeuristicTokenCounter


def test_token_budget_policy_triggers_with_headroom_and_static_overhead() -> None:
    policy = TokenBudgetPolicy(
        token_counter=HeuristicTokenCounter(char_per_token=1),
        context_window=20,
        reserve_output_tokens=5,
        retain_latest_tokens=5,
        static_overhead_tokens=6,
    )
    messages = [
        Message(id="msg_1", role="user", content="12345"),
        Message(id="msg_2", role="assistant", content="67890"),
    ]

    assert policy.effective_window == 15
    assert policy.should_compress(messages) is True


def test_token_budget_policy_keeps_latest_suffix_by_token_budget() -> None:
    policy = TokenBudgetPolicy(
        token_counter=HeuristicTokenCounter(char_per_token=1),
        context_window=15,
        reserve_output_tokens=0,
        retain_latest_tokens=8,
    )
    messages = [
        Message(id="msg_1", role="user", content="11111"),
        Message(id="msg_2", role="assistant", content="22222"),
        Message(id="msg_3", role="user", content="33333"),
        Message(id="msg_4", role="assistant", content="44444"),
    ]

    assert policy.oldest_prefix_size(messages) == 3


def test_token_budget_policy_returns_zero_when_under_budget() -> None:
    policy = TokenBudgetPolicy(
        token_counter=HeuristicTokenCounter(char_per_token=1),
        context_window=30,
        reserve_output_tokens=0,
        retain_latest_tokens=8,
    )
    messages = [Message(id="msg_1", role="user", content="small")]

    assert policy.oldest_prefix_size(messages) == 0


def test_token_budget_policy_works_with_tool_pair_expansion() -> None:
    from agentos.compression import Evictor

    policy = TokenBudgetPolicy(
        token_counter=HeuristicTokenCounter(char_per_token=1),
        context_window=12,
        reserve_output_tokens=0,
        retain_latest_tokens=4,
    )
    messages = [
        Message(id="user_1", role="user", content="11111"),
        Message(
            id="assistant_1",
            role="assistant",
            content="tool",
            tool_calls=[ToolCall(id="call_1", name="read_file")],
        ),
        Message(
            id="tool_1",
            role="tool",
            content="result",
            tool_call_id="call_1",
        ),
        Message(id="user_2", role="user", content="2"),
    ]

    assert Evictor(policy).select_message_ids(messages) == [
        "user_1",
        "assistant_1",
        "tool_1",
    ]
