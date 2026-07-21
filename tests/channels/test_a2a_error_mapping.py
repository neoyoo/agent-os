from __future__ import annotations

import pytest

from agentos.artifacts.types import ArtifactMediaTypeUnsupportedError
from agentos.channels._a2a_errors import (
    A2AChannelOperationError,
    map_a2a_operation_error,
)
from agentos.distributed.errors import (
    A2APushConfigNotFoundError,
    A2APushConflictError,
    CommandStateError,
    DistributedBackendUnavailableError,
)
from agentos.transports.a2a.mapping import A2AMappingError


@pytest.mark.parametrize(
    ("error", "method", "code"),
    [
        (A2APushConfigNotFoundError(), "GetTaskPushNotificationConfig", -32001),
        (CommandStateError(), "CancelTask", -32002),
        (CommandStateError(), "SendMessage", -32602),
        (A2AMappingError(), "SendMessage", -32602),
        (A2APushConflictError(), "CreateTaskPushNotificationConfig", -32602),
        (ArtifactMediaTypeUnsupportedError(), "SendMessage", -32005),
        (DistributedBackendUnavailableError(), "GetTask", -32603),
        (RuntimeError("postgresql://secret"), "GetTask", -32603),
    ],
)
def test_a2a_error_mapping_is_typed_method_aware_and_redacted(
    error: BaseException,
    method: str,
    code: int,
) -> None:
    mapped = map_a2a_operation_error(error, method=method)

    assert mapped.code == code
    assert "secret" not in mapped.message


def test_channel_policy_error_preserves_only_approved_code() -> None:
    mapped = map_a2a_operation_error(
        A2AChannelOperationError(-32004),
        method="SubscribeToTask",
    )

    assert mapped.code == -32004
