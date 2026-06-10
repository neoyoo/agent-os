from dataclasses import dataclass

from agentos.messages import Message
from agentos.tokens import HeuristicTokenCounter


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str


def test_heuristic_counter_counts_text_with_ceiling() -> None:
    counter = HeuristicTokenCounter(char_per_token=4)

    assert counter.count_text("abcde") == 2


def test_heuristic_counter_counts_messages_and_tools() -> None:
    counter = HeuristicTokenCounter(char_per_token=4)
    message = Message(id="msg_1", role="tool", content="abcdefgh")

    total = counter.count_messages(
        [message],
        tools=[ToolSpec(name="read_file", description="Read a file")],
    )

    assert total > counter.count_text(message.content)
