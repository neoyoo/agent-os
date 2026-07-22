from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias

from agentos.distributed.models import RequestScope


CliOperation: TypeAlias = Literal[
    "run_submit",
    "run_command",
    "side_effect_resolve",
    "run_query",
    "run_watch",
    "artifact_upload",
    "artifact_list",
    "artifact_read",
    "artifact_delete",
]

_OPERATIONS = frozenset(
    {
        "run_submit",
        "run_command",
        "side_effect_resolve",
        "run_query",
        "run_watch",
        "artifact_upload",
        "artifact_list",
        "artifact_read",
        "artifact_delete",
    },
)
_RUN_RESOURCES = frozenset(
    {"run_command", "side_effect_resolve", "run_query", "run_watch"},
)
_ARTIFACT_RESOURCES = frozenset({"artifact_read", "artifact_delete"})


@dataclass(frozen=True, slots=True)
class CliResource:
    """描述一次 CLI 授权所访问的最窄领域资源。"""

    session_id: str
    run_id: str | None = None
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("session_id", "run_id", "artifact_id"):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or not value.strip()):
                raise ValueError(f"{name} must not be empty")


class CliScopeResolver(Protocol):
    """将部署可信身份和 route hint 解析为授权后的请求作用域。"""

    async def resolve(
        self,
        operation: CliOperation,
        resource: CliResource,
        tenant_hint: str,
    ) -> RequestScope: ...


class CliAuthenticationError(RuntimeError):
    """表示 CLI 调用方尚未通过部署身份认证。"""

    code = "cli_authentication_required"
    message = "CLI authentication is required"

    def __init__(self) -> None:
        super().__init__(self.message)


class CliPermissionError(RuntimeError):
    """表示 CLI 调用方无权执行指定领域操作。"""

    code = "cli_permission_denied"
    message = "CLI operation is not permitted"

    def __init__(self) -> None:
        super().__init__(self.message)


async def resolve_cli_scope(
    resolver: CliScopeResolver,
    operation: CliOperation,
    resource: CliResource,
    tenant_hint: str,
) -> RequestScope:
    """校验操作资源形状并解析可信的 tenant 请求作用域。"""

    if operation not in _OPERATIONS:
        raise ValueError("CLI operation is invalid")
    if type(resource) is not CliResource:
        raise TypeError("resource must be CliResource")
    if type(tenant_hint) is not str or not tenant_hint.strip():
        raise ValueError("tenant_hint must not be empty")
    _validate_resource_shape(operation, resource)
    scope = await resolver.resolve(operation, resource, tenant_hint)
    if type(scope) is not RequestScope:
        raise RuntimeError("CLI scope resolver must return RequestScope")
    return scope


def _validate_resource_shape(
    operation: CliOperation,
    resource: CliResource,
) -> None:
    if operation in _RUN_RESOURCES:
        valid = resource.run_id is not None and resource.artifact_id is None
    elif operation in _ARTIFACT_RESOURCES:
        valid = resource.run_id is None and resource.artifact_id is not None
    else:
        valid = resource.run_id is None and resource.artifact_id is None
    if not valid:
        raise ValueError("CLI resource does not match operation")


__all__ = [
    "CliAuthenticationError",
    "CliOperation",
    "CliPermissionError",
    "CliResource",
    "CliScopeResolver",
    "resolve_cli_scope",
]
