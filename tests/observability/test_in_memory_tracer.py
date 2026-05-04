from agentos.observability.tracer import InMemoryTracer, NoOpTracer


def test_in_memory_tracer_records_nested_spans_with_parent_ids() -> None:
    tracer = InMemoryTracer()

    with tracer.start_span("agent.turn", attributes={"agentos.capture.mode": "full"}):
        with tracer.start_span("provider.complete") as child:
            child.set_attribute("langfuse.observation.type", "generation")
            child.add_event("provider.response", {"tool_call_count": 0})

    assert [record.name for record in tracer.records] == [
        "agent.turn",
        "provider.complete",
    ]
    root, child = tracer.records
    assert root.parent_span_id is None
    assert child.parent_span_id == root.span_id
    assert root.attributes["agentos.capture.mode"] == "full"
    assert child.attributes["langfuse.observation.type"] == "generation"
    assert child.events[0].name == "provider.response"
    assert child.events[0].attributes == {"tool_call_count": 0}
    assert root.status == "ok"
    assert child.status == "ok"


def test_in_memory_span_records_exception_and_reraises() -> None:
    tracer = InMemoryTracer()

    try:
        with tracer.start_span("provider.complete"):
            raise ValueError("bad response")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError")

    record = tracer.records[0]
    assert record.status == "error"
    assert record.status_description == "bad response"
    assert record.events[0].name == "exception"
    assert record.events[0].attributes["exception.type"] == "ValueError"
    assert record.events[0].attributes["exception.message"] == "bad response"


def test_noop_tracer_accepts_span_operations() -> None:
    tracer = NoOpTracer()

    with tracer.start_span("noop") as span:
        span.set_attribute("key", "value")
        span.set_attributes({"a": 1})
        span.add_event("event", {"b": 2})
        span.record_exception(RuntimeError("ignored"))
        span.set_status("error", "ignored")
