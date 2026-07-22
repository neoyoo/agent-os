from __future__ import annotations

from dataclasses import dataclass

from agentos.artifacts.types import ArtifactNotFoundError
from agentos.cli.application import CliFactoryConfigurationError
from agentos.cli.auth import CliAuthenticationError, CliPermissionError
from agentos.distributed.errors import (
    ActiveRunConflictError,
    ArtifactConflictError,
    ArtifactInUseError,
    CommandConflictError,
    CommandNotDueError,
    CommandStateError,
    DeliveryUnavailableError,
    DistributedBackendUnavailableError,
    LegacyDistributedSchemaError,
    MigrationChecksumMismatchError,
    MigrationVersionError,
    RunNotFoundError,
    RunSubmissionConflictError,
    SchemaMigrationRequiredError,
    SideEffectInFlightError,
)


@dataclass(frozen=True, slots=True)
class CliError:
    """稳定的 CLI 退出码、错误码和脱敏消息。"""

    exit_code: int
    code: str
    message: str


_AUTH_ERRORS = (CliAuthenticationError, CliPermissionError)
_CONFLICT_ERRORS = (
    ActiveRunConflictError,
    ArtifactConflictError,
    ArtifactInUseError,
    CommandConflictError,
    CommandNotDueError,
    CommandStateError,
    LegacyDistributedSchemaError,
    MigrationChecksumMismatchError,
    MigrationVersionError,
    RunSubmissionConflictError,
    SchemaMigrationRequiredError,
    SideEffectInFlightError,
)
_BACKEND_ERRORS = (
    DeliveryUnavailableError,
    DistributedBackendUnavailableError,
)


def map_cli_error(error: BaseException) -> CliError:
    """将领域或边界异常映射为稳定且脱敏的 CLI 错误。"""

    if isinstance(error, _AUTH_ERRORS):
        return CliError(3, error.code, error.message)
    if isinstance(error, RunNotFoundError):
        return CliError(4, error.code, error.message)
    if isinstance(error, ArtifactNotFoundError):
        return CliError(4, "artifact_not_found", "artifact not found")
    if isinstance(error, _CONFLICT_ERRORS):
        return CliError(5, error.code, error.message)
    if isinstance(error, _BACKEND_ERRORS):
        return CliError(6, error.code, error.message)
    if isinstance(error, CliFactoryConfigurationError):
        return CliError(2, error.code, str(error))
    if isinstance(error, OSError):
        return CliError(2, "cli_io_error", "CLI input or output failed")
    if isinstance(error, (TypeError, ValueError)):
        return CliError(2, "invalid_cli_input", "invalid CLI input")
    return CliError(1, "internal_error", "internal error")


__all__ = ["CliError", "map_cli_error"]
