import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_GENERATOR = PROJECT_ROOT / "scripts" / "generate_public_api_inventory.py"
PUBLIC_API_INVENTORY = PROJECT_ROOT / "docs" / "public-api-inventory.json"
PUBLIC_API_STABILITY = PROJECT_ROOT / "docs" / "public-api-stability.json"
API_STABILITY_DOC = PROJECT_ROOT / "docs" / "api-stability.md"
PHASE0_PLAN = (
    PROJECT_ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-07-11-agentos-phase0-baseline-remediation-implementation-plan.md"
)
_REMOVED_ASYNC_LOOP_NAME = "Async" + "QueryLoop"

_PHASE4_ROOT_STABLE_EXPORTS = frozenset(
    {"__version__", "Agent", "AgentBuilder", "AgentResult", "RunOptions"},
)
_RUNTIME_STABLE_BEFORE_SINGLE_ASYNC_CUTOVER = frozenset(
    {
        "Agent",
        _REMOVED_ASYNC_LOOP_NAME,
        "ContextLoaded",
        "DistributedWebRuntimeProfile",
        "DistributedWebSessionOperationsProfile",
        "FinalResult",
        "LocalRuntimeProfile",
        "PlanUpdated",
        "ProviderRequestBuilder",
        "QueryLoop",
        "RuntimeProfile",
        "SkillLoaded",
        "StatusUpdate",
        "WebRuntimeProfile",
    }
)
_RUNTIME_EXPERIMENTAL_BEFORE_SINGLE_ASYNC_CUTOVER = frozenset(
    {
        "AgentContinuationFailedEvent",
        "AgentEvent",
        "AgentInboxBackpressureEvent",
        "AgentResult",
        "AgentTaskCancelledEvent",
        "AgentTaskCompletedEvent",
        "AgentTaskDispatchedEvent",
        "AgentTaskFailedEvent",
        "AgentTaskLateResultReceivedEvent",
        "AssistantCompleted",
        "AssistantContentDelta",
        "AssistantMessageAppendedEvent",
        "AssistantThinkingDelta",
        "ChannelRuntimeProfile",
        "ChapterStartedEvent",
        "CompressedSegmentAppendedEvent",
        "CompressionCompletedEvent",
        "CompressionFailedEvent",
        "CompressionSkippedEvent",
        "ContextRenderedEvent",
        "DistributedAgentProfile",
        "DistributedRuntimeProfile",
        "DistributedTeamRuntimeProfile",
        "event_payload",
        "event_to_json",
        "event_to_sse",
        "event_type",
        "EventBus",
        "InheritedStateSetEvent",
        "MemoryContextSetEvent",
        "ProductionStatePlaneDeploymentProfile",
        "ProviderCircuitOpenError",
        "ProviderRequestBuiltEvent",
        "ProviderResponseReceivedEvent",
        "ProviderRetryEvent",
        "RecallContextFailedEvent",
        "RecallContextInjectedEvent",
        "RecallContextRequestedEvent",
        "RetryPolicy",
        "RunOptions",
        "RuntimeCompositionProfile",
        "SessionState",
        "SnapshotLoadedEvent",
        "SnapshotSavedEvent",
        "SubagentSpawnedEvent",
        "ToolCallRequestedEvent",
        "ToolExecutionCompletedEvent",
        "ToolExecutionStartedEvent",
        "ToolResultAppendedEvent",
        "ToolResultCappedEvent",
        "ToolStreamCompleted",
        "ToolStreamFailed",
        "ToolStreamStarted",
        "TurnCompletedEvent",
        "TurnFailedEvent",
        "TurnNoticeProvider",
        "TurnStartedEvent",
        "TurnState",
        "TurnStreamCancelled",
        "TurnStreamCompleted",
        "TurnStreamEvent",
        "TurnStreamFailed",
        "TurnStreamStarted",
        "UserMessageAppendedEvent",
        "WorkerProcessLifecycleDeploymentProfile",
        "WorkingStateSchemaDeclaredEvent",
        "WorkingStateSchemaExtendedEvent",
        "WorkingStateUpdatedEvent",
    }
)
_RUNTIME_STABLE_SINGLE_ASYNC_ADDITIONS = frozenset(
    {
        "AgentBusyError",
        "AgentResult",
        "AgentRunError",
        "AgentStream",
        "AgentStreamClosedError",
        "AgentStreamConsumerError",
        "AgentWaiting",
        "ContinuationUnavailableError",
        "iter_jsonl",
        "iter_sse",
        "LocalContinuationInput",
        "RunInput",
        "RunOptions",
        "RunOutcome",
        "RunProtocolError",
        "RunRequest",
        "TurnStreamWaiting",
        "UserTurnInput",
        "WaitingUnsupportedError",
        "WaitReason",
    }
)
_RUNTIME_STABLE_PHASE5_ADDITIONS = frozenset(
    {
        "DurableCommandReceipt",
        "DurableRunCommand",
    }
)
_RUNTIME_EXPERIMENTAL_TASK6_ADDITIONS = frozenset(
    {
        "SideEffectResolution",
        "SideEffectResolutionKind",
        "SideEffectResume",
        "side_effect_resolution_from_payload",
        "side_effect_resolution_to_payload",
    }
)
_DURABLE_STABLE_PHASE5_EXPORTS = frozenset(
    {
        "DurableRuntimeProfile",
        "SQLiteDurableStore",
    }
)
_SYNC_STABLE_EXPORTS = frozenset(
    {
        "run",
        "SyncAdapterEventLoopError",
        "SyncAdapterReentryError",
        "SyncAgent",
        "SyncAgentClosedError",
        "SyncAgentStream",
        "SyncStreamConsumerError",
    }
)


