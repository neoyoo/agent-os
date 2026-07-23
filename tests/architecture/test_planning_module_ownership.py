import ast
import importlib
import importlib.util
import inspect
from pathlib import Path

import agentos.planning as planning
from agentos.planning.runtime import PlannerRuntime
from agentos.planning.scheduling import (
    PlanClaimedSchedulerTickReport,
    PlanClaimSweepReport,
    PlanSchedulerTickReport,
    PlannerSchedulablePlan,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_planner_runtime_and_scheduling_types_have_single_module_owners() -> None:
    assert PlannerRuntime.__module__ == "agentos.planning.runtime"
    assert PlanSchedulerTickReport.__module__ == "agentos.planning.scheduling_reports"
    assert (
        PlanClaimedSchedulerTickReport.__module__
        == "agentos.planning.scheduling_reports"
    )
    assert PlannerSchedulablePlan.__module__ == "agentos.planning.scheduling_reports"
    assert PlanClaimSweepReport.__module__ == "agentos.planning.scheduling_reports"

    assert planning.PlannerRuntime is PlannerRuntime


def test_planner_runtime_retains_canonical_scheduler_api() -> None:
    runtime_api = {
        name
        for name, member in inspect.getmembers(PlannerRuntime, inspect.isfunction)
        if not name.startswith("_")
    }

    assert {
        "schedulable_plans",
        "claim_schedulable_plans",
        "sweep_expired_claims",
        "scheduler_tick",
        "claimed_scheduler_tick",
    }.issubset(runtime_api)


def test_planning_package_does_not_import_multi_or_infrastructure_adapters() -> None:
    forbidden_prefixes = (
        "agentos.multi",
        "agentos.persistence",
    )

    for path in (PROJECT_ROOT / "src" / "agentos" / "planning").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.append(node.module)
        assert not any(
            module.startswith(forbidden_prefixes)
            for module in imported
        ), f"{path.name} imports a forbidden dependency: {imported}"


def test_planner_legacy_module_and_multi_re_exports_are_removed() -> None:
    multi = importlib.import_module("agentos.multi")

    legacy_module = "agentos.multi" + ".planner"
    assert importlib.util.find_spec(legacy_module) is None
    assert set(multi.__all__).isdisjoint(planning.__all__)
    assert not hasattr(multi, "PostgresPlanStore")
    assert not hasattr(multi, "PostgresPlanClaimStore")


def test_plan_codec_has_one_planning_owner() -> None:
    serializers = importlib.import_module("agentos.planning.serializers")
    multi_serializers = importlib.import_module("agentos.multi.serializers")
    codec_names = {
        "evidence_handle_from_dict",
        "evidence_handle_to_dict",
        "plan_assignment_from_dict",
        "plan_assignment_to_dict",
        "plan_state_from_dict",
        "plan_state_to_dict",
        "plan_step_from_dict",
        "plan_step_to_dict",
    }

    assert codec_names <= set(serializers.__all__)
    assert all(getattr(serializers, name).__module__ == serializers.__name__ for name in codec_names)
    assert all(not hasattr(multi_serializers, name) for name in codec_names)


def test_planner_exports_exist_only_on_their_real_owners() -> None:
    agentos = importlib.import_module("agentos")

    for name in (
        "CompareAndSavePlanStore",
        "InMemoryPlanStore",
        "PlanConflictError",
        "PlanStoreRecord",
    ):
        assert hasattr(planning, name)
        assert not hasattr(agentos, name)
    assert not hasattr(agentos, "PostgresPlanStore")
    assert importlib.util.find_spec("agentos.multi.postgres_plan") is None
