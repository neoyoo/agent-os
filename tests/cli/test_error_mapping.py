from __future__ import annotations

import pytest

from agentos.artifacts.types import ArtifactNotFoundError
from agentos.cli.auth import CliPermissionError
from agentos.cli.errors import map_cli_error
from agentos.distributed.errors import (
    ArtifactConflictError,
    CommandStateError,
    DistributedBackendUnavailableError,
    RunNotFoundError,
    SchemaMigrationRequiredError,
    SideEffectInFlightError,
)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (CliPermissionError(), (3, "cli_permission_denied")),
        (RunNotFoundError(), (4, "run_not_found")),
        (ArtifactNotFoundError(), (4, "artifact_not_found")),
        (ArtifactConflictError(), (5, "artifact_conflict")),
        (CommandStateError(), (5, "command_state")),
        (SideEffectInFlightError(), (5, "side_effect_in_flight")),
        (SchemaMigrationRequiredError(), (5, "schema_migration_required")),
        (
            DistributedBackendUnavailableError(),
            (6, "distributed_backend_unavailable"),
        ),
        (ValueError("secret payload"), (2, "invalid_cli_input")),
        (OSError("C:/secret/file"), (2, "cli_io_error")),
    ],
)
def test_cli_error_exit_matrix_is_stable(
    error: BaseException,
    expected: tuple[int, str],
) -> None:
    mapped = map_cli_error(error)

    assert (mapped.exit_code, mapped.code) == expected


def test_internal_error_never_exposes_adapter_details() -> None:
    mapped = map_cli_error(
        RuntimeError(
            "postgresql://user:password@host token=secret SELECT * C:/private",
        ),
    )

    assert mapped.exit_code == 1
    assert mapped.code == "internal_error"
    assert mapped.message == "internal error"
