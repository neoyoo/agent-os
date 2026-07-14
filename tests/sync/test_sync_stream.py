import inspect
from threading import Thread

import pytest

from agentos.runtime.errors import SyncStreamConsumerError
from agentos.sync import SyncAgent, SyncAgentStream
from tests.runtime._query_loop_contract_fixtures import make_recording_agent


def test_sync_agent_stream_constructor_is_owned_by_sync_agent() -> None:
    assert str(inspect.signature(SyncAgentStream)) == "() -> 'None'"
    with pytest.raises(TypeError, match="created by SyncAgent"):
        SyncAgentStream()


def test_sync_stream_allows_cross_thread_close_but_one_consumer_thread() -> None:
    agent, _ = make_recording_agent()
    sync_agent = SyncAgent(agent)
    stream = sync_agent.run("hello", stream=True)
    first = next(stream)
    errors: list[BaseException] = []

    def consume() -> None:
        try:
            next(stream)
        except BaseException as error:
            errors.append(error)

    consumer = Thread(target=consume)
    consumer.start()
    consumer.join(timeout=2)
    closer = Thread(target=stream.close)
    closer.start()
    closer.join(timeout=2)
    sync_agent.close()

    assert first is not None
    assert len(errors) == 1
    assert isinstance(errors[0], SyncStreamConsumerError)
    assert stream.closed
