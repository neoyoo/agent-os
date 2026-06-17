from __future__ import annotations

import json
import sys

from agentos.channels.a2a import (
    A2AAgentCapabilities,
    A2AAgentCard,
    A2AAgentExtension,
    A2AAgentSkill,
)
from agentos.channels.a2a_operations import A2AExtensionNegotiationPolicy


def conformance_card() -> A2AAgentCard:
    return A2AAgentCard(
        name="Research Agent",
        description="Answers research tasks.",
        url="https://agents.example/a2a",
        version="1.0.0",
        protocol_version="1.0.0",
        capabilities=A2AAgentCapabilities(
            streaming=True,
            extensions=(
                A2AAgentExtension(
                    uri="https://extensions.example/trace-artifacts/v1",
                    description="Trace artifact propagation.",
                ),
            ),
        ),
        skills=(
            A2AAgentSkill(
                id="research",
                name="Research",
                description="Research a question.",
                input_modes=("text/plain",),
                output_modes=("text/plain",),
            ),
        ),
    )


def test_a2a_conformance_report_passes_for_supported_card_and_payloads() -> None:
    from agentos.channels.a2a_conformance import A2AConformanceHarness

    harness = A2AConformanceHarness()

    report = harness.run(conformance_card())

    assert report.passed is True
    assert report.failed_checks == ()
    assert "agent-card-required-fields" in report.check_ids
    assert "operation-jsonrpc-envelope" in report.check_ids
    assert "message-stream-jsonrpc-envelope" in report.check_ids
    assert "message-stream-event-shape" in report.check_ids
    assert "task-resubscribe-jsonrpc-envelope" in report.check_ids
    assert "task-resubscribe-event-shape" in report.check_ids
    assert "extension-negotiation" in report.check_ids
    assert report.finding("message-stream-jsonrpc-envelope").passed is True
    assert report.finding("message-stream-event-shape").passed is True
    assert report.finding("task-resubscribe-jsonrpc-envelope").passed is True
    assert report.finding("task-resubscribe-event-shape").passed is True
    assert report.to_dict()["passed"] is True


def test_a2a_conformance_report_fails_non_stream_operation_payload() -> None:
    from agentos.channels.a2a_conformance import A2AConformanceHarness
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        a2a_operation_request_to_dict,
    )

    send_payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(
                role="user",
                parts=(A2AMessagePart.from_text("hello"),),
            ),
        ),
    )

    report = A2AConformanceHarness().run(
        conformance_card(),
        stream_operation_request_payload=send_payload,
    )

    stream_check = report.finding("message-stream-jsonrpc-envelope")
    assert report.passed is False
    assert stream_check.passed is False
    assert "SendStreamingMessage" in stream_check.detail


def test_a2a_conformance_report_fails_malformed_stream_event_payload() -> None:
    from agentos.channels.a2a_conformance import A2AConformanceHarness

    report = A2AConformanceHarness().run(
        conformance_card(),
        stream_event_payload={"unknown": {"taskId": "task_1"}},
    )

    event_check = report.finding("message-stream-event-shape")
    assert report.passed is False
    assert event_check.passed is False
    assert "message stream event" in event_check.detail


def test_a2a_conformance_report_fails_non_resubscribe_operation_payload() -> None:
    from agentos.channels.a2a_conformance import A2AConformanceHarness
    from agentos.channels.a2a_operations import (
        A2AMessage,
        A2AMessagePart,
        A2AOperationRequest,
        a2a_operation_request_to_dict,
    )

    send_payload = a2a_operation_request_to_dict(
        A2AOperationRequest.message_send(
            A2AMessage(
                role="user",
                parts=(A2AMessagePart.from_text("hello"),),
            ),
        ),
    )

    report = A2AConformanceHarness().run(
        conformance_card(),
        task_resubscribe_request_payload=send_payload,
    )

    resubscribe_check = report.finding("task-resubscribe-jsonrpc-envelope")
    assert report.passed is False
    assert resubscribe_check.passed is False
    assert "SubscribeToTask" in resubscribe_check.detail


def test_a2a_conformance_report_fails_malformed_resubscribe_event_payload() -> None:
    from agentos.channels.a2a_conformance import A2AConformanceHarness

    report = A2AConformanceHarness().run(
        conformance_card(),
        task_resubscribe_event_payload={"taskEvent": {"id": "task_1"}},
    )

    event_check = report.finding("task-resubscribe-event-shape")
    assert report.passed is False
    assert event_check.passed is False
    assert "statusUpdate" in event_check.detail


