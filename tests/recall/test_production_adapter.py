from types import SimpleNamespace

from agentos.recall import QdrantRecallIndex, SegmentRecallDocument


class FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed_text(self, text: str) -> list[float]:
        self.texts.append(text)
        return [float(len(text)), 1.0]


class FakeQdrantClient:
    def __init__(self) -> None:
        self.points: list[dict[str, object]] = []

    def upsert(self, collection_name: str, points: list[dict[str, object]]) -> None:
        self.points.extend(points)

    def search(
        self,
        collection_name: str,
        query_vector: list[float],
        query_filter: dict[str, object],
        limit: int,
    ) -> list[SimpleNamespace]:
        session_id = query_filter["must"][0]["match"]["value"]  # type: ignore[index]
        return [
            SimpleNamespace(payload=point["payload"], score=0.7)
            for point in self.points
            if point["payload"]["session_id"] == session_id  # type: ignore[index]
        ][:limit]

    def delete(self, collection_name: str, points_selector: dict[str, object]) -> None:
        session_id = points_selector["filter"]["must"][0]["match"]["value"]  # type: ignore[index]
        self.points = [
            point
            for point in self.points
            if point["payload"]["session_id"] != session_id  # type: ignore[index]
        ]


def test_qdrant_recall_index_indexes_searches_and_deletes_by_session() -> None:
    client = FakeQdrantClient()
    embeddings = FakeEmbeddingProvider()
    index = QdrantRecallIndex(
        url="http://unused",
        collection_name="agentos-recall",
        embedding_provider=embeddings,
        client=client,
    )
    document = SegmentRecallDocument(
        session_id="session_1",
        segment_id="seg_1",
        topic="project metadata",
        summary="project.name = agent-os",
        keywords=("pyproject",),
    )

    index.index_segment(document)
    candidates = index.search_segments("session_1", "pyproject", limit=1)
    index.delete_session("session_1")

    assert candidates[0].segment_id == "seg_1"
    assert candidates[0].score == 0.7
    assert document.to_text() in embeddings.texts
    assert "pyproject" in embeddings.texts
    assert client.points == []
