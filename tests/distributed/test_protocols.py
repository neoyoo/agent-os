import inspect

from agentos.distributed.protocols import (
    DistributedArtifactPort,
    EventReplayPort,
    EventSubscription,
    ExecutionClaimPort,
    LeasePort,
    OutboxPort,
    QueuePort,
    RunCommandPort,
    RunQueryPort,
    RunSubmissionPort,
)


PROTOCOL_METHODS = {
    RunSubmissionPort: ("submit",),
    RunCommandPort: ("submit_command",),
    RunQueryPort: ("get_run",),
    ExecutionClaimPort: (
        "resolve_delivery",
        "claim_pending_turn",
        "heartbeat",
        "release",
        "recover_expired",
    ),
    OutboxPort: ("claim_batch", "mark_published", "release_claim"),
    QueuePort: ("publish", "receive", "reclaim", "ack", "close"),
    LeasePort: ("acquire", "renew", "ensure_owned", "release"),
    EventReplayPort: ("append", "replay", "follow", "close"),
    DistributedArtifactPort: ("upload", "list", "read", "delete"),
}


def test_shared_boundaries_are_narrow_protocols() -> None:
    for protocol, method_names in PROTOCOL_METHODS.items():
        assert protocol._is_protocol is True
        public_methods = tuple(
            name
            for name, value in protocol.__dict__.items()
            if not name.startswith("_") and callable(value)
        )
        assert public_methods == method_names


def test_all_io_protocol_methods_are_native_async() -> None:
    for protocol, method_names in PROTOCOL_METHODS.items():
        for method_name in method_names:
            method = getattr(protocol, method_name)
            if protocol is EventReplayPort and method_name == "follow":
                assert "EventSubscription" in str(inspect.signature(method).return_annotation)
            else:
                assert inspect.iscoroutinefunction(method), (
                    f"{protocol.__name__}.{method_name} must be async"
                )


def test_protocols_have_no_sync_shadow_methods() -> None:
    forbidden = {"sync", "blocking", "run_sync", "to_thread"}
    for protocol, method_names in PROTOCOL_METHODS.items():
        assert not any(
            token in method_name
            for method_name in method_names
            for token in forbidden
        )


def test_application_and_tenant_ports_require_explicit_scope() -> None:
    scoped_methods = (
        (RunSubmissionPort, "submit"),
        (RunCommandPort, "submit_command"),
        (RunQueryPort, "get_run"),
        (ExecutionClaimPort, "claim_pending_turn"),
        (ExecutionClaimPort, "heartbeat"),
        (ExecutionClaimPort, "release"),
        (ExecutionClaimPort, "recover_expired"),
        (LeasePort, "acquire"),
        (LeasePort, "renew"),
        (LeasePort, "ensure_owned"),
        (LeasePort, "release"),
        (EventReplayPort, "append"),
        (EventReplayPort, "replay"),
        (EventReplayPort, "follow"),
        (DistributedArtifactPort, "upload"),
        (DistributedArtifactPort, "list"),
        (DistributedArtifactPort, "read"),
        (DistributedArtifactPort, "delete"),
    )
    for protocol, method_name in scoped_methods:
        assert "scope" in inspect.signature(getattr(protocol, method_name)).parameters

    assert "scope" not in inspect.signature(
        ExecutionClaimPort.resolve_delivery,
    ).parameters
    assert "scope" not in inspect.signature(OutboxPort.claim_batch).parameters
    assert "scope" not in inspect.signature(QueuePort.receive).parameters


def test_queue_publish_requires_the_authoritative_outbox_record() -> None:
    parameters = inspect.signature(QueuePort.publish).parameters

    assert tuple(parameters) == ("self", "record")
    assert "OutboxRecord" in str(parameters["record"].annotation)


def test_event_subscription_has_explicit_async_close() -> None:
    assert EventSubscription._is_protocol is True
    assert inspect.iscoroutinefunction(EventSubscription.__anext__)
    assert inspect.iscoroutinefunction(EventSubscription.aclose)