def test_a2a_conformance_report_fails_missing_card_fields_and_legacy_kind_payloads() -> None:
    from agentos.channels.a2a_conformance import A2AConformanceHarness

    legacy_message_payload = {
        "role": "user",
        "parts": [{"kind": "text", "text": "hello"}],
    }
    report = A2AConformanceHarness().run(
        A2AAgentCard(
            name="Legacy Agent",
            description="Uses legacy payloads.",
            url="",
            version="",
        ),
        message_payload=legacy_message_payload,
    )

    failed_check_ids = {finding.check_id for finding in report.failed_checks}

    assert report.passed is False
    assert "agent-card-required-fields" in failed_check_ids
    assert "message-part-wrapper-shape" in failed_check_ids


def test_a2a_conformance_report_records_extension_negotiation_error_mapping() -> None:
    from agentos.channels.a2a_conformance import A2AConformanceHarness

    required_uri = "https://extensions.example/trace-artifacts/v1"
    policy = A2AExtensionNegotiationPolicy(
        local_extensions=(A2AAgentExtension(uri=required_uri, required=True),),
    )

    report = A2AConformanceHarness().run(
        conformance_card(),
        extension_negotiation_policy=policy,
        headers={"A2A-Version": "1.0"},
    )
    mapping_check = report.finding("extension-required-error-mapping")

    assert mapping_check.passed is True
    assert mapping_check.evidence["errorCode"] == -32008
    assert mapping_check.evidence["missingExtensions"] == [required_uri]


def test_a2a_external_conformance_importer_parses_canonical_json_report() -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceReportImporter,
    )

    payload = """
    {
      "suite": "official-a2a-conformance",
      "version": "2026.06",
      "runId": "ci-123",
      "target": "https://agents.example/a2a",
      "metadata": {"commit": "abc123"},
      "checks": [
        {
          "checkId": "agent-card",
          "title": "Agent Card",
          "passed": true,
          "detail": "card accepted",
          "evidence": {"durationMs": 12}
        },
        {
          "checkId": "message-send",
          "title": "message/send",
          "passed": false,
          "detail": "missing task id"
        }
      ]
    }
    """

    report = A2AExternalConformanceReportImporter().from_json(payload)

    assert report.suite == "official-a2a-conformance"
    assert report.source == "external"
    assert report.version == "2026.06"
    assert report.run_id == "ci-123"
    assert report.target == "https://agents.example/a2a"
    assert report.metadata == {"commit": "abc123"}
    assert report.passed is False
    assert report.check_ids == ("agent-card", "message-send")
    assert report.finding("agent-card").passed is True
    assert report.finding("agent-card").evidence == {"durationMs": 12}
    assert report.finding("message-send").detail == "missing task id"
    assert report.to_dict()["source"] == "external"


def test_a2a_external_conformance_importer_accepts_common_alias_fields() -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceReportImporter,
    )

    report = A2AExternalConformanceReportImporter().from_mapping(
        {
            "name": "vendor-suite",
            "run_id": "run-1",
            "checks": [
                {
                    "id": "streaming",
                    "name": "Streaming",
                    "status": "pass",
                    "message": "stream worked",
                },
                {
                    "id": "push",
                    "name": "Push notifications",
                    "status": "failed",
                    "error": "webhook rejected",
                },
            ],
        },
    )

    assert report.suite == "vendor-suite"
    assert report.run_id == "run-1"
    assert report.finding("streaming").passed is True
    assert report.finding("streaming").detail == "stream worked"
    assert report.finding("push").passed is False
    assert report.finding("push").detail == "webhook rejected"


def test_a2a_external_conformance_importer_rejects_malformed_reports() -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceImportError,
        A2AExternalConformanceReportImporter,
    )

    importer = A2AExternalConformanceReportImporter()

    for payload, expected in [
        ("not-json", "invalid JSON"),
        ({"suite": "missing checks"}, "checks"),
        ({"checks": [{"title": "missing id", "passed": True}]}, "check id"),
        ({"checks": [{"checkId": "missing-result"}]}, "passed"),
    ]:
        try:
            if isinstance(payload, str):
                importer.from_json(payload)
            else:
                importer.from_mapping(payload)
        except A2AExternalConformanceImportError as error:
            assert expected in str(error)
        else:
            raise AssertionError("malformed report should fail")


