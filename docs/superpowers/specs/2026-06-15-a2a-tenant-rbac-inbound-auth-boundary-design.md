# A2A Tenant RBAC Inbound Auth Boundary Design

## Target Conclusion

A2A enterprise deployments need tenant-aware authorization after peer identity
has been authenticated. The SDK should provide a narrow claims-backed tenant
RBAC policy that maps verified JWT/OIDC claims to tenant, role, and scope
attributes, then authorizes operations and resources against declarative tenant
rules. The SDK should not own the tenant directory, user lifecycle, role
assignment source of truth, IdP administration, credential rotation, or
organization synchronization.

## Problem

The SDK already provides:

- bearer-token inbound auth;
- JWT/OIDC claims validation;
- peer allow-lists;
- operation allow-lists;
- task/resource allow-lists.

Those policies are useful for service-to-service authorization, but enterprise
A2A deployments also need tenant isolation. A peer that is valid for tenant A
must not call tenant B resources, and operations such as `tasks/cancel` should
be restricted to roles/scopes with that permission. Today every application
must reimplement the claims extraction and rule evaluation boundary.

## Scope

In scope:

- Add `A2ATenantRbacRule`.
- Add `ClaimsTenantRbacA2AInboundAuthPolicy`.
- Extract tenant ids, roles, and scopes from verified claims.
- Support custom claim names for tenant ids, roles, and scopes.
- Support operation-level and resource-level authorization.
- Support wildcard operations/resources through `*`.
- Keep unauthorized errors generic as `A2AInboundAuthError("unauthorized peer")`.
- Export the new API through `agentos.channels` and top-level `agentos`.
- Update readiness, production docs, skill docs, and roadmap.

Out of scope:

- No database-backed tenant directory.
- No built-in role assignment administration.
- No tenant sync from IdP or enterprise IAM.
- No UI for RBAC management.
- No credential rotation.
- No changes to JWT verification cryptography.
- No changes to runtime query loops.

## API Shape

```python
tenant_policy = ClaimsTenantRbacA2AInboundAuthPolicy(
    claims_policy=OidcClaimsA2AInboundAuthPolicy(...),
    rules=(
        A2ATenantRbacRule(
            tenant_id="tenant_acme",
            operations=("message/send", "tasks/get"),
            roles=("agent-operator",),
        ),
        A2ATenantRbacRule(
            tenant_id="tenant_acme",
            operations=("tasks/cancel",),
            scopes=("a2a:tasks.cancel",),
            resources=(("task", "task_1"),),
        ),
    ),
)
```

The policy implements:

- `authorize(headers)`
- `authorize_operation(headers, operation=...)`
- `authorize_resource(headers, operation=..., task_id=..., resource_type=..., resource_id=...)`

## Claims Mapping

Default claim names:

- tenant ids: `tenant_id`, `tenant`, `tenants`
- roles: `roles`, `role`
- scopes: `scope`, `scp`, `scopes`

Scalar strings become one value. Whitespace-delimited scope strings split into
multiple scopes. Lists/tuples/sets become one value per string item.

## Rule Semantics

A request is allowed when at least one rule matches all required dimensions:

- claim tenant id matches `rule.tenant_id`;
- requested operation is in `rule.operations`, or `*` is present;
- if the rule declares roles, the claims must include at least one role;
- if the rule declares scopes, the claims must include at least one scope;
- if a task id is provided, it must match a declared `("task", task_id)` resource
  unless the rule declares `("*", "*")`, `("task", "*")`, or no resources;
- if a resource type/id is provided, it must match the same wildcard-aware
  resource rules.

Roles and scopes are OR conditions within one rule. Multiple rules are also OR.

## Verification

Tests should prove:

- operation authorization succeeds when tenant and role match;
- authorization rejects a valid peer from the wrong tenant;
- resource authorization requires matching task/resource scope;
- custom claim names are supported;
- scope strings are split correctly;
- error messages remain generic and repr redacts the claims policy;
- public exports include the policy and rule;
- runtime query loops remain free of A2A/auth/RBAC imports.
