from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_MODULE = (
    PROJECT_ROOT / "src" / "agentos" / "distributed" / "postgres" / "artifacts.py"
)


def test_artifact_store_exposes_no_staging_cleanup_without_upload_leases() -> None:
    source = ARTIFACT_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ARTIFACT_MODULE))
    store = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PostgresArtifactStore"
    )
    operations = {
        node.name
        for node in store.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }

    assert operations == {"close", "upload", "list", "read", "delete"}
    assert "Automatic age-based cleanup is intentionally deferred" in source
    staging_sql = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "'staging'" in node.value
    ]
    assert len(staging_sql) == 2
    assert all("DELETE" not in query.upper() for query in staging_sql)
    assert all("created_at" not in query for query in staging_sql)


def test_excluded_document_recognition_capability_is_absent() -> None:
    forbidden = "".join(("o", "c", "r"))
    paths = [
        *(PROJECT_ROOT / "src").rglob("*.py"),
        *(PROJECT_ROOT / "tests").rglob("*.py"),
        PROJECT_ROOT / "pyproject.toml",
        *(PROJECT_ROOT / ".github" / "workflows").glob("*.yml"),
    ]
    violations = [
        str(path.relative_to(PROJECT_ROOT))
        for path in paths
        if forbidden in path.read_text(encoding="utf-8").casefold()
    ]

    assert violations == []
