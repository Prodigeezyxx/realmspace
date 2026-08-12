# ADR 002 — Rules are data, and there is one evaluator

**Status:** Accepted (2026-08-11) · **Implemented on this track (2026-08-12)** —
`backend/app/consumers/rules.py` (the evaluator, with all four corrections
below), `consumers/dispatch.py` + `app/actions/` (the actions), `app/routers/
rules.py` (authoring), and `dashboard/src/agents/presets.ts` + `preview.ts` (the
presets and the dry run). Two things this document did not anticipate are
recorded under "What implementing it changed", at the end.
**Context:** `roadmap.md` Phase 3 — "ADR-002: rule spec as JSON data
(trigger/condition/action) + one evaluator in the edge engine + browser preview
reuse. Existing browser agent runtime becomes *simulation/preview* of edge rules
— no split-brain."
**Deciders:** `antoniorobles/phase1` (`[neo4j-track]`), written against the
evaluator already shipped on `floats-agent` (`backend/app/rules.py`, 2026-08-10).

> **On ADR 001.** It lives on the `floats-agent` branch and is not visible from
> here. It decides the *graph store* for that track (relational tables in the bus
> DB, Neo4j deferred) and neither settles nor is settled by this one: a rule spec
> is a wire contract, not a storage choice. See `CHANGELOG.md` on the two tracks.

## Decision

**A rule is a JSON document, not code.** One shape, stored per tenant, evaluated
by exactly one evaluator on the edge. The browser does not implement rules; it
*previews* them by running the same document against local events.

The shape is the one `floats-agent` already ships, adopted verbatim so the two
tracks do not end up with two rule languages:

```json
{
  "ruleId": "r_entry_crowd",
  "tenantId": "t_floats",
  "name": "Entrance crowding → ping ops",
  "triggerType": "spatial.dwell",
  "triggerZoneId": "z_entry",
  "condition": { "type": "threshold", "count": 5, "windowSec": 30,
                 "zoneId": "z_entry", "minDwellSec": 30 },
  "action":    { "type": "slack", "channel": "#ops", "message": "5 at entrance" },
  "enabled": true,
  "cooldownSec": 60
}
```

- `triggerType` is any type registered in `event-bus-spec.md` §3 — **not** a
  closed enum. The taxonomy is additive-only, and a rule spec that cannot name
  `intent.scored` or `spatial.tagged` the day those producers land would force a
  spec migration to use them, which is the tax Phase 3's pre-registration was
  meant to avoid.
- `condition.type` is `threshold` | `any` | `none`.
- `action.type` is `slack` | `webhook` | `screen_swap` | `staff_prompt` | `log`.
- camelCase on the wire, as with every other contract here
  (`dashboard/src/lib/contracts/events.ts` is canonical for the browser).

## Why data rather than code

Three reasons, in the order they bite:

1. **An operator writes rules, not an engineer.** The composer UI is
   plain-English → spec → operator confirms. That is only possible if a rule is
   a value the UI can build, show back, and store.
2. **A rule must be inspectable after it fires.** `rule.fired` cites a
   `ruleId`, and answering "why did this fire?" means reading the document as it
   was, not reasoning about a deployed function.
3. **One evaluator, two runtimes.** The same document has to run on the edge and
   in the browser preview. Two implementations of a rule *language* stay in sync
   for about a week; two runners of one *document* diverge only if the language
   changes.

## The evaluator is a bus consumer

On this track it subclasses `backend/app/consumers/base.py` rather than hooking
the bus callback directly. That is not a style preference — the base loop
already provides cursor-per-tenant, per-event cursor advance, idempotent
reprocessing, exponential backoff and dead-lettering, all of which Phase 3's
acceptance criteria require of the rules engine, and all of which the HITL
review screen (`/ops`, `GET /v1/dead-letters`) already surfaces.

`< 3s` from event to action is the SLA (`event-bus-spec.md` §4).

## Four things the spec pins that the shipped evaluator does differently

Recorded because they are the difference between a rule engine and a *replayable*
rule engine, and because whichever track is adopted inherits them.

1. **The window is derived from the bus, not held in memory.** `rules.py` keeps
   `_event_windows: dict[str, list[dict]]`, trimmed at 200 entries. A restart
   mid-session empties it, so a rule that would have fired does not, and a
   replay of the same events produces a different set of firings. The window is
   a `seq`-ranged read over the log, which is already indexed
   `(tenant_id, session_id, seq)`.
