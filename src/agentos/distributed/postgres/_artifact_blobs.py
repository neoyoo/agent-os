import asyncio

from agentos.distributed.blobs.protocol import BlobStore


async def put_upload_candidate(
    blobs: BlobStore,
    *,
    artifact_id: str,
    data: bytes,
) -> bool:
    put_task = asyncio.create_task(
        blobs.put_if_absent(
            artifact_id=artifact_id,
            data=data,
        ),
    )
    try:
        return await asyncio.shield(put_task)
    except asyncio.CancelledError as cancellation:
        try:
            await put_task
        except BaseException:
            raise cancellation from None
        raise


__all__ = ["put_upload_candidate"]