def _load_inventory_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_public_api_inventory",
        INVENTORY_GENERATOR,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inventory_generator = _load_inventory_generator()


def _load_public_api_inventory() -> dict[str, object]:
    return json.loads(PUBLIC_API_INVENTORY.read_text(encoding="utf-8"))


def _public_api_pytest_commands(text: str) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in text.splitlines()
        if "pytest" in line and "tests/architecture/test_public_api.py" in line
    )


def test_public_api_audit_commands_include_inventory_contract_suite() -> None:
    inventory_test = "tests/architecture/test_public_api_inventory.py"
    api_stability = API_STABILITY_DOC.read_text(encoding="utf-8")
    plan = PHASE0_PLAN.read_text(encoding="utf-8")
    task5 = plan.split("### Task 5:", 1)[1].split("### Task 6:", 1)[0]
    task7 = plan.split("### Task 7:", 1)[1]

    command_groups = {
        "api-stability": _public_api_pytest_commands(api_stability),
        "phase0-task5": _public_api_pytest_commands(task5),
        "phase0-task7": _public_api_pytest_commands(task7),
    }
    for owner, commands in command_groups.items():
        assert commands, owner
        assert all(inventory_test in command for command in commands), (
            owner,
            commands,
        )


def test_public_api_inventory_is_reproducible(tmp_path: Path) -> None:
    output = tmp_path / "public-api-inventory.json"
    subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "generate_public_api_inventory.py"),
            "--policy",
            str(PUBLIC_API_STABILITY),
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    assert json.loads(output.read_text(encoding="utf-8")) == (
        _load_public_api_inventory()
    )


def _inventory_ci_job(
    *,
    setup_python: str = "${{ matrix.python-version }}",
    install: str = "uv sync --python ${{ matrix.python-version }} --extra dev",
    generate: str = (
        "uv run --python ${{ matrix.python-version }} python "
        "scripts/generate_public_api_inventory.py "
        "--policy docs/public-api-stability.json "
        '--output "${RUNNER_TEMP}/public-api-inventory.json"'
    ),
    compare: str = (
        "git diff --exit-code --no-index -- "
        "docs/public-api-inventory.json "
        '"${RUNNER_TEMP}/public-api-inventory.json"'
    ),
) -> dict[str, object]:
    return {
        "strategy": {
            "matrix": {"python-version": ["3.11", "3.12", "3.13"]},
        },
        "steps": [
            {
                "uses": "actions/setup-python@v5",
                "with": {"python-version": setup_python},
            },
            {"name": "Install development dependencies", "run": install},
            {"name": "Generate public API inventory", "run": generate},
            {"name": "Verify public API inventory", "run": compare},
        ],
    }


