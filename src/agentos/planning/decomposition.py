from __future__ import annotations

from collections.abc import Callable, Mapping as MappingABC
from dataclasses import dataclass, field
from typing import Mapping

from agentos.planning.models import PlanStep, SubAgentTemplate


@dataclass(frozen=True, slots=True)
class PlanStepSpec:
    """分解策略提出的一个结构化步骤。"""

    instruction: str
    step_id: str | None = None
    required_capabilities: tuple[str, ...] = ()
    template_id: str | None = None
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanDecomposition:
    """应用侧分解策略生成的结构化 Plan 提案。"""

    objective: str
    steps: tuple[PlanStepSpec, ...]


@dataclass(frozen=True, slots=True)
class PlanDecompositionValidationReport:
    """结构化 Plan 分解提案的校验结果。"""

    ok: bool
    errors: tuple[str, ...] = ()
    step_count: int = 0
    required_templates: tuple[str, ...] = ()
    unknown_templates: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """返回 JSON-safe 校验报告。"""

        return {
            "ok": self.ok,
            "errors": self.errors,
            "step_count": self.step_count,
            "required_templates": self.required_templates,
            "unknown_templates": self.unknown_templates,
        }


@dataclass(frozen=True, slots=True)
class PlanDecompositionGatePolicy:
    """LLM 原始分解提案持久化前的门禁策略。"""

    max_steps: int | None = None
    require_template: bool = False
    require_approval: bool = False
    approved: bool = False
    allowed_template_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_steps is not None and self.max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        if any(not template_id.strip() for template_id in self.allowed_template_ids):
            raise ValueError("allowed_template_ids must not contain empty names")


@dataclass(frozen=True, slots=True)
class PlanDecompositionGateReport:
    """LLM 原始分解提案的 JSON-safe 门禁报告。"""

    accepted: bool
    requires_approval: bool = False
    errors: tuple[str, ...] = ()
    validation: PlanDecompositionValidationReport | None = None
    step_count: int = 0
    required_templates: tuple[str, ...] = ()
    unknown_templates: tuple[str, ...] = ()
    missing_templates: tuple[str, ...] = ()
    disallowed_templates: tuple[str, ...] = ()
    normalized_decomposition: PlanDecomposition | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        """返回 JSON-safe 门禁报告。"""

        return {
            "accepted": self.accepted,
            "requires_approval": self.requires_approval,
            "errors": self.errors,
            "validation": (
                self.validation.as_dict() if self.validation is not None else None
            ),
            "step_count": self.step_count,
            "required_templates": self.required_templates,
            "unknown_templates": self.unknown_templates,
            "missing_templates": self.missing_templates,
            "disallowed_templates": self.disallowed_templates,
            "normalized_decomposition": (
                self._decomposition_to_dict(self.normalized_decomposition)
                if self.normalized_decomposition is not None
                else None
            ),
            "metadata": dict(self.metadata),
        }

    def _decomposition_to_dict(
        self,
        decomposition: PlanDecomposition,
    ) -> dict[str, object]:
        return {
            "objective": decomposition.objective,
            "steps": [
                {
                    "step_id": step.step_id,
                    "instruction": step.instruction,
                    "required_capabilities": step.required_capabilities,
                    "template_id": step.template_id,
                    "depends_on": step.depends_on,
                }
                for step in decomposition.steps
            ],
        }


def materialize_decomposition(
    decomposition: PlanDecomposition,
    *,
    templates: Mapping[str, SubAgentTemplate],
    id_factory: Callable[[str], object],
) -> tuple[str, tuple[PlanStep, ...]]:
    """校验并物化一个分解提案，不执行持久化。"""

    objective = decomposition.objective.strip()
    if not objective:
        raise ValueError("decomposition objective is required")
    if not decomposition.steps:
        raise ValueError("decomposition must include at least one step")
    steps = tuple(
        _step_from_spec(spec, templates=templates, id_factory=id_factory)
        for spec in decomposition.steps
    )
    _validate_step_dependencies(steps)
    return objective, steps


