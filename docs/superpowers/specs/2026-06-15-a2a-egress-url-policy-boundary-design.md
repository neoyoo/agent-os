# A2A Egress URL Policy Boundary Design

## Target Conclusion

A2A production deployments should not rely on ad hoc HTTPS checks for outbound
metadata, discovery, key, and peer-operation calls. The SDK should provide one
small, injectable egress URL policy boundary that can be reused by Agent Card
resolution, OIDC discovery, JWKS trust stores, JWT verifiers, operation clients,
and the internal task bridge. The policy should enforce HTTPS/public-host checks
and optional exact-host or domain-suffix allow-lists before transport calls.
DNS pinning, enterprise egress proxies, CA rollout, credential rotation, and
tenant RBAC remain deployment/profile responsibilities.

## Problem

Phase 49 closed the RS256/JWKS JWT verification boundary, but the current A2A
outbound paths still do not share a common egress policy:

- `A2ACardResolver` fetches well-known Agent Cards from configured URLs.
- `OidcDiscoveryMetadataProvider` fetches OIDC metadata and accepts a discovered
  `jwks_uri`.
- `JwksA2ACardTrustStore` and `JwksA2AJwtVerifier` fetch JWKS documents.
- `A2AOperationClient` and `A2AAdapter` call peer operation/task endpoints.
- Push notifications already have webhook URL policies, but those policies are
  scoped to webhook config objects and are not reusable for discovery/JWKS/client
  calls.

This leaves production operators with only documentation for DNS/egress control.
The SDK should not become a network security appliance, but it should provide a
testable point where deployments can reject unsafe or untrusted outbound URLs
before any HTTP transport executes.

## Scope

In scope:

- Add `A2AEgressPolicyError`.
- Add `A2AEgressUrlPolicy` protocol.
- Add `PublicHttpsA2AEgressUrlPolicy`.
- Add `HostAllowListA2AEgressUrlPolicy`.
- Add optional `egress_url_policy` injection to:
  - `A2ACardResolver`
  - `JwksA2ACardTrustStore`
  - `OidcDiscoveryMetadataProvider`
  - `JwksA2AJwtVerifier`
  - `A2AAdapter`
  - `A2AOperationClient`
- Validate configured and discovered outbound URLs before transport calls.
- Export the new public API from `agentos.channels` and top-level `agentos`.
- Update readiness docs and the agent-os skill to describe the boundary.

Out of scope:

- DNS resolution or DNS pinning.
- Proxy configuration.
- CA bundle management.
- Tenant RBAC decisions.
- Replacing push notification URL policy objects.
- Making runtime loops aware of A2A or egress policy.

## Policy Semantics

`PublicHttpsA2AEgressUrlPolicy`:

- Requires `https://`.
- Requires a hostname.
- Rejects `localhost` and `*.localhost`.
- Rejects literal IP hosts that are private, loopback, link-local, multicast,
  unspecified, or reserved.
- Does not resolve DNS names.

`HostAllowListA2AEgressUrlPolicy`:

- Delegates to a base policy first.
- Allows exact host matches.
- Allows domain suffix matches for the suffix itself and its subdomains.
- Does not allow suffix tricks such as `trusted.example.evil.com`.

## Compatibility

The new policy is opt-in for clients and resolvers so existing local or internal
HTTP test setups do not break. Existing JWKS/OIDC constructors keep their current
HTTPS validation and gain optional allow-list/public-host enforcement.

## Verification

Tests should prove:

- Public HTTPS policy accepts normal HTTPS hostnames and rejects localhost,
  private literal IPs, and non-HTTPS URLs.
- Host allow-list policy accepts exact and suffix matches and rejects suffix
  confusion.
- Resolver, OIDC discovery, JWKS card trust, JWKS JWT verification, operation
  client, and internal A2A adapter invoke the policy before transport calls.
- Runtime loop files do not import or mention the new A2A egress boundary.
