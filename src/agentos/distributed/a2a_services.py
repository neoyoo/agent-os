from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from agentos._json_values import freeze_json_mapping
from agentos.distributed._model_validation import require_identifier
from agentos.distributed.a2a_models import (
    A2APushConfigInput,
    A2APushConfigPage,
    A2APushConfigRecord,
    A2APushConfigRecordPage,
    A2APushConfigView,
    A2ATaskBinding,
    A2ATaskListPage,
    A2ATaskListQuery,
)
from agentos.distributed.a2a_protocols import (
    A2APushPort,
    A2APushUrlPolicy,
    A2ATaskCatalogPort,
    A2ATaskPort,
)
from agentos.distributed.errors import (
    A2APushConfigNotFoundError,
    A2ATaskNotFoundError,
)
from agentos.distributed.models import RequestScope
from agentos.runtime.payloads import (
    PayloadProtectionContext,
    PayloadProtector,
    protect_payload,
)


@dataclass(frozen=True, slots=True)
class A2ATaskService:
    """A2A Channel 使用的 task/run binding Application Service。"""

    port: A2ATaskPort

    async def bind(
        self,
        scope: RequestScope,
        *,
        task_id: str,
        session_id: str,
        run_id: str,
    ) -> A2ATaskBinding:
        """幂等持久化一个 v1 task/run binding。"""

        _require_scope(scope)
        binding = A2ATaskBinding(
            tenant_id=scope.tenant_id,
            task_id=task_id,
            session_id=session_id,
            run_id=run_id,
        )
        resolved = await self.port.bind(scope=scope, binding=binding)
        if type(resolved) is not A2ATaskBinding or resolved != binding:
            raise RuntimeError("a2a task port returned another binding")
        return resolved

    async def resolve(
        self,
        scope: RequestScope,
        task_id: str,
    ) -> A2ATaskBinding:
        """解析 task；未知与跨 tenant 使用相同 not-found。"""

        _require_scope(scope)
        require_identifier(task_id, "task_id")
        binding = await self.port.resolve(scope=scope, task_id=task_id)
        if binding is None:
            raise A2ATaskNotFoundError()
        if type(binding) is not A2ATaskBinding or (
            binding.tenant_id != scope.tenant_id or binding.task_id != task_id
        ):
            raise RuntimeError("a2a task port returned another binding")
        return binding


@dataclass(frozen=True, slots=True)
class A2ATaskCatalogService:
    """Validated tenant-scoped A2A task catalog Application Service."""

    port: A2ATaskCatalogPort

    async def list(
        self,
        scope: RequestScope,
        query: A2ATaskListQuery,
    ) -> A2ATaskListPage:
        _require_scope(scope)
        if type(query) is not A2ATaskListQuery:
            raise TypeError("query must be A2ATaskListQuery")
        page = await self.port.list(scope=scope, query=query)
        if type(page) is not A2ATaskListPage:
            raise RuntimeError("a2a task catalog port returned an invalid page")
        if page.page_size != query.page_size:
            raise RuntimeError("a2a task catalog port returned another page size")
        if any(item.binding.tenant_id != scope.tenant_id for item in page.items):
            raise RuntimeError("a2a task catalog port returned another scope")
        positions = tuple(
            (item.status_updated_at, item.binding.task_id) for item in page.items
        )
        if positions != tuple(sorted(positions, reverse=True)):
            raise RuntimeError("a2a task catalog port returned an unordered page")
        return page


