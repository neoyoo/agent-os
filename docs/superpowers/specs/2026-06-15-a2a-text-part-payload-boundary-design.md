# A2A Text Part Payload Boundary Design

Date: 2026-06-15

## Target Conclusion

After adding `A2A-Version: 1.0`, the SDK should stop emitting the legacy
`kind` discriminator for text parts in new A2A operation payloads. The next
safe conformance slice is to serialize text parts in the A2A 1.0 wrapper-object
shape while continuing to accept the legacy shape on inbound requests.

## Design

- Change `a2a_message_part_to_dict(...)` for text parts to emit
  `{"text": value}`.
- Keep `a2a_message_part_from_dict(...)` backward compatible with both
  `{"text": value}` and `{"kind": "text", "text": value}`.
- Leave file parts, data parts, artifacts, and streaming event wrapper parity
  for later phases.
- Keep the change inside `agentos.channels.a2a_operations`; runtime and planner
  boundaries must remain unaware of A2A payload details.

## Non-Goals

- No file/data part support in this slice.
- No full message/artifact conformance harness.
- No public API surface change.
- No automatic payload downgrade for old peers.

## Acceptance Criteria

- A message text part serializes as `{"text": "..."}`.
- Legacy text parts with `kind: "text"` still deserialize.
- `A2AOperationClient` sends `message/send` payloads using the new text part
  shape.
- Existing server tests still accept old payloads.
- Readiness and SDK guidance describe text-part 1.0 payload parity as present,
  while keeping file/data/artifact/event parity as roadmap work.
