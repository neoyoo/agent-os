from dataclasses import FrozenInstanceError

import pytest

from agentos.providers import ProviderInputItem, TextPart
from agentos.runtime.continuation import (
    ContinuationNotice,
    project_continuation_data,
)


def test_continuation_notice_is_a_frozen_validated_value() -> None:
    notice = ContinuationNotice(
        kind="task_completed",
        subject_id="task_123",
        action="check_agent_tasks",
    )

    with pytest.raises(FrozenInstanceError):
        notice.subject_id = "task_456"  # type: ignore[misc]
    with pytest.raises(ValueError, match="unsupported continuation notice kind"):
        ContinuationNotice(
            kind="unknown",  # type: ignore[arg-type]
            subject_id="task_123",
            action="check_agent_tasks",
        )


@pytest.mark.parametrize("field", ["subject_id", "action"])
def test_continuation_notice_requires_non_empty_string_fields(field: str) -> None:
    values = {
        "kind": "team_message",
        "subject_id": "message_123",
        "action": "team_read_messages",
    }
    values[field] = ""

    with pytest.raises(ValueError, match=field):
        ContinuationNotice(**values)  # type: ignore[arg-type]


def test_continuation_projection_matches_the_frozen_contract() -> None:
    item = project_continuation_data(
        (
            ContinuationNotice(
                kind="task_completed",
                subject_id="task_123",
                action="check_agent_tasks",
            ),
        )
    )

    assert item == ProviderInputItem.continuation_data(
        '<continuation-data protocol="agentos.continuation" version="1.0"\n'
        '    origin="runtime" authority="context-data" persistence="ephemeral"\n'
        '    visibility="internal">\n'
        '  <notice kind="task_completed" subject-id="task_123"\n'
        '      action="check_agent_tasks"/>\n'
        '</continuation-data>\n'
    )


def test_continuation_projection_preserves_notice_order_and_escapes_attributes() -> None:
    notices = (
        ContinuationNotice(
            kind="team_message",
            subject_id='message<&>"\'\r\n\t',
            action='team<&>"\'\r\n\t',
        ),
        ContinuationNotice(
            kind="task_completed",
            subject_id="task_2",
            action="check_agent_tasks",
        ),
    )

    first = project_continuation_data(notices)
    second = project_continuation_data(notices)

    assert first == second
    assert first.content == (
        TextPart(
            '<continuation-data protocol="agentos.continuation" version="1.0"\n'
            '    origin="runtime" authority="context-data" persistence="ephemeral"\n'
            '    visibility="internal">\n'
            '  <notice kind="team_message" '
            'subject-id="message&lt;&amp;&gt;&quot;&apos;&#xD;&#xA;&#x9;"\n'
            '      action="team&lt;&amp;&gt;&quot;&apos;&#xD;&#xA;&#x9;"/>\n'
            '  <notice kind="task_completed" subject-id="task_2"\n'
            '      action="check_agent_tasks"/>\n'
            '</continuation-data>\n'
        ),
    )


def test_continuation_projection_rejects_empty_or_untyped_notices() -> None:
    with pytest.raises(ValueError, match="at least one notice"):
        project_continuation_data(())
    with pytest.raises(TypeError, match="ContinuationNotice"):
        project_continuation_data((object(),))  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", ["\x00", "\x01", "\x0b", "\ufffe", "\uffff", "\ud800"])
def test_continuation_projection_rejects_invalid_xml_characters(invalid: str) -> None:
    notice = ContinuationNotice(
        kind="team_message",
        subject_id=f"message_{invalid}",
        action="team_read_messages",
    )

    with pytest.raises(ValueError, match="invalid XML character") as error:
        project_continuation_data((notice,))

    assert invalid not in str(error.value)
