# A2A Conformance Harness Design

> Date: 2026-06-15
> Phase: 46
> Branch: `review/agentos-sdk-architecture-20260611`

## Target Conclusion

A2A cannot rely only on unit tests to prove it "looks like the spec". The SDK
needs a stable self-conformance harness that composes Agent Card, JSON-RPC
envelope, protocol version, part wrapper, artifact/event wrapper, and extension
negotiation checks into repeatable reports. Official external suite integration
remains future work.

## External Baseline

The A2A latest specification defines a discovery-first, operation-oriented
protocol:

- Agent Cards describe protocol version, URL/interface metadata, capabilities,
  skills, security, and extensions.
- JSON-RPC requests use a `jsonrpc`, `id`, `method`, and `params` envelope.
- HTTP service parameters include `A2A-Version` and `A2A-Extensions`.
- Message parts use wrapper shapes such as `{"text": "..."}`, `{"raw": ...}`,
  `{"url": ...}`, and `{"data": ...}` rather than the removed legacy `kind`
  discriminator.
- Artifacts use `artifactId` plus `parts`.
- Task update events are wrapped as `statusUpdate` and `artifactUpdate`.
- Unsupported protocol versions map to `VersionNotSupportedError`; required
  extension failures map to extension-support-required.

This phase does not attempt official certification. It turns the SDK's known
A2A protocol surfaces into a structured local report so every release can prove
that the implemented boundary remains internally coherent.

## SDK Boundary

Add `agentos.channels.a2a_conformance`.

Responsibilities:

- provide immutable check/finding/report dataclasses;
- run a deterministic set of SDK-owned A2A self-checks;
- use existing serializers and policies instead of re-implementing protocol
  logic;
- return structured report data instead of raising on the first failure;
- keep runtime loops unaware of A2A semantics.

Recommended API:

- `A2AConformanceCheck`
- `A2AConformanceFinding`
- `A2AConformanceReport`
- `A2AConformanceHarness`

The harness should accept an `A2AAgentCard` and optional sample request,
response, status event, artifact event, protocol headers, and extension policy.
When optional samples are omitted, it should use SDK-generated default samples
that cover the supported A2A operation boundary.

## Checks

Initial checks:

- Agent Card serialization contains required public fields:
  `protocolVersion`, `name`, `description`, `url`, `version`, `capabilities`,
  `defaultInputModes`, `defaultOutputModes`, and `skills`.
- Agent Card capability extensions serialize under `capabilities.extensions`.
- Message text/file/data parts emit A2A 1.0 wrapper shapes and no `kind`.
- Artifacts emit `artifactId` and part wrappers.
- Status and artifact events emit `statusUpdate` and `artifactUpdate`.
- Operation requests emit the JSON-RPC envelope with `jsonrpc`, `method`,
  `params`, and optional `id`.
- Protocol version policy can emit/validate `A2A-Version` semantics locally.
- Extension negotiation policy emits/parses `A2A-Extensions`.
- Required-extension failures can be represented as the A2A `-32008` error
  mapping.

## Non-Goals

- No network calls.
- No official A2A compliance claim.
- No external conformance runner integration.
- No protocol implementation duplicate.
- No QueryLoop, AsyncQueryLoop, planner, or team-runtime coupling.
- No runtime policy changes.

## Acceptance Criteria

- A valid card and generated payload samples produce a passing report.
- Legacy `kind` output or missing required card values produce failed findings
  without raising.
- Required-extension failure mapping is represented in report details.
- Public exports include the harness/report/check/finding types.
- Readiness, production docs, skill guidance, and roadmap describe SDK-owned
  self-conformance as available while keeping external conformance and
  deployment trust governance as roadmap work.
- Runtime boundary scan finds no A2A/planner/channel coupling in query loops.
