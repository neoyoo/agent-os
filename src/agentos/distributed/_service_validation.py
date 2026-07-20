from __future__ import annotations

from agentos.distributed.models import (
    ReplayItem,
    RequestScope,
    RunReadModel,
    StreamGap,
)
from agentos.distributed.errors import RunNotFoundError
from agentos.distributed.protocols import EventSubscription, RunQueryPort


async def get_run(
    port: RunQueryPort,
    scope: RequestScope,
    session_id: str,
    run_id: str,
) -> RunReadModel:
    run = await port.get_run(
        scope=scope,
        session_id=session_id,
        run_id=run_id,
    )
    if run is None:
        raise RunNotFoundError()
    if type(run) is not RunReadModel or (
        run.tenant_id != scope.tenant_id
        or run.session_id != session_id
        or run.run_id != run_id
    ):
        raise RuntimeError("run query port returned another run read model")
    return run


def validated_replay(
    events: EventSubscription,
    tenant_id: str,
    session_id: str,
    run_id: str,
    requested_cursor: str | None,
) -> EventSubscription:
    return _ValidatedEventSubscription(
        events,
        (tenant_id, session_id, run_id),
        requested_cursor,
    )


class _ValidatedEventSubscription:
    __slots__ = ("_closed", "_expected", "_requested_cursor", "_source")

    def __init__(
        self,
        source: EventSubscription,
        expected: tuple[str, str, str],
        requested_cursor: str | None,
    ) -> None:
        self._source = source
        self._expected = expected
        self._requested_cursor = requested_cursor
        self._closed = False

    def __aiter__(self) -> EventSubscription:
        return self

    async def __anext__(self) -> ReplayItem | StreamGap:
        if self._closed:
            raise StopAsyncIteration
        try:
            item = await self._source.__anext__()
            _validate_replay_item(item, self._expected, self._requested_cursor)
            return item
        except StopAsyncIteration:
            await self.aclose()
            raise
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._source.aclose()


def _validate_replay_item(
    item: ReplayItem | StreamGap,
    expected: tuple[str, str, str],
    requested_cursor: str | None,
) -> None:
    if type(item) is ReplayItem:
        actual = (
            item.event.tenant_id,
            item.event.session_id,
            item.event.run_id,
        )
        label = "replay item"
    elif type(item) is StreamGap:
        actual = (item.tenant_id, item.session_id, item.run_id)
        label = "stream gap"
        if item.requested_cursor != requested_cursor:
            raise RuntimeError("event replay port returned another stream gap")
    else:
        raise TypeError("event replay port returned an invalid item")
    if actual != expected:
        raise RuntimeError(f"event replay port returned another {label}")
