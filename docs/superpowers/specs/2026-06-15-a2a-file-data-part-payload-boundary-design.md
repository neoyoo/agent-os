# A2A File/Data Part Payload Boundary Design

Date: 2026-06-15

## Target Conclusion

A2A 1.0 interoperability cannot stop at text-only messages. The SDK should
represent file bytes, file URLs, and structured data as first-class A2A message
parts and emit the A2A 1.0 wrapper-object payload shape, while keeping full
artifact/event parity and external conformance testing for later phases.

## External Baseline

The A2A 1.0 protocol uses the JSON member name as the part discriminator:

- Text part: `{"text": "..."}`
- File bytes part: `{"raw": "...", "filename": "...", "mediaType": "..."}`
- File URL part: `{"url": "...", "filename": "...", "mediaType": "..."}`
- Data part: `{"data": {...}, "mediaType": "application/json"}`

Legacy pre-1.0 payloads used `kind` plus nested `file` or `data` members. The
SDK should not emit `kind`, but inbound parsers may accept legacy payloads for
transition compatibility.

## Design

- Extend `A2AMessagePart` with file and data variants.
- Add constructors for text, file bytes, file URL, and structured data.
- Serialize parts using the A2A 1.0 wrapper-object shape.
- Parse both A2A 1.0 file/data wrappers and legacy `kind: file/data` payloads.
- Enforce a single payload discriminator per part so malformed peer messages
  fail before reaching runtime or application logic.
- Keep the change inside `agentos.channels.a2a_operations`; QueryLoop,
  AsyncQueryLoop, planner, and team runtime must remain unaware of A2A payload
  details.

## Non-Goals

- No artifact update event parity in this phase.
- No streaming event wrapper parity in this phase.
- No binary decoding, MIME validation, file fetching, or malware scanning.
- No automatic runtime injection of file/data parts into provider messages.
- No external A2A conformance harness.

## Acceptance Criteria

- `A2AMessagePart.from_file_bytes(...)` serializes to `raw` plus optional
  `filename` and `mediaType`.
- `A2AMessagePart.from_file_url(...)` serializes to `url` plus optional
  `filename` and `mediaType`.
- `A2AMessagePart.from_data(...)` serializes to `data` and `mediaType`.
- `a2a_message_part_from_dict(...)` parses current file/data wrappers.
- `a2a_message_part_from_dict(...)` parses legacy file/data parts for inbound
  compatibility.
- Ambiguous parts with more than one payload discriminator are rejected.
- Readiness and SDK guidance describe file/data part parity as available, while
  artifact/event parity, extension negotiation, and external conformance remain
  roadmap work.
