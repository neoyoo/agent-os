import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

from agentos.builder import AgentBuilder
from agentos.providers import FakeProvider
from agentos.runtime import AgentResult


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INFRASTRUCTURE_DISTRIBUTIONS = frozenset(
    {"psycopg", "psycopg-pool", "qdrant-client", "redis"}
)


def _requirement_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9_.-]*", requirement)
    assert match is not None, f"invalid project dependency: {requirement}"
    return match.group(0).lower().replace("_", "-")


def test_base_install_excludes_infrastructure_dependencies() -> None:
    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    base_dependencies = {
        _requirement_name(requirement)
        for requirement in project.get("dependencies", ())
    }

    assert base_dependencies.isdisjoint(INFRASTRUCTURE_DISTRIBUTIONS)


def test_import_agentos_does_not_preload_infrastructure_adapters() -> None:
    source_root = PROJECT_ROOT / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(source_root), environment.get("PYTHONPATH", "")))
    )
    script = "\n".join(
        (
            "import json",
            "import sys",
            "import agentos",
            "tokens = ('redis', 'postgres', 'qdrant')",
            "adapters = sorted(name for name in sys.modules if name.startswith('agentos.') and any(token in name for token in tokens))",
            "print(json.dumps(adapters))",
            "print(json.dumps(sorted(set(('redis', 'psycopg', 'qdrant_client')) & set(sys.modules))))",
        )
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    loaded_adapters, loaded_clients = (
        json.loads(line) for line in completed.stdout.splitlines()
    )
    assert loaded_adapters == []
    assert loaded_clients == []


def test_fake_provider_runs_level1_agent_without_external_services() -> None:
    agent = AgentBuilder().provider(FakeProvider(["local ok"])).build(
        session_id="session_packaging_smoke"
    )

    result = asyncio.run(agent.run("hello"))

    assert isinstance(result, AgentResult)
    assert result.content == "local ok"
