# RealmSpace agent architecture

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
