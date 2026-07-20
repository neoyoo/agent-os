from pathlib import Path
import tomllib


def test_distributed_optional_dependencies_are_explicit() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]

    assert extras["distributed"] == [
        "psycopg[binary]>=3.1",
        "psycopg-pool>=3.2",
        "redis>=5.0",
    ]
    assert extras["distributed-artifacts"] == ["aioboto3>=13.0"]
