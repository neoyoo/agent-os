from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = PROJECT_ROOT / "scripts" / "generate_module_size_baseline.py"
BASELINE = (
    PROJECT_ROOT / "docs" / "governance" / "agentos-module-size-baseline.json"
)
LEGACY_CONTEXT_EXAMPLE = (
    PROJECT_ROOT / "docs" / "design" / "llm-context-only-example.md"
)
SDK_ARCHITECTURE = PROJECT_ROOT / "docs" / "design" / "sdk-architecture.md"
ACTIVE_ARCHITECTURE_SPEC = (
    "docs/superpowers/specs/"
    "2026-07-10-agentos-next-generation-sdk-architecture-design.md"
)
ACTIVE_CONTEXT_SPEC = (
    "docs/superpowers/specs/"
    "2026-07-10-agentos-context-protocol-v1-design.md"
)


def _load_generator() -> ModuleType:
    assert GENERATOR.is_file(), f"missing generator: {GENERATOR}"
    spec = importlib.util.spec_from_file_location(
        "generate_module_size_baseline",
        GENERATOR,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_python_file(path: Path, lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("pass\n" * lines, encoding="utf-8")


def _run_generator(root: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--root",
            str(root),
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _assert_validation_failure(
    result: subprocess.CompletedProcess[str],
    expected_message: str,
) -> None:
    assert result.returncode == 2
    assert expected_message in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_scan_governed_modules_applies_threshold_gates(tmp_path: Path) -> None:
    package_root = tmp_path / "src" / "agentos"
    _write_python_file(package_root / "below.py", 299)
    _write_python_file(package_root / "review.py", 300)
    _write_python_file(package_root / "nested" / "split.py", 500)
    _write_python_file(package_root / "blocked.py", 800)

    modules = _load_generator().scan_governed_modules(package_root)

    assert modules == [
        {
            "path": "src/agentos/blocked.py",
            "lines": 800,
            "gate": "no_new_responsibility",
        },
        {
            "path": "src/agentos/nested/split.py",
            "lines": 500,
            "gate": "split_or_exception",
        },
        {
            "path": "src/agentos/review.py",
            "lines": 300,
            "gate": "responsibility_review",
        },
    ]


def test_module_size_baseline_has_governed_thresholds() -> None:
    assert BASELINE.is_file(), f"missing baseline: {BASELINE}"
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))

    assert payload["schema"] == "agentos.module_size_baseline"
    assert payload["schema_version"] == 1
    assert payload["thresholds"] == {
        "responsibility_review": 300,
        "split_or_exception": 500,
        "no_new_responsibility": 800,
    }
    assert payload["modules"] == sorted(
        payload["modules"],
        key=lambda item: (-item["lines"], item["path"]),
    )
    assert all(item["lines"] >= 300 for item in payload["modules"])


def test_module_size_baseline_matches_current_source_tree() -> None:
    assert BASELINE.is_file(), f"missing baseline: {BASELINE}"
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    modules = _load_generator().scan_governed_modules(
        PROJECT_ROOT / "src" / "agentos",
    )

    assert payload["modules"] == modules


def test_unified_query_loop_cutover_respects_module_size_targets() -> None:
    maximum_lines = {
        "src/agentos/builder.py": 287,
        "src/agentos/runtime/query_loop.py": 499,
        "src/agentos/runtime/agent.py": 249,
        "src/agentos/runtime/agent_stream.py": 249,
        "src/agentos/runtime/provider_attempt.py": 249,
        "src/agentos/capabilities/skills.py": 660,
        "src/agentos/multi/coordinator.py": 720,
        "src/agentos/observability/instrumented.py": 1273,
        "src/agentos/providers/openai_compatible.py": 891,
        "src/agentos/examples/small_openai_agent.py": 445,
    }

    actual_lines = {
        path: len((PROJECT_ROOT / path).read_text(encoding="utf-8").splitlines())
        for path in maximum_lines
    }

    assert actual_lines == {
        path: min(actual_lines[path], limit)
        for path, limit in maximum_lines.items()
    }


def test_module_size_generator_writes_deterministic_json(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    command = [
        sys.executable,
        str(GENERATOR),
        "--root",
        str(PROJECT_ROOT / "src" / "agentos"),
        "--output",
    ]

    subprocess.run([*command, str(first)], cwd=PROJECT_ROOT, check=True)
    subprocess.run([*command, str(second)], cwd=PROJECT_ROOT, check=True)

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"\n")
    assert first.read_bytes() == BASELINE.read_bytes()


def test_module_size_generator_rejects_missing_root_without_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "missing"
    output = tmp_path / "baseline.json"

    result = _run_generator(root, output)

    _assert_validation_failure(result, "source root does not exist")
    assert not output.exists()


def test_module_size_generator_rejects_file_root_without_overwriting_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "module.py"
    root.write_text("pass\n", encoding="utf-8")
    output = tmp_path / "baseline.json"
    output.write_text("sentinel\n", encoding="utf-8")

    result = _run_generator(root, output)

    _assert_validation_failure(result, "source root is not a directory")
    assert output.read_text(encoding="utf-8") == "sentinel\n"


def test_module_size_generator_rejects_root_without_python_modules(
    tmp_path: Path,
) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    (root / "README.md").write_text("no Python modules\n", encoding="utf-8")
    output = tmp_path / "baseline.json"

    result = _run_generator(root, output)

    _assert_validation_failure(result, "source root contains no Python modules")
    assert not output.exists()


def test_legacy_context_example_is_explicitly_superseded() -> None:
    content = LEGACY_CONTEXT_EXAMPLE.read_text(encoding="utf-8")

    assert "status: superseded" in content
    assert "superseded_by:" in content
    assert ACTIVE_CONTEXT_SPEC in content
    assert "仅作为早期设计输入保留" in content
    for boundary in (
        "SystemEnvelope",
        "ContextSnapshot",
        "Slot Registry",
        "Provider 角色映射",
    ):
        assert boundary in content


def test_active_governance_uses_approved_specs_as_authority() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert ACTIVE_ARCHITECTURE_SPEC in agents
    assert ACTIVE_CONTEXT_SPEC in agents
    assert "## Historical Design Inputs" in agents
    assert "context example is the golden target" not in agents
    assert "system: rendered context" not in agents
    assert "system: SystemEnvelope" in agents
    assert "messages: ContextSnapshot + active ProviderInputItem" in agents


def test_sdk_architecture_points_to_approved_specs() -> None:
    architecture = SDK_ARCHITECTURE.read_text(encoding="utf-8")

    assert ACTIVE_ARCHITECTURE_SPEC in architecture
    assert ACTIVE_CONTEXT_SPEC in architecture
    assert "system: rendered context" not in architecture
    assert "system = ContextRuntime.render()" not in architecture
    assert "messages = MessageRuntime.materialize_active()" not in architecture
    assert "system = SystemEnvelope" in architecture
    assert "messages = ContextSnapshot + active ProviderInputItem" in architecture
    assert (
        "Runtime Contract\nCapability Plane\nContext Management Rules"
        not in architecture
    )
