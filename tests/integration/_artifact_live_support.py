from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import os

import pytest


@dataclass(frozen=True, slots=True)
class ArtifactLiveSettings:
    postgres_dsn: str
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    region: str


def artifact_live_settings() -> ArtifactLiveSettings:
    if os.environ.get("AGENTOS_RUN_INTEGRATION") != "1":
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 to run live integration tests")
    names = (
        "AGENTOS_TEST_POSTGRES_DSN",
        "AGENTOS_TEST_S3_ENDPOINT_URL",
        "AGENTOS_TEST_S3_ACCESS_KEY_ID",
        "AGENTOS_TEST_S3_SECRET_ACCESS_KEY",
        "AGENTOS_TEST_S3_BUCKET",
        "AGENTOS_TEST_S3_REGION",
    )
    values = {name: os.environ.get(name) for name in names}
    if any(not value for value in values.values()):
        pytest.skip("set PostgreSQL and S3 live integration settings")
    return ArtifactLiveSettings(*(str(values[name]) for name in names))


def s3_session(settings: ArtifactLiveSettings) -> object:
    aioboto3 = import_module("aioboto3")
    return aioboto3.Session(
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        region_name=settings.region,
    )


async def create_bucket(
    settings: ArtifactLiveSettings,
    session: object,
    bucket: str,
) -> None:
    async with session.client(  # type: ignore[attr-defined]
        "s3",
        endpoint_url=settings.endpoint_url,
        region_name=settings.region,
    ) as client:
        await client.create_bucket(Bucket=bucket)


async def cleanup_bucket(
    settings: ArtifactLiveSettings,
    session: object,
    bucket: str,
) -> None:
    async with session.client(  # type: ignore[attr-defined]
        "s3",
        endpoint_url=settings.endpoint_url,
        region_name=settings.region,
    ) as client:
        response = await client.list_objects_v2(Bucket=bucket)
        objects = tuple(response.get("Contents", ()))
        if objects:
            await client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": item["Key"]} for item in objects]},
            )
        await client.delete_bucket(Bucket=bucket)
