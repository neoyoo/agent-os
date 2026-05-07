from __future__ import annotations

import os

from qdrant_client import QdrantClient
from qdrant_client.http import models


DEFAULT_COLLECTION = "agentos_recall"


def main() -> None:
    url = os.environ.get("AGENTOS_QDRANT_URL", "http://localhost:6333")
    collection_name = os.environ.get(
        "AGENTOS_QDRANT_COLLECTION",
        DEFAULT_COLLECTION,
    )
    vector_size = int(os.environ["AGENTOS_QDRANT_VECTOR_SIZE"])
    distance = models.Distance[
        os.environ.get("AGENTOS_QDRANT_DISTANCE", "COSINE").upper()
    ]

    client = QdrantClient(url=url)
    collection_names = {
        collection.name
        for collection in client.get_collections().collections
    }
    if collection_name not in collection_names:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=distance,
            ),
        )

    for field_name in ["session_id", "segment_id"]:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


if __name__ == "__main__":
    main()
