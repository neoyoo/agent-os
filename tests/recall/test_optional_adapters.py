import sys

import pytest


def test_qdrant_adapter_reports_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.recall.qdrant_index import QdrantRecallIndex

    monkeypatch.setitem(sys.modules, "qdrant_client", None)

    with pytest.raises(RuntimeError, match=r"agentos\[qdrant\]"):
        QdrantRecallIndex(
            url="http://localhost:6333",
            collection_name="agentos-recall",
            embedding_provider=object(),
        )