def validate_decomposition(
    decomposition: PlanDecomposition,
    *,
    templates: Mapping[str, SubAgentTemplate],
) -> PlanDecompositionValidationReport:
    """校验结构化分解提案，不执行持久化。"""

    errors: list[str] = []
    if not decomposition.objective.strip():
        errors.append("decomposition objective is required")
    if not decomposition.steps:
        errors.append("decomposition must include at least one step")

    required_templates = tuple(
        dict.fromkeys(
            spec.template_id
            for spec in decomposition.steps
            if spec.template_id is not None
        ),
    )
    unknown_templates = tuple(
        template_id
        for template_id in required_templates
        if template_id not in templates
    )
    errors.extend(
        f"unknown template: {template_id}" for template_id in unknown_templates
    )

    steps: list[PlanStep] = []
    for index, spec in enumerate(decomposition.steps, start=1):
        instruction = spec.instruction.strip()
        if not instruction:
            errors.append("decomposition step instruction is required")
        if spec.template_id is not None and spec.template_id not in templates:
            if spec.template_id not in unknown_templates:
                errors.append(f"unknown template: {spec.template_id}")
        steps.append(
            PlanStep(
                step_id=spec.step_id or f"__generated_step_{index}",
                instruction=instruction,
                required_capabilities=tuple(spec.required_capabilities),
                template_id=spec.template_id,
                depends_on=tuple(spec.depends_on),
            ),
        )

    try:
        _validate_step_dependencies(tuple(steps))
    except ValueError as error:
        errors.append(str(error))

    deduped_errors = tuple(dict.fromkeys(errors))
    return PlanDecompositionValidationReport(
        ok=not deduped_errors,
        errors=deduped_errors,
        step_count=len(decomposition.steps),
        required_templates=required_templates,
        unknown_templates=unknown_templates,
    )


def gate_decomposition_proposal(
    proposal: Mapping[str, object],
    *,
    templates: Mapping[str, SubAgentTemplate],
    policy: PlanDecompositionGatePolicy | None = None,
    metadata: Mapping[str, object] | None = None,
) -> PlanDecompositionGateReport:
    """解析并校验原始分解提案，不执行持久化。"""

    gate_policy = policy or PlanDecompositionGatePolicy()
    try:
        decomposition = _decomposition_from_proposal(proposal)
    except ValueError as error:
        return PlanDecompositionGateReport(
            accepted=False,
            errors=(str(error),),
            metadata={} if metadata is None else dict(metadata),
        )

    validation = validate_decomposition(decomposition, templates=templates)
    errors = list(validation.errors)
    step_count = len(decomposition.steps)
    missing_templates = _proposal_steps_missing_templates(
        decomposition,
        require_template=gate_policy.require_template,
    )
    disallowed_templates = _proposal_disallowed_templates(
        validation.required_templates,
        allowed_template_ids=gate_policy.allowed_template_ids,
    )

    if gate_policy.max_steps is not None and step_count > gate_policy.max_steps:
        errors.append(
            f"decomposition exceeds max_steps: {step_count} > {gate_policy.max_steps}",
        )
    errors.extend(
        f"step {step_id} requires a template_id" for step_id in missing_templates
    )
    errors.extend(
        f"template not allowed: {template_id}" for template_id in disallowed_templates
    )

    requires_approval = gate_policy.require_approval and not gate_policy.approved
    if requires_approval:
        errors.append("decomposition approval is required")

    deduped_errors = tuple(dict.fromkeys(errors))
    accepted = not deduped_errors and validation.ok
    return PlanDecompositionGateReport(
        accepted=accepted,
        requires_approval=requires_approval,
        errors=deduped_errors,
        validation=validation,
        step_count=step_count,
        required_templates=validation.required_templates,
        unknown_templates=validation.unknown_templates,
        missing_templates=missing_templates,
        disallowed_templates=disallowed_templates,
        normalized_decomposition=decomposition if accepted else None,
        metadata={} if metadata is None else dict(metadata),
    )


