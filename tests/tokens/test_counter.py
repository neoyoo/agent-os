from dataclasses import dataclass

from agentos.messages import StoredMessage
from agentos.tokens import HeuristicTokenCounter


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str


@dataclass(frozen=True)
class ProviderSerializationTrap:
    value: str

    def to_provider_dict(self) -> dict[str, object]:
        raise AssertionError("token counter must not use Provider serialization hooks")


def test_heuristic_counter_counts_text_with_ceiling() -> None:
    counter = HeuristicTokenCounter(char_per_token=4)

    assert counter.count_text("abcde") == 2


def test_heuristic_counter_counts_messages_and_tools() -> None:
    counter = HeuristicTokenCounter(char_per_token=4)
    message = StoredMessage(id="msg_1", role="tool", content="abcdefgh")

    total = counter.count_messages(
        [message],
        tools=[ToolSpec(name="read_file", description="Read a file")],
    )

    assert total > counter.count_text(message.content)


def test_heuristic_counter_does_not_use_provider_serialization_hooks() -> None:
    counter = HeuristicTokenCounter(char_per_token=4)

    assert counter.count_messages([ProviderSerializationTrap(value="abcdefgh")]) > 0
