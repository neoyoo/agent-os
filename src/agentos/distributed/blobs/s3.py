from __future__ import annotations

import asyncio
from importlib import import_module
from typing import Protocol
from urllib.parse import urlsplit

from agentos.artifacts.types import (
    ArtifactValidationError,
    validate_artifact_id,
)
from agentos.distributed.errors import (
    DistributedBackendUnavailableError,
    DistributedStoreClosedError,
)


class _AsyncClientContext(Protocol):
    async def __aenter__(self) -> object: ...

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> object: ...


class S3BlobStore:
    """使用调用方配置的 S3-compatible backend 保存共享 Artifact bytes。"""

    def __init__(
        self,
        *,
        client: object,
        bucket_name: str,
        key_prefix: str = "agentos/blobs",
        _client_context: _AsyncClientContext | None = None,
    ) -> None:
        _validate_bucket_name(bucket_name)
        normalized_prefix = _normalize_key_prefix(key_prefix)
        _validate_client(client)
        self._client = client
        self._bucket_name = bucket_name
        self._key_prefix = normalized_prefix
        self._client_context = _client_context
        self._closed = False
        self._close_lock = asyncio.Lock()

    @classmethod
    async def open(
        cls,
        *,
        bucket_name: str,
        key_prefix: str = "agentos/blobs",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        session: object | None = None,
    ) -> S3BlobStore:
        """延迟创建并持有一个 aioboto3 S3 client。"""

        _validate_bucket_name(bucket_name)
        _normalize_key_prefix(key_prefix)
        _validate_endpoint_url(endpoint_url)
        _validate_region_name(region_name)
        if session is None:
            session = _new_aioboto3_session()
        client_factory = getattr(session, "client", None)
        if not callable(client_factory):
            raise TypeError("session must provide an async client factory")
        options: dict[str, str] = {}
        if endpoint_url is not None:
            options["endpoint_url"] = endpoint_url
        if region_name is not None:
            options["region_name"] = region_name
        try:
            context = client_factory("s3", **options)
            enter = getattr(context, "__aenter__", None)
            exit_context = getattr(context, "__aexit__", None)
            if not callable(enter) or not callable(exit_context):
                raise TypeError
            client = await enter()
        except Exception:
            raise DistributedBackendUnavailableError() from None
        try:
            return cls(
                client=client,
                bucket_name=bucket_name,
                key_prefix=key_prefix,
                _client_context=context,
            )
        except BaseException:
            try:
                await exit_context(None, None, None)
            except Exception:
                pass
            raise

    async def __aenter__(self) -> S3BlobStore:
        self._ensure_open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def put_if_absent(
        self,
        *,
        artifact_id: str,
        data: bytes,
    ) -> bool:
        """使用 S3 条件写保证已有 bytes 不被覆盖。"""

        validate_artifact_id(artifact_id)
        if type(data) is not bytes:
            raise ArtifactValidationError("artifact data must be bytes")
        client = self._ensure_open()
        try:
            await client.put_object(
                Bucket=self._bucket_name,
                Key=self._object_key(artifact_id),
                Body=data,
                IfNoneMatch="*",
            )
        except Exception as error:
            if _is_error(error, codes=("PreconditionFailed", "412"), status=412):
                return False
            raise DistributedBackendUnavailableError() from None
        return True

    async def read(self, *, artifact_id: str) -> bytes | None:
        """读取共享 bytes，且始终关闭响应 body。"""

        validate_artifact_id(artifact_id)
        client = self._ensure_open()
        try:
            response = await client.get_object(
                Bucket=self._bucket_name,
                Key=self._object_key(artifact_id),
            )
        except Exception as error:
            if _is_error(error, codes=("NoSuchKey", "NotFound", "404"), status=404):
                return None
            raise DistributedBackendUnavailableError() from None
        try:
            body = response["Body"]
            async with body:
                data = await body.read()
            if type(data) is not bytes:
                raise TypeError
        except Exception:
            raise DistributedBackendUnavailableError() from None
        return data

    async def delete(self, *, artifact_id: str) -> None:
        """幂等删除共享 bytes。"""

        validate_artifact_id(artifact_id)
        client = self._ensure_open()
        try:
            await client.delete_object(
                Bucket=self._bucket_name,
                Key=self._object_key(artifact_id),
            )
        except Exception:
            raise DistributedBackendUnavailableError() from None

    async def close(self) -> None:
        """幂等关闭 Store 自己通过 session 创建的 client。"""

        async with self._close_lock:
            if self._closed and self._client_context is None:
                return
            self._closed = True
            context = self._client_context
            if context is None:
                return
            try:
                await context.__aexit__(None, None, None)
            except Exception:
                raise DistributedBackendUnavailableError() from None
            self._client_context = None

    def _ensure_open(self) -> object:
        if self._closed:
            raise DistributedStoreClosedError()
        return self._client

    def _object_key(self, artifact_id: str) -> str:
        return f"{self._key_prefix}/{artifact_id}"


