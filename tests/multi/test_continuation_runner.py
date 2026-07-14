from agentos.multi.continuation_runner import run_local_continuation
from agentos.runtime import LocalContinuationInput


class RecordingSyncAgent:
    def __init__(self) -> None:
        self.inputs: list[object] = []

    def run(self, input: object) -> object:
        self.inputs.append(input)
        return object()


def test_local_continuation_uses_typed_continuation_input() -> None:
    agent = RecordingSyncAgent()

    run_local_continuation(agent)  # type: ignore[arg-type]

    assert agent.inputs == [LocalContinuationInput()]