def _step_from_spec(
    spec: PlanStepSpec,
    *,
    templates: Mapping[str, SubAgentTemplate],
    id_factory: Callable[[str], object],
) -> PlanStep:
    instruction = spec.instruction.strip()
    if not instruction:
        raise ValueError("decomposition step instruction is required")
    if spec.template_id is not None:
        try:
            templates[spec.template_id]
        except KeyError as error:
            raise KeyError(spec.template_id) from error
    return PlanStep(
        step_id=spec.step_id or str(id_factory("step")),
        instruction=instruction,
        required_capabilities=tuple(spec.required_capabilities),
        template_id=spec.template_id,
        depends_on=tuple(spec.depends_on),
    )


def _decomposition_from_proposal(
    proposal: Mapping[str, object],
) -> PlanDecomposition:
    if not isinstance(proposal, MappingABC):
        raise ValueError("decomposition proposal must be an object")
    objective_value = proposal.get("objective")
    if objective_value is None:
        raise ValueError("decomposition objective is required")
    steps_value = proposal.get("steps")
    if not isinstance(steps_value, list | tuple):
        raise ValueError("decomposition steps must be a list")
    return PlanDecomposition(
        objective=str(objective_value).strip(),
        steps=_proposal_step_specs(steps_value),
    )


def _proposal_step_specs(
    value: list[object] | tuple[object, ...],
) -> tuple[PlanStepSpec, ...]:
    specs: list[PlanStepSpec] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, MappingABC):
            raise ValueError(f"step {index} must be an object")
        instruction_value = item.get("instruction")
        if instruction_value is None:
            raise ValueError(f"step {index} instruction is required")
        specs.append(
            PlanStepSpec(
                instruction=str(instruction_value).strip(),
                step_id=_optional_string(item.get("step_id")),
                required_capabilities=_string_tuple_from_raw(
                    item.get("required_capabilities", ()),
                    field_name=f"step {index} required_capabilities",
                ),
                template_id=_optional_string(item.get("template_id")),
                depends_on=_string_tuple_from_raw(
                    item.get("depends_on", ()),
                    field_name=f"step {index} depends_on",
                ),
            ),
        )
    return tuple(specs)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _string_tuple_from_raw(value: object, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list | tuple):
        raise ValueError(f"{field_name} must be a list")
    return tuple(str(item) for item in value)


def _proposal_steps_missing_templates(
    decomposition: PlanDecomposition,
    *,
    require_template: bool,
) -> tuple[str, ...]:
    if not require_template:
        return ()
    return tuple(
        step.step_id or f"step_{index}"
        for index, step in enumerate(decomposition.steps, start=1)
        if step.template_id is None or not step.template_id.strip()
    )


def _proposal_disallowed_templates(
    required_templates: tuple[str, ...],
    *,
    allowed_template_ids: tuple[str, ...],
) -> tuple[str, ...]:
    if not allowed_template_ids:
        return ()
    allowed = set(allowed_template_ids)
    return tuple(
        template_id for template_id in required_templates if template_id not in allowed
    )


def _validate_step_dependencies(steps: tuple[PlanStep, ...]) -> None:
    by_id = {step.step_id: step for step in steps}
    if len(by_id) != len(steps):
        raise ValueError("decomposition step ids must be unique")
    for step in steps:
        unknown = sorted(set(step.depends_on).difference(by_id))
        if unknown:
            raise ValueError(
                f"unknown dependency for {step.step_id}: {', '.join(unknown)}",
            )
        if step.step_id in step.depends_on:
            raise ValueError(f"dependency cycle includes {step.step_id}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visited:
            return
        if step_id in visiting:
            raise ValueError(f"dependency cycle includes {step_id}")
        visiting.add(step_id)
        for dependency_id in by_id[step_id].depends_on:
            visit(dependency_id)
        visiting.remove(step_id)
        visited.add(step_id)

    for step in steps:
        visit(step.step_id)
