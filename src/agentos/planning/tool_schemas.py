from __future__ import annotations

from agentos.planning.models import EVIDENCE_KINDS, PLAN_STATUSES

PlannerToolDefinition = tuple[str, str, dict[str, object]]


def planner_tool_definitions() -> tuple[PlannerToolDefinition, ...]:
    """按 Provider 暴露顺序构造 Planner Tool 定义。"""

    return (
        (
            "plan_create",
            "Create a structured execution plan.",
            _plan_create_parameters(),
        ),
        (
            "plan_gate_decomposition_proposal",
            (
                "Parse and gate a raw LLM decomposition proposal without "
                "creating a plan."
            ),
            _plan_gate_decomposition_proposal_parameters(),
        ),
        (
            "plan_create_from_decomposition",
            "Create a plan from a structured decomposition.",
            _plan_create_from_decomposition_parameters(),
        ),
        (
            "plan_add_step",
            "Add a step to an existing execution plan.",
            _plan_add_step_parameters(),
        ),
        (
            "plan_assign_step",
            "Assign a plan step to a subagent template.",
            _plan_assign_step_parameters(),
        ),
        (
            "plan_ready_steps",
            "Return pending plan steps whose dependencies are complete.",
            _plan_id_parameters(),
        ),
        (
            "plan_schedulable_plans",
            (
                "Return owned plans with dependency-ready or due-retry "
                "work for deployment-owned schedulers."
            ),
            _plan_schedulable_plans_parameters(),
        ),
        (
            "plan_claim_schedulable_plans",
            (
                "Claim owned schedulable plans for this scheduler worker "
                "through the configured claim store."
            ),
            _plan_claim_schedulable_plans_parameters(),
        ),
        (
            "plan_claimed_scheduler_tick",
            (
                "Claim owned schedulable plans and run one scheduler tick "
                "only for plans claimed by this worker."
            ),
            _plan_claimed_scheduler_tick_parameters(),
        ),
        (
            "plan_dispatch_ready_steps",
            "Dispatch dependency-ready plan steps to subagent templates.",
            _plan_dispatch_ready_steps_parameters(),
        ),
        (
            "plan_scheduler_tick",
            (
                "Run one planner scheduler pass: reset due retries and "
                "dispatch dependency-ready steps."
            ),
            _plan_scheduler_tick_parameters(),
        ),
        (
            "plan_fail_step",
            "Record a failed plan step and its retry metadata.",
            _plan_fail_step_parameters(),
        ),
        (
            "plan_retryable_steps",
            "Return failed plan steps whose retry delay has elapsed.",
            _plan_id_parameters(),
        ),
        (
            "plan_retry_step",
            "Move a retryable failed plan step back to pending.",
            _plan_step_parameters(),
        ),
        (
            "plan_record_evidence",
            "Record evidence and optionally attach it to plan steps.",
            _plan_record_evidence_parameters(),
        ),
        (
            "plan_complete_step",
            "Mark a plan step completed and attach evidence handles.",
            _plan_complete_step_parameters(),
        ),
        (
            "plan_status",
            "Return one plan or all plans owned by this agent.",
            {
                "type": "object",
                "properties": {"plan_id": {"type": "string"}},
            },
        ),
    )


def _plan_create_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "objective": {"type": "string"},
            "plan_id": {"type": "string"},
        },
        "required": ["objective"],
    }


def _step_properties() -> dict[str, object]:
    return {
        "instruction": {"type": "string"},
        "step_id": {"type": "string"},
        "required_capabilities": {
            "type": "array",
            "items": {"type": "string"},
        },
        "template_id": {"type": "string"},
        "depends_on": {"type": "array", "items": {"type": "string"}},
    }


def _plan_create_from_decomposition_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "objective": {"type": "string"},
            "plan_id": {"type": "string"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": _step_properties(),
                    "required": ["instruction"],
                },
            },
        },
        "required": ["objective", "steps"],
    }


def _plan_gate_decomposition_proposal_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "proposal": {
                "type": "object",
                "properties": {
                    "objective": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": _step_properties(),
                            "required": ["instruction"],
                        },
                    },
                },
                "required": ["objective", "steps"],
            },
            "policy": {
                "type": "object",
                "properties": {
                    "max_steps": {"type": "integer", "minimum": 1},
                    "require_template": {"type": "boolean"},
                    "require_approval": {"type": "boolean"},
                    "approved": {"type": "boolean"},
                    "allowed_template_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
            "metadata": {"type": "object"},
        },
        "required": ["proposal"],
    }


def _plan_add_step_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "instruction": {"type": "string"},
            "required_capabilities": {
                "type": "array",
                "items": {"type": "string"},
            },
            "template_id": {"type": "string"},
        },
        "required": ["plan_id", "instruction"],
    }


def _plan_assign_step_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "step_id": {"type": "string"},
            "template_id": {"type": "string"},
        },
        "required": ["plan_id", "step_id", "template_id"],
    }


def _plan_id_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {"plan_id": {"type": "string"}},
        "required": ["plan_id"],
    }


def _statuses_property() -> dict[str, object]:
    return {
        "type": "array",
        "items": {"type": "string", "enum": list(PLAN_STATUSES)},
    }


def _plan_schedulable_plans_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "statuses": _statuses_property(),
            "limit": {"type": "integer", "minimum": 1},
        },
    }


def _plan_claim_schedulable_plans_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "worker_id": {"type": "string"},
            "lease_seconds": {"type": "number", "exclusiveMinimum": 0},
            "statuses": _statuses_property(),
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["worker_id", "lease_seconds"],
    }


def _plan_claimed_scheduler_tick_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "worker_id": {"type": "string"},
            "lease_seconds": {"type": "number", "exclusiveMinimum": 0},
            "statuses": _statuses_property(),
            "limit": {"type": "integer", "minimum": 1},
            "default_template_id": {"type": "string"},
            "retry_limit": {"type": "integer", "minimum": 1},
            "dispatch_limit": {"type": "integer", "minimum": 1},
            "release_after_tick": {"type": "boolean"},
        },
        "required": ["worker_id", "lease_seconds"],
    }


def _plan_dispatch_ready_steps_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "default_template_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["plan_id"],
    }


def _plan_scheduler_tick_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "default_template_id": {"type": "string"},
            "retry_limit": {"type": "integer", "minimum": 1},
            "dispatch_limit": {"type": "integer", "minimum": 1},
        },
        "required": ["plan_id"],
    }


def _plan_fail_step_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "step_id": {"type": "string"},
            "error": {"type": "string"},
        },
        "required": ["plan_id", "step_id", "error"],
    }


def _plan_step_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "step_id": {"type": "string"},
        },
        "required": ["plan_id", "step_id"],
    }


def _plan_record_evidence_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "step_ids": {"type": "array", "items": {"type": "string"}},
            "kind": {"type": "string", "enum": list(EVIDENCE_KINDS)},
            "summary": {"type": "string"},
            "uri": {"type": "string"},
            "producer_agent_id": {"type": "string"},
            "metadata": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["plan_id", "kind", "summary"],
    }


def _plan_complete_step_parameters() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string"},
            "step_id": {"type": "string"},
            "evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["plan_id", "step_id"],
    }