def test_a2a_external_conformance_invocation_plan_gate_marks_ready() -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceInvocationGateReport,
        A2AExternalConformanceInvocationPlan,
    )

    plan = A2AExternalConformanceInvocationPlan(
        suite_id="official-a2a-conformance",
        suite_version="2026.06",
        target="https://agents.example/a2a",
        command=("a2a-conformance", "--target", "https://agents.example/a2a"),
        credential_policy_ref="vault://a2a/conformance/client",
        network_egress_policy_ref="egress-policy:a2a-public-https",
        version_matrix_ref="matrix:a2a-1.0",
        artifact_retention_ref="s3://ci/a2a/conformance/",
        failure_alerting_ref="pagerduty:a2a-conformance",
        required_check_ids=("agent-card", "message-send", "message-stream"),
        metadata={"environment": "ci"},
    )

    gate = A2AExternalConformanceInvocationGateReport.from_plan(plan)
    payload = gate.as_dict()

    assert gate.ready is True
    assert gate.status == "ok"
    assert gate.missing_components == ()
    assert payload["ready"] is True
    assert payload["plan"]["suite_id"] == "official-a2a-conformance"
    assert payload["plan"]["suite_version"] == "2026.06"
    assert payload["plan"]["target"] == "https://agents.example/a2a"
    assert payload["plan"]["command"] == (
        "a2a-conformance",
        "--target",
        "https://agents.example/a2a",
    )
    assert payload["plan"]["credential_policy_ref"] == (
        "vault://a2a/conformance/client"
    )
    assert payload["plan"]["metadata"] == {"environment": "ci"}
    assert payload["required_components"] == (
        "external_suite_runner",
        "target_endpoint",
        "credential_policy",
        "network_egress_policy",
        "version_matrix",
        "ci_artifact_retention",
        "failure_alerting",
    )
    assert payload["configured_components"] == (
        "external_suite_runner",
        "target_endpoint",
        "credential_policy",
        "network_egress_policy",
        "version_matrix",
        "ci_artifact_retention",
        "failure_alerting",
    )
    assert payload["no_certification_claim"] is True
    assert "external conformance invocation plan" in payload["sdk_owned"]
    assert "external suite execution" in payload["deployment_owned"]


def test_a2a_external_conformance_invocation_plan_reports_missing_components() -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceInvocationGateReport,
        A2AExternalConformanceInvocationPlan,
    )

    plan = A2AExternalConformanceInvocationPlan(
        suite_id="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=("a2a-conformance",),
        credential_policy_ref="vault://a2a/conformance/client",
        network_egress_policy_ref="egress-policy:a2a-public-https",
        version_matrix_ref="matrix:a2a-1.0",
    )

    gate = A2AExternalConformanceInvocationGateReport.from_plan(plan)
    readiness = plan.readiness_check()

    assert gate.ready is False
    assert gate.status == "failed"
    assert gate.missing_components == (
        "ci_artifact_retention",
        "failure_alerting",
    )
    assert readiness["ok"] is False
    assert readiness["status"] == "failed"
    assert readiness["missing_components"] == (
        "ci_artifact_retention",
        "failure_alerting",
    )
    assert "external suite execution" in readiness["deployment_owned"]


def test_a2a_external_conformance_invocation_plan_projects_execution_record() -> None:
    from agentos.channels.a2a_conformance import (
        A2AConformanceFinding,
        A2AConformanceReport,
        A2AExternalConformanceExecutionRecord,
        A2AExternalConformanceInvocationPlan,
    )

    report = A2AConformanceReport(
        suite="official-a2a-conformance",
        source="external",
        version="2026.06",
        run_id="ci-900",
        target="https://agents.example/a2a",
        findings=(
            A2AConformanceFinding("agent-card", "Agent Card", True),
            A2AConformanceFinding("message-send", "message/send", True),
        ),
    )
    plan = A2AExternalConformanceInvocationPlan(
        suite_id="official-a2a-conformance",
        suite_version="2026.06",
        target="https://agents.example/a2a",
        command=("a2a-conformance", "--target", "https://agents.example/a2a"),
        credential_policy_ref="vault://a2a/conformance/client",
        network_egress_policy_ref="egress-policy:a2a-public-https",
        version_matrix_ref="matrix:a2a-1.0",
        artifact_retention_ref="s3://ci/a2a/conformance/",
        failure_alerting_ref="pagerduty:a2a-conformance",
    )

    record = plan.to_execution_record(
        exit_code=0,
        started_at=100.0,
        ended_at=104.5,
        environment="ci",
        artifact_uri="s3://ci/a2a/conformance/report.json",
        stdout_summary="2 checks passed",
        report=report,
        metadata={"commit": "abc123"},
    )

    assert isinstance(record, A2AExternalConformanceExecutionRecord)
    assert record.suite == "official-a2a-conformance"
    assert record.target == "https://agents.example/a2a"
    assert record.command == (
        "a2a-conformance",
        "--target",
        "https://agents.example/a2a",
    )
    assert record.exit_code == 0
    assert record.duration_seconds == 4.5
    assert record.report is report
    assert record.metadata == {
        "suite_version": "2026.06",
        "plan_metadata": {},
        "commit": "abc123",
    }


