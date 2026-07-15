import inspect

from agentos.planning import (
    PlanDispatchAlreadySubmittedError,
    PlanError,
    PlanStepDispatcher,
)


def test_plan_step_dispatcher_exposes_only_domain_values() -> None:
    signature = inspect.signature(PlanStepDispatcher.submit)

    assert tuple(signature.parameters) == (
        "self",
        "plan",
        "step",
        "assignment",
        "template",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for name, parameter in signature.parameters.items()
        if name != "self"
    )
    assert issubclass(PlanDispatchAlreadySubmittedError, PlanError)
