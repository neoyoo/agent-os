from __future__ import annotations

import asyncio

import pytest

from agentos.artifacts.types import ArtifactValidationError
from agentos.distributed.errors import (
    DistributedBackendUnavailableError,
    DistributedStoreClosedError,
)
from tests.planning._async import async_test


ARTIFACT_ID = "art_12345678-1234-4234-9234-123456789abc"
SECOND_ARTIFACT_ID = "art_abcdefab-cdef-4abc-8def-abcdefabcdef"


class FakeClientError(Exception):
    def __init__(self, code: str, status: int, message: str = "backend detail") -> None:
        super().__init__(message)
        self.response = {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class FakeBody:
    def __init__(self, data: bytes, *, failure: BaseException | None = None) -> None:
        self._data = data
        self._failure = failure
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> FakeBody:
        self.entered = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited = True

    async def read(self) -> bytes:
        if self._failure is not None:
            raise self._failure
        return self._data


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.failure: BaseException | None = None
        self.last_body: FakeBody | None = None

    async def put_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("put_object", kwargs))
        self._raise_failure()
        identity = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        if identity in self.objects:
            raise FakeClientError("PreconditionFailed", 412)
        self.objects[identity] = bytes(kwargs["Body"])
        return {"ETag": '"etag"'}

    async def get_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_object", kwargs))
        self._raise_failure()
        identity = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        try:
            data = self.objects[identity]
        except KeyError:
            raise FakeClientError("NoSuchKey", 404) from None
        self.last_body = FakeBody(data)
        return {"Body": self.last_body}

    async def delete_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("delete_object", kwargs))
        self._raise_failure()
        identity = (str(kwargs["Bucket"]), str(kwargs["Key"]))
        self.objects.pop(identity, None)
        return {}

    def _raise_failure(self) -> None:
        if self.failure is not None:
            raise self.failure


class FakeClientContext:
    def __init__(self, client: FakeS3Client) -> None:
        self.client = client
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> FakeS3Client:
        self.entered = True
        return self.client

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


class FakeSession:
    def __init__(self, context: FakeClientContext) -> None:
        self.context = context
        self.calls: list[tuple[str, dict[str, object]]] = []

    def client(self, service_name: str, **kwargs: object) -> FakeClientContext:
        self.calls.append((service_name, kwargs))
        return self.context


@async_test
async def test_s3_blob_store_conditionally_puts_reads_and_deletes() -> None:
    from agentos.distributed.blobs.s3 import S3BlobStore

    client = FakeS3Client()
    store = S3BlobStore(
        client=client,
        bucket_name="private-artifacts",
        key_prefix="agentos/blobs",
    )

    assert await store.put_if_absent(artifact_id=ARTIFACT_ID, data=b"first") is True
    assert await store.put_if_absent(artifact_id=ARTIFACT_ID, data=b"second") is False
    assert await store.read(artifact_id=ARTIFACT_ID) == b"first"
    assert await store.read(artifact_id=SECOND_ARTIFACT_ID) is None

    put_call = client.calls[0]
    assert put_call == (
        "put_object",
        {
            "Bucket": "private-artifacts",
            "Key": f"agentos/blobs/{ARTIFACT_ID}",
            "Body": b"first",
            "IfNoneMatch": "*",
        },
    )
    assert client.last_body is not None
    assert client.last_body.entered is True
    assert client.last_body.exited is True

    await store.delete(artifact_id=ARTIFACT_ID)
    await store.delete(artifact_id=ARTIFACT_ID)
    assert await store.read(artifact_id=ARTIFACT_ID) is None


@async_test
async def test_s3_blob_store_validates_before_calling_backend() -> None:
    from agentos.distributed.blobs.s3 import S3BlobStore

    client = FakeS3Client()
    store = S3BlobStore(client=client, bucket_name="private-artifacts")

    with pytest.raises(ArtifactValidationError, match="artifact id is invalid"):
        await store.read(artifact_id="../secret")
    with pytest.raises(ArtifactValidationError, match="artifact data must be bytes"):
        await store.put_if_absent(artifact_id=ARTIFACT_ID, data=bytearray(b"x"))

    assert client.calls == []


@async_test
async def test_s3_blob_store_rejects_key_injection_and_endpoint_credentials() -> None:
    from agentos.distributed.blobs.s3 import S3BlobStore

    client = FakeS3Client()
    session = FakeSession(FakeClientContext(client))

    with pytest.raises(ValueError, match="key_prefix is invalid"):
        S3BlobStore(
            client=client,
            bucket_name="private-artifacts",
            key_prefix="../secret",
        )
    with pytest.raises(ValueError, match="endpoint_url is invalid") as captured:
        await S3BlobStore.open(
            bucket_name="private-artifacts",
            endpoint_url="https://user:password@objects.internal.example",
            session=session,
        )

    assert "user" not in str(captured.value)
    assert "password" not in str(captured.value)
    assert session.calls == []