def test_a2a_external_conformance_evidence_redacts_secret_command_arguments() -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceExecutionRecord,
        A2AExternalConformanceInvocationPlan,
    )

    plan = A2AExternalConformanceInvocationPlan(
        suite_id="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=(
            "a2a-conformance",
            "--token",
            "raw-token",
            "--api-key=raw-api-key",
            "--header",
            "Authorization: Bearer raw-bearer",
        ),
        credential_policy_ref="vault://a2a/conformance/client",
        network_egress_policy_ref="egress-policy:a2a-public-https",
        version_matrix_ref="matrix:a2a-1.0",
        artifact_retention_ref="s3://ci/a2a/conformance/",
        failure_alerting_ref="pagerduty:a2a-conformance",
    )
    record = A2AExternalConformanceExecutionRecord(
        suite="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=plan.command,
        exit_code=0,
    )

    assert record.command == plan.command
    assert plan.as_dict()["command"] == (
        "a2a-conformance",
        "--token",
        "<redacted>",
        "--api-key=<redacted>",
        "--header",
        "Authorization: Bearer <redacted>",
    )
    encoded = json.dumps(
        {
            "plan": plan.as_dict(),
            "record": record.as_dict(),
        },
    )
    assert "raw-token" not in encoded
    assert "raw-api-key" not in encoded
    assert "raw-bearer" not in encoded


def test_a2a_external_conformance_invocation_plan_rejects_invalid_values() -> None:
    import pytest

    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceInvocationPlan,
    )

    valid_kwargs = {
        "suite_id": "official-a2a-conformance",
        "target": "https://agents.example/a2a",
        "command": ("a2a-conformance",),
        "credential_policy_ref": "vault://a2a/conformance/client",
        "network_egress_policy_ref": "egress-policy:a2a-public-https",
        "version_matrix_ref": "matrix:a2a-1.0",
    }

    for override, expected in [
        ({"suite_id": " "}, "suite_id"),
        ({"target": " "}, "target"),
        ({"command": ()}, "command"),
        ({"command": ("a2a-conformance", " ")}, "command"),
        ({"credential_policy_ref": " "}, "credential_policy_ref"),
        ({"network_egress_policy_ref": " "}, "network_egress_policy_ref"),
        ({"version_matrix_ref": " "}, "version_matrix_ref"),
        ({"artifact_retention_ref": " "}, "artifact_retention_ref"),
        ({"failure_alerting_ref": " "}, "failure_alerting_ref"),
        ({"required_check_ids": ("agent-card", " ")}, "required_check_ids"),
        ({"required_components": ("external_suite_runner", " ")}, "required_components"),
    ]:
        with pytest.raises(ValueError, match=expected):
            A2AExternalConformanceInvocationPlan(
                **{**valid_kwargs, **override},
            )


def test_a2a_external_conformance_execution_profile_reports_missing_evidence() -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceExecutionProfile,
    )

    profile = A2AExternalConformanceExecutionProfile()

    metadata = profile.readiness_metadata()

    assert metadata["profile"] == "A2AExternalConformanceExecutionProfile"
    assert metadata["probe_name"] == "a2a_external_conformance_execution"
    assert metadata["ready"] is False
    assert metadata["report_present"] is False
    assert metadata["missing_required_checks"] == (
        "agent-card",
        "message-send",
        "message-stream",
        "tasks-resubscribe",
        "push-notification-config",
    )
    assert metadata["missing_components"] == (
        "external_suite_runner",
        "target_endpoint",
        "credential_policy",
        "network_egress_policy",
        "version_matrix",
        "ci_artifact_retention",
        "failure_alerting",
    )
    assert "external report import and normalization" in metadata["sdk_owned"]
    assert "external suite execution" in metadata["deployment_owned"]
    assert profile.readiness_check()["status"] == "failed"


def test_a2a_external_conformance_execution_profile_reports_failed_required_checks() -> None:
    from agentos.channels.a2a_conformance import (
        A2AConformanceFinding,
        A2AConformanceReport,
        A2AExternalConformanceExecutionProfile,
    )

    report = A2AConformanceReport(
        suite="official-a2a-conformance",
        source="external",
        version="2026.06",
        run_id="ci-123",
        target="https://agents.example/a2a",
        findings=(
            A2AConformanceFinding(
                check_id="agent-card",
                title="Agent Card",
                passed=True,
            ),
            A2AConformanceFinding(
                check_id="message-send",
                title="message/send",
                passed=False,
                detail="task id missing",
            ),
            A2AConformanceFinding(
                check_id="message-stream",
                title="message/stream",
                passed=True,
            ),
        ),
    )
    profile = A2AExternalConformanceExecutionProfile(
        report=report,
        configured_components=(
            "external_suite_runner",
            "target_endpoint",
            "credential_policy",
            "network_egress_policy",
            "version_matrix",
            "ci_artifact_retention",
            "failure_alerting",
        ),
        required_check_ids=("agent-card", "message-send", "message-stream"),
    )

    metadata = profile.readiness_metadata()

    assert metadata["ready"] is False
    assert metadata["report_present"] is True
    assert metadata["suite"] == "official-a2a-conformance"
    assert metadata["source"] == "external"
    assert metadata["version"] == "2026.06"
    assert metadata["run_id"] == "ci-123"
    assert metadata["target"] == "https://agents.example/a2a"
    assert metadata["missing_required_checks"] == ()
    assert metadata["failed_required_checks"] == ("message-send",)
    assert profile.readiness_check()["ok"] is False


