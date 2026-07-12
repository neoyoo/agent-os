import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_GENERATOR = PROJECT_ROOT / "scripts" / "generate_public_api_inventory.py"
PUBLIC_API_STABILITY = PROJECT_ROOT / "docs" / "public-api-stability.json"
PUBLIC_API_INVENTORY = PROJECT_ROOT / "docs" / "public-api-inventory.json"


def _run_cli(
    policy: Path,
    output: Path,
    *,
    python_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if python_path is not None:
        current = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(python_path)
        if current:
            env["PYTHONPATH"] += os.pathsep + current
    return subprocess.run(
        [
            sys.executable,
            str(INVENTORY_GENERATOR),
            "--policy",
            str(policy),
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _valid_policy(modules: object) -> dict[str, object]:
    return {
        "schema": "agentos.public_api_stability",
        "schema_version": 1,
        "modules": modules,
    }


def _write_policy(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _assert_controlled_failure(
    completed: subprocess.CompletedProcess[str],
    expected: str,
) -> None:
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.count("\n") == 1
    assert expected in completed.stderr
    assert "Traceback" not in completed.stderr


def test_inventory_cli_generates_current_inventory(tmp_path: Path) -> None:
    output = tmp_path / "inventory.json"

    completed = _run_cli(PUBLIC_API_STABILITY, output)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(
        PUBLIC_API_INVENTORY.read_text(encoding="utf-8"),
    )
    generated = output.read_bytes()
    assert b"\r\n" not in generated
    assert generated.endswith(b"\n")


def test_inventory_cli_rejects_missing_policy_without_creating_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "inventory.json"

    completed = _run_cli(tmp_path / "missing.json", output)

    _assert_controlled_failure(completed, "unable to read policy")
    assert not output.exists()


def test_inventory_cli_rejects_invalid_utf8_without_overwriting_output(
    tmp_path: Path,
) -> None:
    policy = tmp_path / "policy.json"
    policy.write_bytes(b"\xff")
    output = tmp_path / "inventory.json"
    output.write_text("unchanged", encoding="utf-8")

    completed = _run_cli(policy, output)

    _assert_controlled_failure(completed, "policy is not valid UTF-8")
    assert output.read_text(encoding="utf-8") == "unchanged"


def test_inventory_cli_rejects_invalid_json_without_overwriting_output(
    tmp_path: Path,
) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text("not-json", encoding="utf-8")
    output = tmp_path / "inventory.json"
    output.write_text("unchanged", encoding="utf-8")

    completed = _run_cli(policy, output)

    _assert_controlled_failure(completed, "policy is not valid JSON")
    assert output.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ([], "policy must be a JSON object"),
        (
            {"schema": "agentos.public_api_stability", "schema_version": 1},
            "policy must contain only schema, schema_version, and modules",
        ),
        (
            {"schema_version": 1, "modules": {}},
            "policy must contain only schema, schema_version, and modules",
        ),
        (
            {"schema": "wrong", "schema_version": 1, "modules": {}},
            "unsupported public API stability policy schema",
        ),
        (
            {
                "schema": "agentos.public_api_stability",
                "schema_version": 2,
                "modules": {},
            },
            "unsupported public API stability policy schema version",
        ),
        (
            {
                "schema": "agentos.public_api_stability",
                "modules": {},
            },
            "policy must contain only schema, schema_version, and modules",
        ),
        (_valid_policy([]), "policy modules must be a non-empty object"),
        (_valid_policy({"": {"stable": [], "experimental": []}}), "module name"),
        (_valid_policy({"example.module": []}), "module policy must be an object"),
        (
            _valid_policy(
                {"example.module": {"stable": "Public", "experimental": []}},
            ),
            "stable classification must be a list",
        ),
        (
            _valid_policy(
                {"example.module": {"stable": [], "experimental": "Public"}},
            ),
            "experimental classification must be a list",
        ),
        (
            _valid_policy(
                {"example.module": {"stable": [1], "experimental": []}},
            ),
            "stable classification entries must be non-empty strings",
        ),
        (
            _valid_policy(
                {"example.module": {"stable": [], "experimental": [""]}},
            ),
            "experimental classification entries must be non-empty strings",
        ),
    ],
)
def test_inventory_cli_rejects_malformed_policy_schema(
    tmp_path: Path,
    payload: object,
    expected: str,
) -> None:
    policy = tmp_path / "policy.json"
    _write_policy(policy, payload)
    output = tmp_path / "inventory.json"
    output.write_text("unchanged", encoding="utf-8")

    completed = _run_cli(policy, output)

    _assert_controlled_failure(completed, expected)
    assert output.read_text(encoding="utf-8") == "unchanged"


def _write_public_module(path: Path, name: str, source: str) -> str:
    (path / f"{name}.py").write_text(source, encoding="utf-8")
    return name


@pytest.mark.parametrize(
    ("module_source", "module_policy", "expected"),
    [
        (
            '__all__ = ["PublicOne", "PublicTwo"]\nPublicOne = 1\nPublicTwo = 2\n',
            {"stable": ["PublicOne"], "experimental": []},
            "missing classifications: PublicTwo",
        ),
        (
            '__all__ = ["PublicOne"]\nPublicOne = 1\n',
            {"stable": ["PublicOne", "Removed"], "experimental": []},
            "deleted policy exports: Removed",
        ),
        (
            '__all__ = ["PublicOne"]\nPublicOne = 1\n',
            {"stable": ["PublicOne"], "experimental": ["PublicOne"]},
            "duplicate classifications: PublicOne",
        ),
        (
            "PublicOne = 1\n",
            {"stable": ["PublicOne"], "experimental": []},
            "must define __all__",
        ),
    ],
)
def test_inventory_cli_reports_policy_drift_without_overwriting_output(
    tmp_path: Path,
    module_source: str,
    module_policy: dict[str, list[str]],
    expected: str,
) -> None:
    module_name = _write_public_module(tmp_path, "inventory_fixture", module_source)
    policy = tmp_path / "policy.json"
    _write_policy(policy, _valid_policy({module_name: module_policy}))
    output = tmp_path / "inventory.json"
    output.write_text("unchanged", encoding="utf-8")

    completed = _run_cli(policy, output, python_path=tmp_path)

    _assert_controlled_failure(completed, expected)
    assert output.read_text(encoding="utf-8") == "unchanged"


def test_inventory_cli_reports_output_io_error_without_traceback(
    tmp_path: Path,
) -> None:
    output = tmp_path / "missing" / "inventory.json"

    completed = _run_cli(PUBLIC_API_STABILITY, output)

    _assert_controlled_failure(completed, "unable to write inventory")
    assert not output.exists()


def test_inventory_cli_does_not_hide_unexpected_module_execution_errors(
    tmp_path: Path,
) -> None:
    module_name = _write_public_module(
        tmp_path,
        "broken_inventory_fixture",
        'raise RuntimeError("module execution failed")\n',
    )
    policy = tmp_path / "policy.json"
    _write_policy(
        policy,
        _valid_policy(
            {module_name: {"stable": [], "experimental": []}},
        ),
    )

    completed = _run_cli(policy, tmp_path / "inventory.json", python_path=tmp_path)

    assert completed.returncode == 1
    assert "Traceback" in completed.stderr
    assert "RuntimeError: module execution failed" in completed.stderr