def _public_api_inventory_ci_contract_violations(
    job: dict[str, object],
) -> tuple[str, ...]:
    matrix_expression = "${{ matrix.python-version }}"
    matrix_marker = "__MATRIX_PYTHON__"

    def command_argv(command: str) -> tuple[str, ...]:
        normalized = command.replace(matrix_expression, matrix_marker)
        return tuple(shlex.split(normalized, posix=True))

    steps = job.get("steps")
    if not isinstance(steps, list):
        return ("steps",)
    structured_steps = [step for step in steps if isinstance(step, dict)]
    violations: list[str] = []

    setup_steps = [
        step
        for step in structured_steps
        if step.get("uses") == "actions/setup-python@v5"
    ]
    if len(setup_steps) != 1:
        violations.append("setup-python-step")
    else:
        setup_options = setup_steps[0].get("with")
        if not isinstance(setup_options, dict) or (
            setup_options.get("python-version") != matrix_expression
        ):
            violations.append("setup-python-version")

    commands = {
        step.get("name"): step.get("run")
        for step in structured_steps
        if isinstance(step.get("name"), str)
        and isinstance(step.get("run"), str)
    }
    expected = {
        "Install development dependencies": (
            (
                "uv",
                "sync",
                "--python",
                matrix_marker,
                "--extra",
                "dev",
            ),
            "install-command",
        ),
        "Generate public API inventory": (
            (
                "uv",
                "run",
                "--python",
                matrix_marker,
                "python",
                "scripts/generate_public_api_inventory.py",
                "--policy",
                "docs/public-api-stability.json",
                "--output",
                "${RUNNER_TEMP}/public-api-inventory.json",
            ),
            "generator-command",
        ),
        "Verify public API inventory": (
            (
                "git",
                "diff",
                "--exit-code",
                "--no-index",
                "--",
                "docs/public-api-inventory.json",
                "${RUNNER_TEMP}/public-api-inventory.json",
            ),
            "compare-command",
        ),
    }
    for step_name, (expected_argv, violation) in expected.items():
        command = commands.get(step_name)
        if not isinstance(command, str) or command_argv(command) != expected_argv:
            violations.append(violation)
    return tuple(violations)


@pytest.mark.parametrize(
    ("job", "expected_violation"),
    [
        (
            _inventory_ci_job(generate="echo generate_public_api_inventory.py"),
            "generator-command",
        ),
        (
            _inventory_ci_job(compare="echo git diff --exit-code --no-index"),
            "compare-command",
        ),
        (_inventory_ci_job(setup_python="3.11"), "setup-python-version"),
        (_inventory_ci_job(install="uv sync --extra dev"), "install-command"),
        (
            _inventory_ci_job(
                generate=(
                    "uv run python scripts/generate_public_api_inventory.py "
                    "--policy docs/public-api-stability.json "
                    '--output "${RUNNER_TEMP}/public-api-inventory.json"'
                ),
            ),
            "generator-command",
        ),
        (
            _inventory_ci_job(
                generate=(
                    "uv run --python ${{ matrix.python-version }} python "
                    "scripts/generate_public_api_inventory.py "
                    "--policy docs/public-api-stability.json "
                    "--output generated.json"
                ),
            ),
            "generator-command",
        ),
        (
            _inventory_ci_job(
                compare=(
                    "git diff --exit-code --no-index -- other.json "
                    '"${RUNNER_TEMP}/public-api-inventory.json"'
                ),
            ),
            "compare-command",
        ),
        (
            _inventory_ci_job(
                compare=(
                    "git diff --exit-code --no-index -- "
                    "docs/public-api-inventory.json other-generated.json"
                ),
            ),
            "compare-command",
        ),
    ],
)
def test_public_api_inventory_ci_contract_rejects_mutations(
    job: dict[str, object],
    expected_violation: str,
) -> None:
    assert _public_api_inventory_ci_contract_violations(job) == (
        expected_violation,
    )


def test_public_api_inventory_ci_covers_supported_python_minors() -> None:
    workflow = yaml.safe_load(
        (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8",
        ),
    )
    job = workflow["jobs"]["public-api-inventory"]
    assert job["strategy"]["matrix"]["python-version"] == [
        "3.11",
        "3.12",
        "3.13",
    ]
    assert _public_api_inventory_ci_contract_violations(job) == ()


def test_public_api_stability_policy_contains_only_classifications() -> None:
    policy = json.loads(PUBLIC_API_STABILITY.read_text(encoding="utf-8"))
    assert set(policy) == {"schema", "schema_version", "modules"}
    assert policy["schema"] == "agentos.public_api_stability"
    assert policy["schema_version"] == 1
    for module_policy in policy["modules"].values():
        assert set(module_policy) == {"stable", "experimental"}
        assert set(module_policy["stable"]).isdisjoint(module_policy["experimental"])
    assert {
        "ContextLoaded",
        "FinalResult",
        "PlanUpdated",
        "SkillLoaded",
        "StatusUpdate",
    } <= set(policy["modules"]["agentos.runtime"]["stable"])
    assert '"signature"' not in PUBLIC_API_STABILITY.read_text(encoding="utf-8")


