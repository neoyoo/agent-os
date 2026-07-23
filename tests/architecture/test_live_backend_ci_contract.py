from __future__ import annotations

from pathlib import Path
import shlex
import tomllib

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]

_TEST_ENV = {
    "AGENTOS_RUN_INTEGRATION": "1",
    "AGENTOS_TEST_POSTGRES_DSN": (
        "postgresql://agentos:agentos@localhost:5432/agentos_test"
    ),
    "AGENTOS_TEST_REDIS_URL": "redis://localhost:6379/0",
    "AGENTOS_TEST_S3_ENDPOINT_URL": "http://localhost:4566",
    "AGENTOS_TEST_S3_ACCESS_KEY_ID": "agentos",
    "AGENTOS_TEST_S3_SECRET_ACCESS_KEY": "agentos-test-secret",
    "AGENTOS_TEST_S3_BUCKET": "agentos-test",
    "AGENTOS_TEST_S3_REGION": "us-east-1",
}


def _yaml(path: str) -> dict[str, object]:
    loaded = yaml.safe_load((PROJECT_ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _step(job: dict[str, object], name: str) -> dict[str, object]:
    steps = job["steps"]
    assert isinstance(steps, list)
    for step in steps:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    raise AssertionError(f"missing CI step: {name}")


def test_ci_runs_unit_and_live_suites_against_three_backends() -> None:
    workflow = _yaml(".github/workflows/ci.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["test"]
    assert isinstance(job, dict)

    services = job["services"]
    assert isinstance(services, dict)
    assert {"postgres", "redis", "s3"} <= set(services)
    assert str(services["postgres"]["image"]).startswith("postgres:16")
    assert str(services["redis"]["image"]).startswith("redis:7")
    assert services["s3"]["image"] == "localstack/localstack:3.8.1"
    for service in services.values():
        assert "--health-cmd" in str(service["options"])

    env = job["env"]
    assert isinstance(env, dict)
    assert {key: env.get(key) for key in _TEST_ENV} == _TEST_ENV
    install = shlex.split(str(_step(job, "Install")["run"]))
    extras = {
        install[index + 1]
        for index, token in enumerate(install[:-1])
        if token == "--extra"
    }
    assert {
        "distributed",
        "distributed-artifacts",
        "durable",
        "security",
    } <= extras
    assert _step(job, "Phase 6 scope exclusions")["run"] == (
        "uv run pytest tests/architecture/test_phase6_scope_exclusions.py -q"
    )
    assert _step(job, "Unit tests")["run"] == (
        'uv run pytest -m "not integration" -q'
    )
    integration = _step(job, "Integration tests")
    assert "if" not in integration
    assert integration["run"] == "uv run pytest -m integration -q"
    assert integration["env"] == {
        "AGENTOS_TEST_POSTGRES_CONTAINER": "${{ job.services.postgres.id }}",
        "AGENTOS_TEST_REDIS_CONTAINER": "${{ job.services.redis.id }}",
    }


def test_compose_provides_matching_healthy_backends() -> None:
    compose = _yaml("docker-compose.test.yml")
    services = compose["services"]
    assert isinstance(services, dict)
    assert {"postgres", "redis", "s3"} <= set(services)
    assert str(services["postgres"]["image"]).startswith("postgres:16")
    assert str(services["redis"]["image"]).startswith("redis:7")
    assert services["s3"]["image"] == "localstack/localstack:3.8.1"
    assert services["s3"]["environment"]["SERVICES"] == "s3"
    for service in services.values():
        healthcheck = service.get("healthcheck")
        assert isinstance(healthcheck, dict)
        assert healthcheck.get("test")


def test_integration_marker_names_all_live_backends() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    markers = project["tool"]["pytest"]["ini_options"]["markers"]
    integration_markers = [
        marker for marker in markers if marker.startswith("integration:")
    ]
    assert integration_markers == [
        (
            "integration: live PostgreSQL/Redis/S3-compatible tests that require "
            "docker-compose.test.yml"
        ),
    ]