2. **Cooldown runs on event time.** `_cooldown_active` compares
   `datetime.now(timezone.utc)` against `last_fired_at`. Wall clock makes a
   replay produce different output from the original run — the same reason the
   tracker's dropout sweep is on event time (`event-bus-spec.md`, "a wall-clock
   timer … would make a replay produce different events from the original run,
   which is a worse trade"). Cooldown compares `occurredAt` to the `occurredAt`
   of the last firing.
3. **`rule.fired` carries a derived `event_id`.** Via `consumers/ids.py`, from
   `("rules", tenant_id, session_id, rule_id, triggering_seq)`. A random id
   means a replayed input mints a new output the bus cannot dedupe, and the
   action fires twice. **Per-dispatch idempotency keys on that `event_id`**, as
   Phase 3 requires, so a retry after a timeout cannot double-post.
4. **`condition.type: "none"` needs a close-out, not an arrival.** Evaluated
   when an event arrives, "none" asks whether the window is empty at the moment
   something happened — nearly a contradiction. It means *nothing happened for
   windowSec*, which can only be judged at a boundary: the next event after the
   window, or `session.ended`. Until that is implemented, a `none` rule is
   accepted and marked unevaluated rather than silently never firing.

## The browser stops owning rules

`dashboard/src/agents/` today holds eight `AgentDefinition` objects
(`definitions/dwell.ts` and friends) with their own trigger vocabulary —
`{ event: "person_dwell_exceeded", config: { thresholdSec: 30 } }`. That is a
second rule language, and it is the split-brain the roadmap names.

The definitions become **presets** that compile to the spec above, and
`agents/runtime.ts` becomes a preview runner: same JSON, local events, no
dispatch — the dry-run an operator sees before saving. Nothing in the browser
decides whether a rule fires in production.

## Consequences

- Rules persist in the same SQL database as the bus (a `rules` table), even on
  this track, where the *graph* is Neo4j. Rules are configuration, not graph:
  they are read by an evaluator that is already holding a SQL session, and a
  rule surviving a restart must not depend on the graph being up.
- `rule.fired` is already in the taxonomy and needs no contract change. Actions
  that cost money emit `cost.metered` (also already registered).
- Both tracks can exchange rule documents, which keeps the bake-off comparing
  evaluators rather than dialects.
- A rule language change is a spec version bump, not a code change in two
  runtimes.

## What implementing it changed

Two things this document got wrong, or did not say, kept in the ADR rather than
only in the changelog — a decision record that hides its own corrections is worth
less than one that carries them.

**§2's cooldown is right and its obvious implementation is not.** "Cooldown
compares `occurredAt` to the `occurredAt` of the last firing" is correct and
under-specified: *finding* the last firing by taking the most recent `rule.fired`
at a lower `seq` never finds one, because a firing is appended after the event
that caused it and therefore always holds the higher seq. Every event in a burst
concludes the rule has never fired and cooldown silently never applies — a crowd
of five produces five Slack posts. The lookup has to order on the `triggerSeq`
the firing carries, which is also what makes a replay reproduce the original run.

**§4's `none` needs an identity, not just a boundary.** Judging an absence when
the next event arrives is right, but *every* event after the boundary reveals the
same silence, so two arrivals a second apart each fire. Suppressing the second
with control flow is another backwards read and another thing to get wrong. The
firing's derived id is keyed on the **silence** — the seq of the event the quiet
started after — so every later arrival derives the same id and the bus refuses
the duplicate. §3's mechanism, applied to a condition whose cause is an absence.

**A third thing, which is not about the spec but about how it was verified.** The
evaluator read the rule document's camelCase (`zoneId`, `anonId`) against
payloads the tracker writes in snake_case (`event-bus-spec.md` §3). A rule was
armed, correct, and matched nothing, with no error to show for it. Both spellings
are now accepted, snake_case first, as the spec already concedes for
`anon_id` / `person_id`. What is worth recording is that a full unit-test suite
on each side stayed green: each had been written in its own dialect and agreed
with itself. `tests/test_phase3_acceptance.py` runs the chain end to end for
exactly that class of failure.

## Rejected

- **Rules as Python/TypeScript predicates.** Fastest to write, impossible for an
  operator to author, and unreadable after the fact from a `rule.fired` row.
- **A general expression language (CEL, JSONLogic).** Buys arbitrary conditions
  and costs a sandbox, an audit story, and a UI that can no longer show an
  operator what their rule says in English. Revisit if `threshold`/`any`/`none`
  proves too narrow — the `condition.type` discriminator leaves room.
- **Rules in the graph.** Tempting on this track since zones and dwell already
  live there, but it couples "can this booth act" to "is Neo4j up", and it makes
  a rule document dependent on which backend won the bake-off.
