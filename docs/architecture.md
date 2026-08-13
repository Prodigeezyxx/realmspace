# realmspace architecture

Two halves that meet at the event bus:

```
 EDGE (one laptop, works offline)

 camera → perception (YOLO + ByteTrack)          offline buffer + replay
            │ perception.detection
            ▼
      ┌──────────────────────────────┐
      │  event_log (Postgres)        │  append-only · idempotent · replayable
      └──────────────────────────────┘
            │                    ▲
            │ poll seq > cursor  │ spatial.zone_enter / zone_exit / dwell
            ▼                    │
      ┌──────────┐         ┌──────────────┐
      │ tracker  ├────────►│  the bus     │
      └──────────┘         └──────┬───────┘
                                  │
                           ┌──────▼────────┐
                           │ graph writer  │
                           └──────┬────────┘
                                  ▼
                    ┌──────────────────────────┐
                    │  graph (Neo4j)           │  Person / Zone / edges
                    └──────────────────────────┘
                                  │
                       FastAPI ───┴─── Next.js dashboard
```

## Backend (`backend/`)

- **Bus** — `app/repository.py`: the only SQL against `event_log`. Append is
  idempotent on a producer-assigned `event_id`; reads are cursor-based
  (`seq > last_seq`). Spec: [`event-bus-spec.md`](./event-bus-spec.md).
- **Graph** — `app/graph/repository.py`: the only Cypher in the system, every
  function tenant-scoped. Schema and constraints in `app/graph/schema.py`,
  applied by `app/graph/migrations.py`. Spec: [`data-model.md`](./data-model.md).
- **Consumers** — `app/consumers/`: `base.py` holds the one poll → handle →
  advance → dead-letter loop, and everything else subclasses it — `tracker`,
  `graph_writer`, `identity`, `reanonymise`, `rules`, `dispatch`, `broadcast`,
  registered in that order in `run.py`. They run as asyncio tasks started by the
  FastAPI lifespan and reported by `/health`. Adding a feature to this system
  almost always means adding one of these rather than a route.
- **Derived ids** — `app/consumers/ids.py`: a consumer that *produces* events
  derives each `event_id` from what caused it, so replay is a no-op instead of
  double-counting.

**Two stores on purpose.** Postgres holds the log and timeseries; Neo4j holds
the graph. They cannot share a transaction, which is exactly why every graph
write is a `MERGE` and every consumer must be idempotent.

**Two independent buses, not one.** `dashboard/src/lib/event-bus.ts` stays as
the browser-side fan-out for live UI; the Postgres log is the durable backend
behind it (`event-bus-spec.md` §7). Same event shapes.

**Where it runs.** `docker compose up` from the repo root brings up Postgres,
Neo4j and the backend together (`Dockerfile`, `docker-compose.yml`,
`docker/entrypoint.sh`) — the entrypoint applies both migration systems before
serving. Database ports are deliberately non-default so the stack coexists with
a brew-installed Postgres and Neo4j rather than competing for a port.

**Auth** — `app/auth/`: JWT for people, API keys for devices, verified
locally so the edge box authenticates with no network. `tenant_id` is derived
from the credential; there is no parameter to supply one. Postgres row-level
security is the remaining piece.

---

## Dashboard agent architecture

- **Agent** — `dashboard/src/agents/definitions/*.ts`: trigger event, ordered skills, outputs (`stream` | `webhook` | `log`).
- **Skill** — `dashboard/src/skills/*.ts`: pure `run(input, config)`; no React, no Claude.
- **Registry** — `agents/registry.ts`: `listAgents()`, `getAgent()`, `setAgentEnabled()`.
- **Runtime** — `agents/runtime.ts`: runs skill chain, emits to event bus.
- **Engine** — `lib/agent-engine.ts`: `dispatchTrigger()`, `processFrameTracks()`, `runNlqQuery()`.
- **Bus** — `lib/event-bus.ts`: in-memory pub/sub; hooks in `hooks/useAgentStream.ts`.

## Trigger events

| Event | Agents |
|-------|--------|
| `frame_tracks` | zone, heatmap |
| `person_dwell_exceeded` | dwell |
| `zone_threshold_exceeded` | alert |
| `nlq_query` | nlq |
| `session_ended` | report |
| `twin_layout_loaded` | layout |

Swap registry implementation later; skills and UI stay the same.

## Live session (background)

- **Runtime** — `lib/live-session/detector-runtime.ts` module singleton; inference survives route changes.
- **Store** — `lib/live-session/store.ts` holds stats, twin avatars, heatmap (synced with feed).
- **Provider** — `LiveSessionProvider` hosts hidden sensor video; `/live` mirrors stream + overlay.

## Onboarding

- **Prefabs** — Step 3 of `/sessions/new`: `PrefabPicker` → `prefabId` + `boothSize` + zone polygons on the session.
- **Apply** — `lib/prefabs/apply.ts` maps templates into session zones for twin + agents.