def test_a2a_external_conformance_execution_profile_marks_ready_when_required_checks_pass() -> None:
    from agentos.channels.a2a_conformance import (
        A2AConformanceFinding,
        A2AConformanceReport,
        A2AExternalConformanceExecutionProfile,
    )

    report = A2AConformanceReport(
        suite="official-a2a-conformance",
        source="external",
        version="2026.06",
        run_id="ci-456",
        target="https://agents.example/a2a",
        findings=(
            A2AConformanceFinding("agent-card", "Agent Card", True),
            A2AConformanceFinding("message-send", "message/send", True),
            A2AConformanceFinding("message-stream", "message/stream", True),
            A2AConformanceFinding("tasks-resubscribe", "tasks/resubscribe", True),
            A2AConformanceFinding(
                "push-notification-config",
                "push notification config",
                True,
            ),
            A2AConformanceFinding("extra-vendor-check", "vendor check", False),
        ),
    )
    profile = A2AExternalConformanceExecutionProfile(
        report=report,
        configured_components=(
            "external_suite_runner",
            "target_endpoint",
            "credential_policy",
            "network_egress_policy",
            "version_matrix",
            "ci_artifact_retention",
            "failure_alerting",
        ),
    )

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["ready"] is True
    assert metadata["required_check_ids"] == (
        "agent-card",
        "message-send",
        "message-stream",
        "tasks-resubscribe",
        "push-notification-config",
    )
    assert metadata["missing_required_checks"] == ()
    assert metadata["failed_required_checks"] == ()
    assert metadata["report_passed"] is False
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True


def test_a2a_external_conformance_execution_profile_rejects_empty_names() -> None:
    import pytest

    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceExecutionProfile,
    )

    with pytest.raises(ValueError, match="probe_name"):
        A2AExternalConformanceExecutionProfile(probe_name=" ")

    with pytest.raises(ValueError, match="required_components"):
        A2AExternalConformanceExecutionProfile(required_components=())

    with pytest.raises(ValueError, match="configured_components"):
        A2AExternalConformanceExecutionProfile(configured_components=("",))

    with pytest.raises(ValueError, match="required_check_ids"):
        A2AExternalConformanceExecutionProfile(required_check_ids=("",))


def test_a2a_external_conformance_execution_record_gate_marks_ready() -> None:
    from agentos.channels.a2a_conformance import (
        A2AConformanceFinding,
        A2AConformanceReport,
        A2AExternalConformanceExecutionRecord,
        A2AExternalConformanceGateReport,
    )

    report = A2AConformanceReport(
        suite="official-a2a-conformance",
        source="external",
        version="2026.06",
        run_id="ci-789",
        target="https://agents.example/a2a",
        findings=(
            A2AConformanceFinding("agent-card", "Agent Card", True),
            A2AConformanceFinding("message-send", "message/send", True),
            A2AConformanceFinding("message-stream", "message/stream", True),
        ),
    )
    record = A2AExternalConformanceExecutionRecord(
        suite="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=("a2a-conformance", "--target", "https://agents.example/a2a"),
        exit_code=0,
        started_at=100.0,
        ended_at=108.25,
        environment="ci",
        artifact_uri="s3://ci/a2a/report.json",
        stdout_summary="3 checks passed",
        stderr_summary="",
        report=report,
        metadata={"commit": "abc123"},
    )

    gate = A2AExternalConformanceGateReport.from_record(
        record,
        required_check_ids=("agent-card", "message-send", "message-stream"),
        required_components=("external_suite_runner", "target_endpoint"),
        configured_components=("external_suite_runner", "target_endpoint"),
    )
    payload = gate.as_dict()

    assert gate.ready is True
    assert gate.status == "ok"
    assert gate.missing_required_checks == ()
    assert gate.failed_required_checks == ()
    assert gate.missing_components == ()
    assert payload["ready"] is True
    assert payload["status"] == "ok"
    assert payload["record"]["suite"] == "official-a2a-conformance"
    assert payload["record"]["command"] == (
        "a2a-conformance",
        "--target",
        "https://agents.example/a2a",
    )
    assert payload["record"]["duration_seconds"] == 8.25
    assert payload["record"]["artifact_uri"] == "s3://ci/a2a/report.json"
    assert payload["report_passed"] is True
    assert payload["no_certification_claim"] is True
    assert "external conformance execution record" in payload["sdk_owned"]


