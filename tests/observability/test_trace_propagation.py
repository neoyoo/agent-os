from agentos.observability import InMemoryTracer, TraceIds


def test_in_memory_tracer_creates_trace_id_for_root_span() -> None:
    tracer = InMemoryTracer()

    with tracer.start_span("agent.turn"):
        ids = tracer.current_trace_ids()

    assert ids.trace_id is not None
    assert len(ids.trace_id) == 32
    assert ids.span_id is not None
    assert len(ids.span_id) == 16
    assert ids.is_remote is False
    assert tracer.records[0].trace_id == ids.trace_id


def test_nested_spans_share_trace_id_and_have_distinct_span_ids() -> None:
    tracer = InMemoryTracer()

    with tracer.start_span("agent.turn"):
        root = tracer.current_trace_ids()
        with tracer.start_span("provider.complete"):
            child = tracer.current_trace_ids()

    assert child.trace_id == root.trace_id
    assert child.span_id != root.span_id
    assert tracer.records[1].parent_span_id == tracer.records[0].span_id


def test_in_memory_tracer_injects_current_traceparent() -> None:
    tracer = InMemoryTracer()
    headers: dict[str, str] = {}

    with tracer.start_span("agent.turn"):
        ids = tracer.current_trace_ids()
        tracer.inject_headers(headers)

    assert headers["traceparent"] == f"00-{ids.trace_id}-{ids.span_id}-01"


def test_in_memory_tracer_extracts_incoming_traceparent() -> None:
    tracer = InMemoryTracer()
    trace_id = "1" * 32
    parent_span_id = "2" * 16

    with tracer.use_incoming_headers(
        {"traceparent": f"00-{trace_id}-{parent_span_id}-01"},
    ):
        with tracer.start_span("agent.turn"):
            ids = tracer.current_trace_ids()

    assert ids.trace_id == trace_id
    assert tracer.records[0].trace_id == trace_id
    assert tracer.records[0].parent_span_id == parent_span_id


def test_in_memory_tracer_ignores_invalid_traceparent() -> None:
    tracer = InMemoryTracer()

    with tracer.use_incoming_headers({"traceparent": "not-valid"}):
        with tracer.start_span("agent.turn"):
            ids = tracer.current_trace_ids()

    assert ids.trace_id is not None
    assert ids.trace_id != "not-valid"
    assert tracer.records[0].parent_span_id is None


def test_current_trace_ids_is_empty_outside_span() -> None:
    tracer = InMemoryTracer()

    ids = tracer.current_trace_ids()

    assert ids == TraceIds(trace_id=None, span_id=None)
