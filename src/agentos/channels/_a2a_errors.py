from __future__ import annotations

from agentos.artifacts.types import (
    ArtifactMediaTypeUnsupportedError,
    ArtifactValidationError,
)
from agentos.distributed.errors import (
    A2APushConfigNotFoundError,
    A2APushConflictError,
    A2ATaskConflictError,
    A2ATaskNotFoundError,
    ActiveRunConflictError,
    CommandConflictError,
    CommandNotDueError,
    CommandStateError,
    DistributedBackendUnavailableError,
    DistributedStoreClosedError,
    RunNotFoundError,
    RunSubmissionConflictError,
    SideEffectInFlightError,
)
from agentos.transports.a2a.mapping import A2AMappingError
from agentos.transports.a2a.operation_types import A2AOperationError
from agentos.transports.a2a.protocol import (
    A2AExtensionNegotiationError,
    A2AProtocolVersionError,
)
from agentos.transports.run_stream import RunStreamCursorError


class A2AChannelOperationError(RuntimeError):
    """Channel 确定性策略产生的 A2A operation error。"""

    def __init__(self, code: int) -> None:
        self.error = A2AOperationError(code)
        super().__init__(self.error.message)


def map_a2a_operation_error(
    error: BaseException,
    *,
    method: str,
) -> A2AOperationError:
    if type(error) is A2AChannelOperationError:
        return error.error
    if type(error) is A2AProtocolVersionError:
        return error.error
    if type(error) is A2AExtensionNegotiationError:
        return error.error
    if isinstance(
        error,
        (A2ATaskNotFoundError, A2APushConfigNotFoundError, RunNotFoundError),
    ):
        return A2AOperationError(-32001)
    if method == "CancelTask" and isinstance(
        error,
        (
            ActiveRunConflictError,
            CommandConflictError,
            CommandNotDueError,
            CommandStateError,
            SideEffectInFlightError,
        ),
    ):
        return A2AOperationError(-32002)
    if isinstance(error, ArtifactMediaTypeUnsupportedError):
        return A2AOperationError(-32005)
    if isinstance(
        error,
        (
            A2AMappingError,
            A2APushConflictError,
            A2ATaskConflictError,
            ActiveRunConflictError,
            ArtifactValidationError,
            CommandConflictError,
            CommandNotDueError,
            CommandStateError,
            RunSubmissionConflictError,
            RunStreamCursorError,
        ),
    ):
        return A2AOperationError(-32602)
    if isinstance(
        error,
        (DistributedBackendUnavailableError, DistributedStoreClosedError),
    ):
        return A2AOperationError(-32603)
    return A2AOperationError(-32603)


__all__ = ["A2AChannelOperationError", "map_a2a_operation_error"]