def test_a2a_external_conformance_execution_record_gate_reports_failures() -> None:
    from agentos.channels.a2a_conformance import (
        A2AConformanceFinding,
        A2AConformanceReport,
        A2AExternalConformanceExecutionRecord,
        A2AExternalConformanceGateReport,
    )

    report = A2AConformanceReport(
        findings=(
            A2AConformanceFinding("agent-card", "Agent Card", True),
            A2AConformanceFinding("message-send", "message/send", False),
        ),
        suite="official-a2a-conformance",
        source="external",
    )
    record = A2AExternalConformanceExecutionRecord(
        suite="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=("a2a-conformance",),
        exit_code=2,
        report=report,
    )

    gate = A2AExternalConformanceGateReport.from_record(
        record,
        required_check_ids=("agent-card", "message-send", "message-stream"),
        required_components=("external_suite_runner", "target_endpoint"),
        configured_components=("external_suite_runner",),
    )
    payload = gate.as_dict()

    assert gate.ready is False
    assert gate.status == "failed"
    assert gate.execution_succeeded is False
    assert gate.report_present is True
    assert gate.failed_required_checks == ("message-send",)
    assert gate.missing_required_checks == ("message-stream",)
    assert gate.missing_components == ("target_endpoint",)
    assert payload["failed_required_checks"] == ("message-send",)
    assert payload["missing_required_checks"] == ("message-stream",)
    assert payload["missing_components"] == ("target_endpoint",)
    assert "external suite execution" in payload["deployment_owned"]


def test_a2a_external_conformance_gate_reports_missing_execution_report() -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceExecutionRecord,
        A2AExternalConformanceGateReport,
    )

    record = A2AExternalConformanceExecutionRecord(
        suite="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=("a2a-conformance",),
        exit_code=0,
    )

    gate = A2AExternalConformanceGateReport.from_record(
        record,
        required_check_ids=("agent-card",),
        required_components=("external_suite_runner",),
        configured_components=("external_suite_runner",),
    )

    assert gate.ready is False
    assert gate.report_present is False
    assert gate.missing_required_checks == ("agent-card",)
    assert gate.failed_required_checks == ()


def test_a2a_external_conformance_execution_record_rejects_invalid_values() -> None:
    import pytest

    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceExecutionRecord,
    )

    with pytest.raises(ValueError, match="suite"):
        A2AExternalConformanceExecutionRecord(
            suite=" ",
            target="https://agents.example/a2a",
            command=("a2a-conformance",),
            exit_code=0,
        )

    with pytest.raises(ValueError, match="target"):
        A2AExternalConformanceExecutionRecord(
            suite="official-a2a-conformance",
            target=" ",
            command=("a2a-conformance",),
            exit_code=0,
        )

    with pytest.raises(ValueError, match="command"):
        A2AExternalConformanceExecutionRecord(
            suite="official-a2a-conformance",
            target="https://agents.example/a2a",
            command=("a2a-conformance", " "),
            exit_code=0,
        )

    with pytest.raises(ValueError, match="exit_code"):
        A2AExternalConformanceExecutionRecord(
            suite="official-a2a-conformance",
            target="https://agents.example/a2a",
            command=("a2a-conformance",),
            exit_code=-1,
        )

    with pytest.raises(ValueError, match="ended_at"):
        A2AExternalConformanceExecutionRecord(
            suite="official-a2a-conformance",
            target="https://agents.example/a2a",
            command=("a2a-conformance",),
            exit_code=0,
            started_at=10.0,
            ended_at=9.0,
        )


def test_a2a_external_conformance_cli_runner_imports_report_path(
    tmp_path,
) -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceCliRunner,
        A2AExternalConformanceExecutionRecord,
        A2AExternalConformanceInvocationPlan,
    )

    report_path = tmp_path / "a2a-report.json"
    report_payload = {
        "suite": "official-a2a-conformance",
        "version": "2026.06",
        "runId": "ci-123",
        "target": "https://agents.example/a2a",
        "checks": [
            {"checkId": "agent-card", "title": "Agent Card", "passed": True},
            {
                "checkId": "message-send",
                "title": "message/send",
                "passed": True,
            },
        ],
    }
    script = (
        "import json\n"
        "from pathlib import Path\n"
        f"Path({str(report_path)!r}).write_text("
        f"{json.dumps(report_payload)!r}, encoding='utf-8')\n"
        "print('external suite finished')\n"
    )
    plan = A2AExternalConformanceInvocationPlan(
        suite_id="official-a2a-conformance",
        suite_version="2026.06",
        target="https://agents.example/a2a",
        command=(sys.executable, "-c", script),
        credential_policy_ref="vault://a2a/conformance/client",
        network_egress_policy_ref="egress-policy:a2a-public-https",
        version_matrix_ref="matrix:a2a-1.0",
        artifact_retention_ref="s3://ci/a2a/conformance/",
        failure_alerting_ref="pagerduty:a2a-conformance",
    )

    record = A2AExternalConformanceCliRunner(
        timeout_seconds=10,
        report_path=report_path,
        environment="ci",
        artifact_uri="s3://ci/a2a/conformance/a2a-report.json",
    ).run(plan)

    assert isinstance(record, A2AExternalConformanceExecutionRecord)
    assert record.suite == "official-a2a-conformance"
    assert record.target == "https://agents.example/a2a"
    assert record.command == (sys.executable, "-c", script)
    assert record.exit_code == 0
    assert record.started_at is not None
    assert record.ended_at is not None
    assert record.duration_seconds is not None
    assert record.environment == "ci"
    assert record.artifact_uri == "s3://ci/a2a/conformance/a2a-report.json"
    assert record.stdout_summary == "external suite finished\n"
    assert record.stderr_summary == ""
    assert record.report is not None
    assert record.report.suite == "official-a2a-conformance"
    assert record.report.check_ids == ("agent-card", "message-send")
    assert record.metadata["runner"] == "A2AExternalConformanceCliRunner"
    assert record.metadata["timeout_seconds"] == 10
    assert record.metadata["report_source"] == str(report_path)
    assert record.metadata["no_certification_claim"] is True


