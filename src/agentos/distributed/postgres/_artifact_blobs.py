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
            created = await put_task
        except BaseException:
            raise cancellation from None
        if created:
            await cleanup_blob(blobs, artifact_id)
        raise


async def cleanup_blob(blobs: BlobStore, artifact_id: str) -> None:
    cleanup = asyncio.create_task(blobs.delete(artifact_id=artifact_id))
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await cleanup
        raise


__all__ = ["cleanup_blob", "put_upload_candidate"]
