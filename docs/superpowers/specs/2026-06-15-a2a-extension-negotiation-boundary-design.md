# A2A Extension Negotiation Boundary Design

> Date: 2026-06-15
> Phase: 45
> Branch: `review/agentos-sdk-architecture-20260611`

## Target Conclusion

A2A payload parity moves the main interoperability risk from JSON shape to
optional semantics. The SDK must own a narrow extension negotiation boundary so
peers can declare, require, reject, or degrade optional capabilities without
putting hidden assumptions into `QueryLoop`, team runtime, or application code.

## External Baseline

The A2A 1.0 specification models capability extensions on the Agent Card under
`capabilities.extensions`. Each extension has a URI, optional description,
optional params, and a `required` flag. HTTP operation calls use the
`A2A-Extensions` header to declare extension support for the current exchange.
If a server declares a required extension and a peer does not advertise support,
the operation should fail with the A2A extension-support-required JSON-RPC error
mapping.

This phase keeps the implementation intentionally small:

- support comma-separated `A2A-Extensions` parsing and emission;
- validate required server-side Agent Card extensions before operation handling;
- allow clients to advertise only configured supported extensions;
- fail locally when a remote card requires an unsupported extension;
- keep optional unsupported extensions degradable by default;
- do not add extension-specific behavior to runtime loops.

## SDK Boundary

Add `A2AExtensionNegotiationPolicy` in `agentos.channels.a2a_operations`.

Responsibilities:

- normalize configured extension URI sets;
- parse inbound `A2A-Extensions` headers;
- expose accepted/unsupported/missing negotiation details;
- produce outbound `A2A-Extensions` headers for a peer `A2AAgentCard`;
- raise `A2AExtensionNegotiationError` when required extensions cannot be
  satisfied;
- map server-side negotiation failures to JSON-RPC `-32008`.

`A2AOperationServer` uses the policy for:

- `message/send`;
- `tasks/get`;
- `tasks/cancel`;
- push notification config routes.

`A2AOperationClient` uses the policy for:

- `message/send`;
- task lifecycle requests;
- push notification config requests.

## Non-Goals

- No official extension registry.
- No extension-specific payload transformation.
- No tenant-level extension governance.
- No external conformance suite execution.
- No coupling to `QueryLoop` or `AsyncQueryLoop`.

## Compatibility

Default behavior remains permissive for existing users:

- if no required extensions are configured, inbound calls without
  `A2A-Extensions` still succeed;
- clients send no `A2A-Extensions` unless configured with supported extension
  URIs and the peer card declares matching extensions;
- optional peer extensions are ignored unless configured as supported.

## Acceptance Criteria

- Required inbound server extensions missing from `A2A-Extensions` return
  JSON-RPC error `-32008`.
- Inbound optional unsupported extension requests degrade without failing by
  default.
- Outbound clients add `A2A-Extensions` for configured supported extensions
  declared by a peer card.
- Outbound clients raise before network I/O when a peer requires an unsupported
  extension.
- Public exports include the new policy and error.
- Readiness/docs/skill guidance no longer list extension negotiation as a
  remaining A2A gap.
- Runtime boundary scan finds no A2A/planner/channel coupling in query loops.
