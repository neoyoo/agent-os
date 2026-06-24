from __future__ import annotations

from collections.abc import Callable

from agentos.multi import TaskRecord, TaskRequest, TaskResult
from agentos.multi.task_store import TaskStore
from agentos.testing.contracts._checks import (
    check_equal,
    check_is,
    require_not_none,
)


def contract_record(
    task_id: str = "contract_task_1",
    *,
    parent_agent_id: str = "parent",
    target_agent_id: str = "expert",
    required_capabilities: tuple[str, ...] = ("architecture-review",),
    allowed_tool_names: tuple[str, ...] = ("read_file",),
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        mode="dispatch",
        parent_agent_id=parent_agent_id,
        target_agent_id=target_agent_id,
        request=TaskRequest(
            task_id=task_id,
            instruction="Review architecture.",
            required_capabilities=required_capabilities,
            allowed_tool_names=allowed_tool_names,
        ),
        status="queued",
        created_at=1.0,
        deadline_at=60.0,
    )


def contract_result(task_id: str, status: str = "completed") -> TaskResult:
    return TaskResult(
        task_id=task_id,
        status=status,  # type: ignore[arg-type]
        summary=f"{status} result",
    )


def run_task_store_contract(factory: Callable[[], TaskStore]) -> None:
    _assert_exact_claim_target_and_capability_fences(factory)
    _assert_lease_reclaim_and_terminal_write_fences(factory)
    _assert_cancel_and_result_consumption(factory)


def _assert_exact_claim_target_and_capability_fences(
    factory: Callable[[], TaskStore],
) -> None:
    store = factory()
    original = contract_record()
    store.create(original)

    wrong_target = store.claim_task(
        original.task_id,
        worker_id="worker_wrong_target",
        target_agent_id="other_expert",
        capabilities=("architecture-review",),
        lease_expires_at=20.0,
        now=2.0,
    )
    wrong_capability = store.claim_task(
        original.task_id,
        worker_id="worker_wrong_capability",
        target_agent_id="expert",
        capabilities=("read_file",),
        lease_expires_at=20.0,
        now=2.0,
    )
    claim = store.claim_task(
        original.task_id,
        worker_id="worker_ok",
        target_agent_id="expert",
        capabilities=("architecture-review",),
        lease_expires_at=20.0,
        now=2.0,
    )

    check_is(wrong_target, None, "claim_task must reject wrong target_agent_id")
    check_is(
        wrong_capability,
        None,
        "claim_task must reject workers without required capabilities",
    )
    claim = require_not_none(claim, "claim_task must allow a matching worker")
    check_equal(claim.task_id, original.task_id, "claim_task must return the task id")
    stored = require_not_none(
        store.get(original.task_id),
        "claimed task must remain readable",
    )
    check_equal(stored.status, "running", "claimed task must be running")
    check_equal(stored.worker_id, "worker_ok", "claimed task must record worker_id")
    check_equal(
        stored.request.allowed_tool_names,
        ("read_file",),
        "claim_task must preserve allowed tool names",
    )


def _assert_lease_reclaim_and_terminal_write_fences(
    factory: Callable[[], TaskStore],
) -> None:
    store = factory()
    store.create(contract_record("contract_task_lease"))
    first = store.claim_task(
        "contract_task_lease",
        worker_id="worker_a",
        target_agent_id="expert",
        capabilities=("architecture-review",),
        lease_expires_at=3.0,
        now=2.0,
    )
    second = store.claim_task(
        "contract_task_lease",
        worker_id="worker_b",
        target_agent_id="expert",
        capabilities=("architecture-review",),
        lease_expires_at=8.0,
        now=4.0,
    )

    require_not_none(first, "initial claim must succeed")
    second = require_not_none(second, "expired lease must be reclaimable")
    check_equal(second.attempt, 2, "lease reclaim must increment attempt")

    terminal_result = contract_result("contract_task_lease")
    check_is(
        store.mark_completed(
            "contract_task_lease",
            terminal_result,
            worker_id="worker_a",
            attempt=1,
            now=5.0,
        ),
        False,
        "stale worker/attempt must not write terminal result",
    )
    check_is(
        store.mark_completed(
            "contract_task_lease",
            terminal_result,
            worker_id="worker_b",
            attempt=2,
            now=6.0,
        ),
        True,
        "current worker/attempt must write terminal result",
    )
    stored = require_not_none(
        store.get("contract_task_lease"),
        "completed task must remain readable",
    )
    check_equal(stored.status, "completed", "completed task must be terminal")
    check_equal(stored.result, terminal_result, "completed task must store result")


def _assert_cancel_and_result_consumption(factory: Callable[[], TaskStore]) -> None:
    store = factory()
    store.create(
        contract_record(
            "contract_task_cancel",
            parent_agent_id="cancel_parent",
        ),
    )
    check_is(
        store.request_cancel("contract_task_cancel", now=3.0),
        True,
        "request_cancel must cancel queued task",
    )
    stored = require_not_none(
        store.get("contract_task_cancel"),
        "cancelled task must remain readable",
    )
    check_equal(stored.status, "cancelled", "cancelled task must be terminal")
    result = require_not_none(stored.result, "cancelled task must store result")
    check_equal(result.status, "cancelled", "cancelled result must be terminal")
    cancel_results = store.consume_results_for_agent("cancel_parent")
    check_equal(
        [result.task_id for result in cancel_results],
        ["contract_task_cancel"],
        "cancelled task result must be consumable by parent",
    )
    check_equal(
        store.consume_results_for_agent("cancel_parent"),
        [],
        "task results must be single-consumer",
    )

    store.create(contract_record("contract_task_result"))
    claim = store.claim_task(
        "contract_task_result",
        worker_id="worker_result",
        target_agent_id="expert",
        capabilities=("architecture-review",),
        lease_expires_at=20.0,
        now=4.0,
    )
    claim = require_not_none(claim, "result task claim must succeed")
    check_is(
        store.mark_completed(
            "contract_task_result",
            contract_result("contract_task_result"),
            worker_id=claim.worker_id,
            attempt=claim.attempt,
            now=5.0,
        ),
        True,
        "claimed task completion must succeed",
    )
    results = store.consume_results_for_agent("parent")
    check_equal(
        [result.task_id for result in results],
        ["contract_task_result"],
        "completed task result must be consumable by parent",
    )
    check_equal(
        store.consume_results_for_agent("parent"),
        [],
        "completed task results must be single-consumer",
    )


__all__ = [
    "contract_record",
    "contract_result",
    "run_task_store_contract",
]
