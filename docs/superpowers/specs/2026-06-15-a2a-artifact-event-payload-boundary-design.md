# A2A Artifact/Event Payload Boundary Design

Date: 2026-06-15

## Target Conclusion

A2A 1.0 interoperability cannot stop at message parts. The SDK should project
task artifacts and task stream/push events into A2A 1.0 wrapper-object payloads
so peers can consume final outputs and progress updates without knowing
agent-os internal task records.

## External Baseline

The A2A 1.0 protocol uses wrapper-object shapes for stream responses:

- Task status update: `{"statusUpdate": {...}}`
- Task artifact update: `{"artifactUpdate": {...}}`
- Message response: `{"message": {...}}`
- Task response: `{"task": {...}}`

Artifacts are structured results with an artifact id and parts:

- Artifact: `{"artifactId": "...", "parts": [...]}`

The SDK already emits A2A 1.0 message part wrappers for text/file/data. This
phase reuses the same part model inside artifacts and event wrappers.

## Design

- Add an SDK `A2AArtifact` dataclass with `artifact_id`, `parts`, optional name,
  description, and metadata.
- Change `A2ATask.artifacts` from opaque mappings to a tuple of `A2AArtifact`
  values, while accepting legacy mapping artifacts at parser and projection
  boundaries.
- Add artifact serializer/parser helpers that emit and parse A2A 1.0 artifact
  shapes.
- Add `A2ATaskArtifactUpdateEvent` for artifact update stream/push events.
- Change task subscription status update serialization to emit
  `{"statusUpdate": {...}}` instead of legacy `{"kind": "status-update", ...}`.
- Keep inbound parsing compatible with legacy status update payloads.
- Add helper serializers/parsers for one-of stream response payloads so future
  SSE, push, and conformance tests share one boundary.

## Non-Goals

- No external A2A conformance harness.
- No provider/runtime handling of artifact parts.
- No automatic artifact storage, fetching, virus scanning, or MIME validation.
- No full JSON-RPC method expansion beyond existing task subscribe/status and
  push notification routes.
- No QueryLoop, AsyncQueryLoop, planner, or team-runtime coupling.

## Acceptance Criteria

- `A2AArtifact` round-trips through `a2a_artifact_to_dict(...)` and
  `a2a_artifact_from_dict(...)`.
- `A2ATask` serializes artifacts as A2A 1.0 artifact objects with parts.
- Internal task result mappings project to artifact objects instead of raw
  dictionaries.
- `A2ATaskSubscriptionEvent` serializes as `{"statusUpdate": {...}}`.
- Legacy `{"kind": "status-update", ...}` payloads still deserialize.
- `A2ATaskArtifactUpdateEvent` serializes as `{"artifactUpdate": {...}}`.
- Push notification payloads wrap status updates through the same
  `statusUpdate` shape.
- ASGI task subscribe SSE data uses the 1.0 status update wrapper while keeping
  the existing SSE event name stable for local clients.
- Readiness, production docs, skill guidance, and roadmap describe artifact and
  event wrapper parity as present while keeping extension negotiation and
  external conformance as future work.
