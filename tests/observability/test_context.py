from agentos.observability import (
    ObservabilityContext,
    RuntimeTraceContext,
    current_observability_context,
    current_runtime_trace_context,
    current_trace_ids,
    inject_trace_headers,
    use_default_trace_propagator,
    use_observability_context,
    use_runtime_trace_context,
)
from agentos.observability.tracer import InMemoryTracer


def test_observability_context_defaults_to_empty() -> None:
    context = current_observability_context()

    assert context == ObservabilityContext()
    assert context.user_id is None
    assert context.incoming_headers is None
    assert context.metadata == {}


def test_use_observability_context_sets_and_restores_values() -> None:
    incoming = {
        "traceparent": "00-11111111111111111111111111111111-2222222222222222-01",
    }

    with use_observability_context(
        user_id="u_1",
        incoming_headers=incoming,
        metadata={"channel": "cli"},
    ):
        context = current_observability_context()
        assert context.user_id == "u_1"
        assert context.incoming_headers == incoming
        assert context.metadata == {"channel": "cli"}

    assert current_observability_context() == ObservabilityContext()


def test_use_observability_context_accepts_explicit_context() -> None:
    context = ObservabilityContext(user_id="u_2", metadata={"channel": "web"})

    with use_observability_context(context) as scoped:
        assert scoped is context
        assert current_observability_context() is context

    assert current_observability_context() == ObservabilityContext()


def test_nested_observability_context_restores_outer_value() -> None:
    with use_observability_context(user_id="outer"):
        assert current_observability_context().user_id == "outer"
        with use_observability_context(user_id="inner"):
            assert current_observability_context().user_id == "inner"
        assert current_observability_context().user_id == "outer"


def test_runtime_trace_context_defaults_to_empty() -> None:
    context = current_runtime_trace_context()

    assert context == RuntimeTraceContext()
    assert context.session_id is None
    assert context.turn_id is None


def test_runtime_trace_context_is_internal_and_scoped() -> None:
    assert current_runtime_trace_context().session_id is None
    assert current_runtime_trace_context().turn_id is None

    with use_runtime_trace_context(session_id="s1", turn_id="turn_1"):
        context = current_runtime_trace_context()
        assert context.session_id == "s1"
        assert context.turn_id == "turn_1"

    assert current_runtime_trace_context().session_id is None
    assert current_runtime_trace_context().turn_id is None


def test_default_trace_propagator_is_scoped() -> None:
    tracer = InMemoryTracer()

    with use_default_trace_propagator(tracer):
        with tracer.start_span("agent.turn"):
            headers: dict[str, str] = {}
            injected = inject_trace_headers(headers)
            ids = current_trace_ids()

    assert injected is headers
    assert headers["traceparent"] == f"00-{ids.trace_id}-{ids.span_id}-01"
    assert current_trace_ids().trace_id is None