def test_phase5_policy_keeps_root_level1_and_adds_durable_api() -> None:
    policy = json.loads(PUBLIC_API_STABILITY.read_text(encoding="utf-8"))
    modules = policy["modules"]

    assert set(modules["agentos"]["stable"]) == _PHASE4_ROOT_STABLE_EXPORTS
    assert modules["agentos"]["experimental"] == []
    assert set(modules["agentos.runtime"]["stable"]) == (
        _RUNTIME_STABLE_BEFORE_SINGLE_ASYNC_CUTOVER
        - {_REMOVED_ASYNC_LOOP_NAME}
        | _RUNTIME_STABLE_SINGLE_ASYNC_ADDITIONS
        | _RUNTIME_STABLE_PHASE5_ADDITIONS
    )
    assert set(modules["agentos.runtime"]["experimental"]) == (
        _RUNTIME_EXPERIMENTAL_BEFORE_SINGLE_ASYNC_CUTOVER
        - {"AgentResult", "RunOptions"}
        | _RUNTIME_EXPERIMENTAL_TASK6_ADDITIONS
    )
    assert set(modules["agentos.durable"]["stable"]) == (
        _DURABLE_STABLE_PHASE5_EXPORTS
    )
    assert modules["agentos.durable"]["experimental"] == []
    assert set(modules["agentos.sync"]["stable"]) == _SYNC_STABLE_EXPORTS
    assert modules["agentos.sync"]["experimental"] == []


@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        (
            "(path: pathlib._local.Path) -> pathlib._local.Path",
            "(path: pathlib.Path) -> pathlib.Path",
        ),
        (
            "(scopes=frozenset({'task', 'session'}))",
            "(scopes=frozenset({'session', 'task'}))",
        ),
        (
            "(scopes=frozenset({'session', 'task'}))",
            "(scopes=frozenset({'session', 'task'}))",
        ),
    ],
)
def test_signature_normalization_is_cross_minor_deterministic(
    signature: str,
    expected: str,
) -> None:
    assert inventory_generator.normalize_signature(signature) == expected


def test_signature_normalization_preserves_private_type_identity() -> None:
    first = "(value: package._one.Widget)"
    second = "(value: package._two.Widget)"
    assert inventory_generator.normalize_signature(first) == first
    assert inventory_generator.normalize_signature(second) == second
    assert inventory_generator.normalize_signature(first) != (
        inventory_generator.normalize_signature(second)
    )


def test_signature_normalization_preserves_address_like_string_defaults() -> None:
    signature = "(marker: str = ' at 0xABCD')"
    assert inventory_generator.normalize_signature(signature) == signature


def test_signature_normalization_removes_only_object_repr_addresses() -> None:
    signature = (
        "(value: package._implementation.PublicType = "
        "<package._implementation.PublicType object at 0xABC123>)"
    )
    assert inventory_generator.normalize_signature(signature) == (
        "(value: package._implementation.PublicType = "
        "<package._implementation.PublicType object>)"
    )


@pytest.mark.parametrize("type_name", ["object", "Sentinel"])
def test_signature_normalization_handles_unqualified_object_repr(
    type_name: str,
) -> None:
    signature = f"(value=<{type_name} object at 0xABCD>)"
    assert inventory_generator.normalize_signature(signature) == (
        f"(value=<{type_name} object>)"
    )


@pytest.mark.parametrize(
    ("policy", "expected_error"),
    [
        (
            {"stable": ["PublicOne"], "experimental": []},
            "missing classifications: PublicTwo",
        ),
        (
            {
                "stable": ["PublicOne", "RemovedExport"],
                "experimental": ["PublicTwo"],
            },
            "deleted policy exports: RemovedExport",
        ),
        (
            {
                "stable": ["PublicOne", "PublicTwo"],
                "experimental": ["PublicTwo"],
            },
            "duplicate classifications: PublicTwo",
        ),
    ],
)
def test_public_api_policy_rejects_classification_drift(
    policy: dict[str, list[str]],
    expected_error: str,
) -> None:
    with pytest.raises(inventory_generator.PolicyError, match=expected_error):
        inventory_generator.classify_exports(
            "example.module",
            ("PublicOne", "PublicTwo"),
            policy,
        )


def test_public_api_policy_rejects_module_without_all() -> None:
    module = ModuleType("example.module")
    with pytest.raises(
        inventory_generator.PolicyError,
        match="example.module must define __all__",
    ):
        inventory_generator.public_export_names(module)
