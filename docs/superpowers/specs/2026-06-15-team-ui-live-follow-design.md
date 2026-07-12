# Team UI Live Follow Design

## Target Conclusion

Team UI replay alone still forces web frontends to poll. The SDK should provide
a live SSE/follow endpoint over the existing `TeamUiStreamStore` cursor model,
without coupling `QueryLoop` to team or UI semantics.

## Scope

This slice adds:

- `GET /v1/teams/{team_id}/ui-events/stream`
- SSE replay from `Last-Event-ID` or `after_event_id`
- bounded follow loop over `TeamUiStreamStore.list_events()`
- heartbeat comments
- configurable idle timeout and poll interval

This slice does not add Redis pub/sub notification. The follow loop is
store-neutral and works with both `InMemoryTeamUiStreamStore` and
`PostgresTeamUiStreamStore`; later deployment profiles can add a notify-backed
adapter without changing the frontend contract.

## SSE Contract

Endpoint:

```text
GET /v1/teams/{team_id}/ui-events/stream
```

Cursor rules:

- Prefer `Last-Event-ID` when present.
- Otherwise use query `after_event_id`.
- Cursor must be a non-negative integer.
- SSE event `id` is the numeric `TeamUiEvent.event_id`.

SSE event:

```text
id: 2
event: team_ui_event
data: {"event_id":2,"team_id":"team_1","kind":"message_appended","payload":{},"created_at":2.0}
```

## Security

- Existing `AsgiAgentApp` auth policy applies before the endpoint.
- Existing rate limiter applies under `team:{team_id}:ui-events:stream`.
- Query and header cursors are validated.
- Payloads come from `TeamUiEvent` projections and should not include workspace
  roots or secrets.

## Acceptance

- Stream replays events after cursor.
- Stream follows new events appended while the connection is open.
- `Last-Event-ID` resumes correctly.
- Missing stream store returns `404`.
- Invalid cursor returns `400`.
- Runtime loop files do not import `TeamUi*` types.