@dataclass(frozen=True, slots=True)
class A2APushService:
    """Secret-protecting A2A push config Application Service."""

    port: A2APushPort
    url_policy: A2APushUrlPolicy
    payload_protector: PayloadProtector

    async def create(
        self,
        scope: RequestScope,
        *,
        binding: A2ATaskBinding,
        config_input: A2APushConfigInput,
        operation_id: str,
    ) -> A2APushConfigView:
        _require_scope(scope)
        if type(binding) is not A2ATaskBinding:
            raise TypeError("binding must be A2ATaskBinding")
        if binding.tenant_id != scope.tenant_id:
            raise ValueError("binding tenant must match request scope")
        if type(config_input) is not A2APushConfigInput:
            raise TypeError("config_input must be A2APushConfigInput")
        require_identifier(operation_id, "operation_id")
        config_id = config_input.config_id or _derived_config_id(operation_id)
        self.url_policy.validate(config_input.url)
        scheme = (
            None
            if config_input.authentication is None
            else config_input.authentication.scheme
        )
        secrets = {
            key: value
            for key, value in (
                ("credentials", _credentials(config_input)),
                ("token", config_input.token),
            )
            if value is not None
        }
        secret_ref = None
        if secrets:
            secret_ref = protect_payload(
                self.payload_protector,
                freeze_json_mapping(secrets),
                context=PayloadProtectionContext(
                    tenant_id=scope.tenant_id,
                    session_id=binding.session_id,
                ),
            )
        record = A2APushConfigRecord(
            tenant_id=scope.tenant_id,
            task_id=binding.task_id,
            config_id=config_id,
            url=config_input.url,
            authentication_scheme=scheme,
            secret_ref=secret_ref,
        )
        persisted = await self.port.create(
            scope=scope,
            binding=binding,
            record=record,
            operation_id=operation_id,
        )
        _validate_record(persisted, scope, binding.task_id, record.config_id)
        if not _records_equivalent(persisted, record):
            raise RuntimeError("a2a push port returned another config")
        return _record_to_view(persisted)

    async def get(
        self,
        scope: RequestScope,
        *,
        task_id: str,
        config_id: str,
    ) -> A2APushConfigView:
        _require_scope(scope)
        require_identifier(task_id, "task_id")
        require_identifier(config_id, "config_id")
        record = await self.port.get(
            scope=scope,
            task_id=task_id,
            config_id=config_id,
        )
        if record is None:
            raise A2APushConfigNotFoundError()
        _validate_record(record, scope, task_id, config_id)
        return _record_to_view(record)

    async def list(
        self,
        scope: RequestScope,
        *,
        task_id: str,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> A2APushConfigPage:
        _require_scope(scope)
        require_identifier(task_id, "task_id")
        _validate_page_request(page_size, page_token)
        page = await self.port.list(
            scope=scope,
            task_id=task_id,
            page_size=page_size,
            page_token=page_token,
        )
        if type(page) is not A2APushConfigRecordPage:
            raise RuntimeError("a2a push port returned an invalid page")
        records = page.records
        for record in records:
            _validate_record(record, scope, task_id, record.config_id)
        config_ids = tuple(record.config_id for record in records)
        if (
            config_ids != tuple(sorted(config_ids))
            or len(set(config_ids)) != len(config_ids)
            or len(records) > page_size
            or (page.next_page_token and len(records) != page_size)
        ):
            raise RuntimeError("a2a push port returned an invalid page")
        return A2APushConfigPage(
            configs=tuple(_record_to_view(record) for record in records),
            next_page_token=page.next_page_token,
        )

    async def delete(
        self,
        scope: RequestScope,
        *,
        task_id: str,
        config_id: str,
        operation_id: str,
    ) -> None:
        _require_scope(scope)
        require_identifier(task_id, "task_id")
        require_identifier(config_id, "config_id")
        require_identifier(operation_id, "operation_id")
        await self.port.delete(
            scope=scope,
            task_id=task_id,
            config_id=config_id,
            operation_id=operation_id,
        )


def _credentials(config: A2APushConfigInput) -> str | None:
    authentication = config.authentication
    return None if authentication is None else authentication.credentials


def _validate_record(
    record: object,
    scope: RequestScope,
    task_id: str,
    config_id: str,
) -> None:
    if type(record) is not A2APushConfigRecord or (
        record.tenant_id != scope.tenant_id
        or record.task_id != task_id
        or record.config_id != config_id
    ):
        raise RuntimeError("a2a push port returned another config")


def _record_to_view(record: A2APushConfigRecord) -> A2APushConfigView:
    return A2APushConfigView(
        task_id=record.task_id,
        config_id=record.config_id,
        url=record.url,
        authentication_scheme=record.authentication_scheme,
    )


def _records_equivalent(
    left: A2APushConfigRecord,
    right: A2APushConfigRecord,
) -> bool:
    return (
        left.tenant_id == right.tenant_id
        and left.task_id == right.task_id
        and left.config_id == right.config_id
        and left.url == right.url
        and left.authentication_scheme == right.authentication_scheme
        and _secret_digest(left) == _secret_digest(right)
    )


def _derived_config_id(operation_id: str) -> str:
    digest = sha256(operation_id.encode("utf-8")).hexdigest()
    return f"a2a_push_{digest}"


def _secret_digest(record: A2APushConfigRecord) -> str | None:
    reference = record.secret_ref
    return None if reference is None else reference.digest


def _require_scope(scope: object) -> None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")


def _validate_page_request(page_size: object, page_token: object) -> None:
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    if page_token is not None and (
        type(page_token) is not str
        or not page_token
        or len(page_token) > 1024
        or any(ord(character) > 127 for character in page_token)
    ):
        raise ValueError("page_token is invalid")


__all__ = ["A2APushService", "A2ATaskCatalogService", "A2ATaskService"]