def test_a2a_external_conformance_cli_runner_records_timeout() -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceCliRunner,
        A2AExternalConformanceInvocationPlan,
    )

    plan = A2AExternalConformanceInvocationPlan(
        suite_id="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=(
            sys.executable,
            "-c",
            "import time; print('started', flush=True); time.sleep(2)",
        ),
        credential_policy_ref="vault://a2a/conformance/client",
        network_egress_policy_ref="egress-policy:a2a-public-https",
        version_matrix_ref="matrix:a2a-1.0",
        artifact_retention_ref="s3://ci/a2a/conformance/",
        failure_alerting_ref="pagerduty:a2a-conformance",
    )

    record = A2AExternalConformanceCliRunner(
        timeout_seconds=0.01,
    ).run(plan)

    assert record.exit_code == 124
    assert record.execution_succeeded is False
    assert record.started_at is not None
    assert record.ended_at is not None
    assert record.report is None
    assert record.metadata["runner"] == "A2AExternalConformanceCliRunner"
    assert record.metadata["timed_out"] is True
    assert record.metadata["timeout_seconds"] == 0.01
    assert record.metadata["no_certification_claim"] is True
    assert "timed out" in record.stderr_summary


def test_a2a_external_conformance_cli_runner_imports_stdout_json() -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceCliRunner,
        A2AExternalConformanceInvocationPlan,
    )

    report_payload = {
        "suite": "official-a2a-conformance",
        "checks": [
            {"checkId": "agent-card", "title": "Agent Card", "passed": True},
        ],
    }
    plan = A2AExternalConformanceInvocationPlan(
        suite_id="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=(sys.executable, "-c", f"print({json.dumps(report_payload)!r})"),
        credential_policy_ref="vault://a2a/conformance/client",
        network_egress_policy_ref="egress-policy:a2a-public-https",
        version_matrix_ref="matrix:a2a-1.0",
        artifact_retention_ref="s3://ci/a2a/conformance/",
        failure_alerting_ref="pagerduty:a2a-conformance",
    )

    record = A2AExternalConformanceCliRunner().run(plan)

    assert record.exit_code == 0
    assert record.report is not None
    assert record.report.check_ids == ("agent-card",)
    assert record.metadata["report_source"] == "stdout"


def test_a2a_external_conformance_cli_runner_records_nonzero_exit_and_env_keys() -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceCliRunner,
        A2AExternalConformanceInvocationPlan,
    )

    plan = A2AExternalConformanceInvocationPlan(
        suite_id="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=(
            sys.executable,
            "-c",
            "import os, sys; print(os.environ['A2A_TOKEN']); "
            "print('broken', file=sys.stderr); sys.exit(7)",
        ),
        credential_policy_ref="vault://a2a/conformance/client",
        network_egress_policy_ref="egress-policy:a2a-public-https",
        version_matrix_ref="matrix:a2a-1.0",
        artifact_retention_ref="s3://ci/a2a/conformance/",
        failure_alerting_ref="pagerduty:a2a-conformance",
    )

    record = A2AExternalConformanceCliRunner(
        env={"A2A_TOKEN": "secret-token"},
    ).run(plan)

    assert record.exit_code == 7
    assert record.execution_succeeded is False
    assert record.stdout_summary == "[redacted]\n"
    assert record.stderr_summary == "broken\n"
    assert record.metadata["env_keys"] == ("A2A_TOKEN",)
    assert "secret-token" not in repr(record.metadata)
    assert "secret-token" not in record.stdout_summary


