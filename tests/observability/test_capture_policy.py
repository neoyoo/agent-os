from agentos.observability.config import (
    CapturePolicy,
    default_redactor,
    json_attribute,
)


def test_metadata_policy_does_not_capture_payloads_by_default() -> None:
    policy = CapturePolicy.metadata_only()

    assert policy.mode == "metadata"
    assert policy.capture_system is False
    assert policy.capture_messages is False
    assert policy.capture_tool_schemas is False
    assert policy.capture_provider_output is False
    assert policy.capture_tool_arguments is False
    assert policy.capture_tool_result is False


def test_redacted_policy_masks_secret_like_values() -> None:
    policy = CapturePolicy.redacted()
    payload = {
        "Authorization": "Bearer sk-test-secret",
        "nested": {
            "api_key": "sk-ant-api03-secret",
            "content": "token=pk-lf-public",
        },
    }

    redacted = policy.redactor(payload)

    assert redacted == {
        "Authorization": "[REDACTED]",
        "nested": {
            "api_key": "[REDACTED]",
            "content": "token=[REDACTED]",
        },
    }


def test_full_local_policy_captures_payloads_with_length_limit() -> None:
    policy = CapturePolicy.full_for_local_development(max_string_length=8)

    assert policy.mode == "full"
    assert policy.capture_system is True
    assert policy.capture_messages is True
    assert policy.capture_tool_schemas is True
    assert policy.capture_provider_output is True
    assert policy.capture_tool_arguments is True
    assert policy.capture_tool_result is True
    assert json_attribute({"text": "1234567890"}, policy=policy) == '{"text":"12345678..."}'


def test_default_redactor_masks_private_key_blocks() -> None:
    value = "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"

    assert default_redactor(value) == "[REDACTED]"
