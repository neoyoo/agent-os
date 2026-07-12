# Team UI Network Stream Design

## Target Conclusion

Team UI event streams cannot stay process-local if team discussion agents are
used through web frontends or multi-node workers. The SDK should provide a
network-readable replay endpoint and a distributed stream store while keeping
team UI projection outside `QueryLoop`.

## Scope

This slice adds:

- JSON replay endpoint: `GET /v1/teams/{team_id}/ui-events`
- Optional `after_event_id` query cursor
- Optional `limit` query bound
- `PostgresTeamUiStreamStore`
- Postgres migration for durable UI events

This slice does not add live SSE follow. The endpoint is replay-only so it can
be safely composed with polling or app-owned SSE until a later phase adds live
subscription semantics.

## Endpoint Contract

`GET /v1/teams/{team_id}/ui-events`

Response:

```json
{
  "team_id": "team_1",
  "events": [
    {
      "event_id": 1,
      "team_id": "team_1",
      "kind": "team_created",
      "payload": {},
      "created_at": 1.0
    }
  ],
  "next_after_event_id": 1
}
```

Query parameters:

- `after_event_id`: optional non-negative integer cursor. Events returned must
  have `event_id > after_event_id`.
- `limit`: optional integer from 1 to 1000. Default is endpoint configured
  `team_ui_event_default_limit`.

Errors:

- `404` when the app has no UI stream store configured.
- `400` for invalid cursor or limit.
- Auth policy and rate limiter still apply at the app boundary.

## Store Contract

`PostgresTeamUiStreamStore` implements `TeamUiStreamStore`.

- Event ids are monotonic per team.
- `append()` uses a per-team sequence row so concurrent appends on different
  nodes cannot reuse event ids.
- `list_events()` returns deterministic ascending event ids.
- Payload is stored as JSONB and serialized through existing
  `team_ui_event_to_dict()` / `team_ui_event_from_dict()` helpers.

## Security And Privacy

- Endpoint is behind existing `AsgiAgentApp` auth and rate limit checks.
- Team ids and query params are treated as input and validated before use.
- SQL uses parameterized queries.
- UI event payloads are intentionally projections. They should not include
  local workspace roots or provider credentials.

## Acceptance

- ASGI JSON endpoint replays events after a cursor and returns
  `next_after_event_id`.
- Endpoint returns `404` when no stream store is configured.
- Endpoint rejects invalid `after_event_id` and `limit`.
- Postgres store round-trips events, orders per-team event ids, filters by team,
  and migration defines required tables/indexes.
- Runtime loop files do not import `TeamUi*` types.