@async_test
async def test_s3_blob_store_redacts_backend_failure() -> None:
    from agentos.distributed.blobs.s3 import S3BlobStore

    client = FakeS3Client()
    client.failure = RuntimeError(
        "bucket=private-artifacts key=secret access_key=AKIA-DO-NOT-LEAK",
    )
    store = S3BlobStore(client=client, bucket_name="private-artifacts")

    with pytest.raises(DistributedBackendUnavailableError) as captured:
        await store.read(artifact_id=ARTIFACT_ID)

    assert str(captured.value) == "distributed backend is unavailable"
    assert captured.value.__cause__ is None


@async_test
async def test_s3_blob_store_closes_only_session_owned_client() -> None:
    from agentos.distributed.blobs.s3 import S3BlobStore

    client = FakeS3Client()
    context = FakeClientContext(client)
    session = FakeSession(context)
    store = await S3BlobStore.open(
        bucket_name="private-artifacts",
        key_prefix="agentos/blobs",
        endpoint_url="https://objects.internal.example",
        region_name="region-1",
        session=session,
    )

    assert context.entered is True
    assert session.calls == [
        (
            "s3",
            {
                "endpoint_url": "https://objects.internal.example",
                "region_name": "region-1",
            },
        ),
    ]

    await store.close()
    await store.close()
    assert context.exited is True
    with pytest.raises(DistributedStoreClosedError):
        await store.read(artifact_id=ARTIFACT_ID)

    injected = S3BlobStore(client=client, bucket_name="private-artifacts")
    await injected.close()
    assert context.exited is True


@async_test
async def test_s3_blob_store_close_failure_is_redacted_and_retryable() -> None:
    from agentos.distributed.blobs.s3 import S3BlobStore

    class FlakyClientContext(FakeClientContext):
        def __init__(self, client: FakeS3Client) -> None:
            super().__init__(client)
            self.exit_calls = 0

        async def __aexit__(self, *args: object) -> None:
            self.exit_calls += 1
            if self.exit_calls == 1:
                raise RuntimeError("secret close failure for private-artifacts")
            self.exited = True

    client = FakeS3Client()
    context = FlakyClientContext(client)
    store = await S3BlobStore.open(
        bucket_name="private-artifacts",
        session=FakeSession(context),
    )

    with pytest.raises(DistributedBackendUnavailableError) as captured:
        await store.close()
    assert str(captured.value) == "distributed backend is unavailable"
    assert captured.value.__cause__ is None

    await store.close()
    assert context.exit_calls == 2
    assert context.exited is True


@async_test
async def test_s3_blob_store_cancelled_close_can_be_retried() -> None:
    from agentos.distributed.blobs.s3 import S3BlobStore

    class BlockingClientContext(FakeClientContext):
        def __init__(self, client: FakeS3Client) -> None:
            super().__init__(client)
            self.exit_started = asyncio.Event()
            self.allow_exit = asyncio.Event()
            self.exit_calls = 0

        async def __aexit__(self, *args: object) -> None:
            self.exit_calls += 1
            self.exit_started.set()
            await self.allow_exit.wait()
            self.exited = True

    context = BlockingClientContext(FakeS3Client())
    store = await S3BlobStore.open(
        bucket_name="private-artifacts",
        session=FakeSession(context),
    )
    closing = asyncio.create_task(store.close())
    await context.exit_started.wait()

    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing

    context.allow_exit.set()
    await store.close()
    assert context.exit_calls == 2
    assert context.exited is True


@async_test
async def test_s3_blob_store_maps_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.distributed.blobs import s3

    def missing_dependency(name: str) -> object:
        assert name == "aioboto3"
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(s3, "import_module", missing_dependency)

    with pytest.raises(DistributedBackendUnavailableError) as captured:
        await s3.S3BlobStore.open(bucket_name="private-artifacts")

    assert str(captured.value) == "distributed backend is unavailable"
    assert captured.value.__cause__ is None


@async_test
async def test_s3_blob_store_closes_read_body_when_read_fails() -> None:
    from agentos.distributed.blobs.s3 import S3BlobStore

    class ReadFailureClient(FakeS3Client):
        async def get_object(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(("get_object", kwargs))
            self.last_body = FakeBody(
                b"",
                failure=RuntimeError("secret backend read failure"),
            )
            return {"Body": self.last_body}

    client = ReadFailureClient()
    store = S3BlobStore(client=client, bucket_name="private-artifacts")

    with pytest.raises(DistributedBackendUnavailableError):
        await store.read(artifact_id=ARTIFACT_ID)

    assert client.last_body is not None
    assert client.last_body.exited is True