def test_a2a_external_conformance_cli_runner_does_not_inherit_host_environment(
    monkeypatch,
) -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceCliRunner,
        A2AExternalConformanceInvocationPlan,
    )

    monkeypatch.setenv("AGENTOS_A2A_HOST_SECRET", "parent-secret")
    plan = A2AExternalConformanceInvocationPlan(
        suite_id="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=(
            sys.executable,
            "-c",
            "import os; print(os.environ.get('AGENTOS_A2A_HOST_SECRET', '<missing>'))",
        ),
        credential_policy_ref="vault://a2a/conformance/client",
        network_egress_policy_ref="egress-policy:a2a-public-https",
        version_matrix_ref="matrix:a2a-1.0",
        artifact_retention_ref="s3://ci/a2a/conformance/",
        failure_alerting_ref="pagerduty:a2a-conformance",
    )

    record = A2AExternalConformanceCliRunner().run(plan)

    assert record.exit_code == 0
    assert record.stdout_summary == "<missing>\n"


def test_a2a_external_conformance_cli_runner_env_is_explicit_allowlist(
    monkeypatch,
) -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceCliRunner,
        A2AExternalConformanceInvocationPlan,
    )

    monkeypatch.setenv("AGENTOS_A2A_HOST_SECRET", "parent-secret")
    plan = A2AExternalConformanceInvocationPlan(
        suite_id="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=(
            sys.executable,
            "-c",
            "import os; "
            "print(os.environ.get('A2A_TOKEN', '<missing>')); "
            "print(os.environ.get('AGENTOS_A2A_HOST_SECRET', '<missing>'))",
        ),
        credential_policy_ref="vault://a2a/conformance/client",
        network_egress_policy_ref="egress-policy:a2a-public-https",
        version_matrix_ref="matrix:a2a-1.0",
        artifact_retention_ref="s3://ci/a2a/conformance/",
        failure_alerting_ref="pagerduty:a2a-conformance",
    )

    record = A2AExternalConformanceCliRunner(
        env={"A2A_TOKEN": "secret-token"},
    ).run(plan)

    assert record.exit_code == 0
    assert record.stdout_summary == "[redacted]\n<missing>\n"
    assert record.metadata["env_keys"] == ("A2A_TOKEN",)


def test_a2a_external_conformance_cli_runner_records_report_import_error(
    tmp_path,
) -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceCliRunner,
        A2AExternalConformanceInvocationPlan,
    )

    report_path = tmp_path / "missing-report.json"
    plan = A2AExternalConformanceInvocationPlan(
        suite_id="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=(sys.executable, "-c", "print('no report')"),
        credential_policy_ref="vault://a2a/conformance/client",
        network_egress_policy_ref="egress-policy:a2a-public-https",
        version_matrix_ref="matrix:a2a-1.0",
        artifact_retention_ref="s3://ci/a2a/conformance/",
        failure_alerting_ref="pagerduty:a2a-conformance",
    )

    record = A2AExternalConformanceCliRunner(
        report_path=report_path,
    ).run(plan)

    assert record.exit_code == 0
    assert record.report is None
    assert record.metadata["report_source"] == str(report_path)
    assert "report_import_error" in record.metadata


def test_a2a_external_conformance_cli_runner_bounds_output_summaries() -> None:
    from agentos.channels.a2a_conformance import (
        A2AExternalConformanceCliRunner,
        A2AExternalConformanceInvocationPlan,
    )

    plan = A2AExternalConformanceInvocationPlan(
        suite_id="official-a2a-conformance",
        target="https://agents.example/a2a",
        command=(
            sys.executable,
            "-c",
            "import sys; print('abcdef'); print('uvwxyz', file=sys.stderr)",
        ),
        credential_policy_ref="vault://a2a/conformance/client",
        network_egress_policy_ref="egress-policy:a2a-public-https",
        version_matrix_ref="matrix:a2a-1.0",
        artifact_retention_ref="s3://ci/a2a/conformance/",
        failure_alerting_ref="pagerduty:a2a-conformance",
    )

    record = A2AExternalConformanceCliRunner(
        stdout_limit=3,
        stderr_limit=4,
    ).run(plan)

    assert record.stdout_summary == "abc"
    assert record.stderr_summary == "uvwx"


def test_a2a_external_conformance_cli_runner_rejects_invalid_settings() -> None:
    import pytest

    from agentos.channels.a2a_conformance import A2AExternalConformanceCliRunner

    with pytest.raises(ValueError, match="timeout_seconds"):
        A2AExternalConformanceCliRunner(timeout_seconds=0)

    with pytest.raises(ValueError, match="stdout_limit"):
        A2AExternalConformanceCliRunner(stdout_limit=-1)

    with pytest.raises(ValueError, match="stderr_limit"):
        A2AExternalConformanceCliRunner(stderr_limit=-1)

    with pytest.raises(ValueError, match="environment"):
        A2AExternalConformanceCliRunner(environment=" ")

    with pytest.raises(ValueError, match="artifact_uri"):
        A2AExternalConformanceCliRunner(artifact_uri=" ")

    with pytest.raises(ValueError, match="env"):
        A2AExternalConformanceCliRunner(env={" ": "secret"})
