from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from typing import Protocol

from agentos.distributed.a2a_models import (
    A2APushConfigRecord,
    A2APushConfigRecordPage,
    A2APushAttemptResolution,
    A2APushDeliveryTarget,
    A2APushFailureCategory,
    A2ATaskBinding,
    A2ATaskListPage,
    A2ATaskListQuery,
)
from agentos.distributed.models import RequestScope


class A2ATaskPort(Protocol):
    """A2A task/run binding 的 tenant-scoped 持久化边界。"""

    async def bind(
        self,
        *,
        scope: RequestScope,
        binding: A2ATaskBinding,
    ) -> A2ATaskBinding: ...

    async def resolve(
        self,
        *,
        scope: RequestScope,
        task_id: str,
    ) -> A2ATaskBinding | None: ...


class A2ATaskCatalogPort(Protocol):
    """Tenant-scoped A2A task catalog query boundary."""

    async def list(
        self,
        *,
        scope: RequestScope,
        query: A2ATaskListQuery,
    ) -> A2ATaskListPage: ...


class A2APushUrlPolicy(Protocol):
    """Application policy for accepting an outbound push URL."""

    def validate(self, url: str) -> None: ...


class A2APushPort(Protocol):
    """Tenant-scoped protected push config persistence boundary."""

    async def create(
        self,
        *,
        scope: RequestScope,
        binding: A2ATaskBinding,
        record: A2APushConfigRecord,
        operation_id: str,
    ) -> A2APushConfigRecord: ...

    async def get(
        self,
        *,
        scope: RequestScope,
        task_id: str,
        config_id: str,
    ) -> A2APushConfigRecord | None: ...

    async def list(
        self,
        *,
        scope: RequestScope,
        task_id: str,
        page_size: int,
        page_token: str | None,
    ) -> A2APushConfigRecordPage: ...

    async def delete(
        self,
        *,
        scope: RequestScope,
        task_id: str,
        config_id: str,
        operation_id: str,
    ) -> None: ...


class A2APushDeliveryPort(Protocol):
    """PostgreSQL outbound Push delivery truth and acknowledgement boundary。"""

    async def open_attempt(
        self,
        *,
        outbox_id: str,
        worker_id: str,
        ttl: timedelta,
    ) -> A2APushDeliveryAttempt | None: ...


class A2APushDeliveryAttempt(Protocol):
    """单个带 fencing identity 的 outbound Push attempt。"""

    @property
    def target(self) -> A2APushDeliveryTarget: ...

    @property
    def attempt_id(self) -> str: ...

    @property
    def expires_at(self) -> datetime: ...

    def authorize_send(self) -> AbstractAsyncContextManager[None]: ...

    async def mark_delivered(
        self,
    ) -> None: ...

    async def mark_failed(
        self,
        *,
        category: A2APushFailureCategory,
    ) -> A2APushAttemptResolution: ...


__all__ = [
    "A2APushPort",
    "A2APushDeliveryAttempt",
    "A2APushDeliveryPort",
    "A2APushUrlPolicy",
    "A2ATaskCatalogPort",
    "A2ATaskPort",
]