def _new_aioboto3_session() -> object:
    try:
        module = import_module("aioboto3")
        session_factory = getattr(module, "Session", None)
        if not callable(session_factory):
            raise TypeError
        return session_factory()
    except Exception:
        raise DistributedBackendUnavailableError() from None


def _validate_client(client: object) -> None:
    if any(
        not callable(getattr(client, operation, None))
        for operation in ("put_object", "get_object", "delete_object")
    ):
        raise TypeError("client must provide async S3 operations")


def _validate_bucket_name(bucket_name: str) -> None:
    if (
        type(bucket_name) is not str
        or not 1 <= len(bucket_name) <= 255
        or bucket_name != bucket_name.strip()
        or any(character.isspace() or ord(character) < 32 for character in bucket_name)
        or "/" in bucket_name
        or "\\" in bucket_name
    ):
        raise ValueError("bucket_name is invalid")


def _normalize_key_prefix(key_prefix: str) -> str:
    if (
        type(key_prefix) is not str
        or not key_prefix
        or len(key_prefix) > 512
        or key_prefix != key_prefix.strip("/")
        or "\\" in key_prefix
        or any(
            not segment
            or segment in (".", "..")
            or any(character.isspace() or ord(character) < 32 for character in segment)
            for segment in key_prefix.split("/")
        )
    ):
        raise ValueError("key_prefix is invalid")
    return key_prefix


def _validate_endpoint_url(endpoint_url: str | None) -> None:
    if endpoint_url is None:
        return
    try:
        parsed = urlsplit(endpoint_url)
        invalid = (
            type(endpoint_url) is not str
            or len(endpoint_url) > 2048
            or endpoint_url != endpoint_url.strip()
            or any(
                character.isspace() or ord(character) < 32
                for character in endpoint_url
            )
            or parsed.scheme not in ("http", "https")
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        )
    except (TypeError, ValueError):
        invalid = True
    if invalid:
        raise ValueError("endpoint_url is invalid")


def _validate_region_name(region_name: str | None) -> None:
    if region_name is not None and (
        type(region_name) is not str
        or not 1 <= len(region_name) <= 255
        or region_name != region_name.strip()
        or any(character.isspace() or ord(character) < 32 for character in region_name)
    ):
        raise ValueError("region_name is invalid")


def _is_error(error: Exception, *, codes: tuple[str, ...], status: int) -> bool:
    response = getattr(error, "response", None)
    if type(response) is not dict:
        return False
    error_data = response.get("Error")
    metadata = response.get("ResponseMetadata")
    code = error_data.get("Code") if type(error_data) is dict else None
    http_status = (
        metadata.get("HTTPStatusCode") if type(metadata) is dict else None
    )
    return code in codes or http_status == status


__all__ = ["S3BlobStore"]
