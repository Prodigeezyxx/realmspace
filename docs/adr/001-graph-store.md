# ADR 001 — The graph lives in Neo4j; the bus stays the source of truth

**Status:** Accepted (2026-09-05). **Supersedes** the ADR of the same number on
the `floats-agent` branch (Accepted 2026-07-27), which decided the opposite for
that track. That document is not deleted and not merged — it is the record of
the other half of a comparison that was run on purpose, and the case it makes is
answered below rather than dropped.
**Context:** `roadmap.md` open decision 1 — *"Graph store: Neo4j (matches docs)
vs. embedded SQLite/DuckDB graph for the edge box (lighter, offline-friendly).
Lean: decide at start of P1."* It was still open at the end of Phase 6.
**Deciders:** `antoniorobles/phase1` (`[neo4j-track]`), written with both tracks
built and readable.

## Decision

**Neo4j is the graph of record. Postgres remains the append-only event bus, and
the bus remains the source of truth.**

The graph is a *projection* of the log, rebuildable by replay. That is true on
both tracks and it is what makes this a storage choice rather than a data-model
one: nothing about `data-model.md`'s nodes and edges changes with the answer,
and the producers never learn where the projection lands.

`antoniorobles/phase1` becomes the trunk. `origin/floats-agent` is archived: kept
as the record of the comparison, not a base for new work.

## Why the decision could not be made at the start of P1

Because the arguments on both sides were predictions, and two of them turned out
to be about architectures that only one track ended up having. Deciding in
week 1 would have picked a winner on the strength of the writing.

| | `floats-agent` (Postgres) | `antoniorobles/phase1` (Neo4j) |
|---|---|---|
| Last commit | 2026-08-11 | 2026-09-03 |
| Backend files | 21 | 198 |
| Graph layer | `backend/app/graph_writer.py` | `backend/app/graph/` — `driver`, `migrations`, `repository`, `schema` |
| P1–P6 | six commits, 2026-08-10 → 08-11 | dated, separately walked acceptances |
| Backend tests | — | 58 modules, **867 passed / 14 skipped** (2026-09-05) |

**This table is not the argument.** A track can be larger because it is better or
because it was worked on longer, and those look identical from a file count. It
is here because it says which claims below are checkable: the Neo4j column's
assertions have tests behind them, the Postgres column's are still predictions.
The reasons follow.

## Why not the relational graph the other ADR chose

Its case rested on four numbered points. Two have since been answered by things
neither track had in July.

**1. "Conference kits must run offline on a laptop with near-zero ops."** True,
and the edge box does not hold the graph on this track. Offline survival is the
perception layer's job: the stub buffers to disk and replays into the bus across
an outage (`roadmap.md` Phase 1, and `perception/`). The graph is server-side and
catches up when the replay lands. The premise was never wrong — it was aimed at a
single-process edge deployment, and this track put the boundary somewhere else.

**2. "Session graphs are small — SQL joins are enough for KPIs + graph writer."**
Still true for KPIs, and it stopped being the whole question at Phase 2. See
point 4.

**3. "Docs already mandate Postgres as the append-only bus; one process beats
bus+Neo4j."** The first clause is adopted verbatim — this ADR does not move the
bus. The second is the real cost of this decision and it is priced below rather
than argued away.

**4. "Ask can still target an allow-listed query library that compiles to SQL
until AGE/Neo4j is justified."** This is the point that decided it. `docs/adr/
003-nl-query-catalogue.md` built exactly that allow-listed library, and it
compiles to Cypher: an operator's question maps to the *name* of a hand-written
query, never to query text, and `tenant_id` and `session_id` are supplied by the
caller rather than being parameters any entry can declare. It has run against a
real model since 2026-08-31 (open decision 2). The other ADR deferred Cypher to
*"revisit when Ask-the-Room needs native Cypher (P2+)"* — P2 arrived and the
revisit is this document. The queries the catalogue actually needs are path
questions (where somebody walked, what they looked at on the way), and those are
the shape Cypher is for.

## What the rejected ADR got right, and what it costs

Its ops-weight objection survives the decision intact, so it is recorded with a
number instead of a shrug. On a cold `docker compose up --wait` (2026-09-05,
empty volumes, this laptop):

| Container | Memory, idle |
|---|---|
| `neo4j:5-community` | **417.6 MiB** |
| `postgres` | 37.7 MiB |
| `app` | 127.2 MiB |

Neo4j idles at **11× the bus's own store on an empty database**, capped by the
512M heap + 256M pagecache set in `docker-compose.yml`. Whole-stack cold boot is
**13s**, which is the figure `roadmap.md` already claims and it still holds — the
weight is RAM, not startup.

That is affordable on a server and it is exactly the wrong shape for a laptop in
a hall, which is what the other ADR was protecting. Anyone who later needs the
graph *on the kit* should read this row as the reason to re-open the question,
and should re-open it — not treat this ADR as having settled it for that case.

## Consequences

**No DB-layer tenant isolation on the graph, and this is the one to watch.**
Neo4j Community cannot do row-level security. The event log has Postgres RLS and
the graph has nothing equivalent, which makes this the softest boundary in the
product — a fact `roadmap.md` already carries and this ADR does not soften.

What stands in its place is application-layer, and it is enforced rather than
intended: `tenant_id` is a required keyword argument on every function in
`backend/app/graph/repository.py`, every Cypher string in it filters or sets
`tenant_id` (151 occurrences across the file), and the module header says why —
a `MERGE` that forgets the key creates a node with no tenant at all and then
happily returns another tenant's nodes. `backend/tests/test_graph_visibility.py`
holds the guarantee, and it reads through a **second session opened while the
first is still open**, which is the API's real situation, rather than through the
one fixture arrangement in which the failure it was written for is invisible.

The honest statement of the gap: an RLS violation is refused by the database, and
this one is refused by a convention with a test behind it. A reviewer adding a
function to that repository is the control. Say so in review.

**The swap stays reversible.** Because the graph is a replayable projection, and
because the snapshot contract (`dashboard/src/lib/contracts/graph.ts`) is the
seam the dashboard reads through, moving to Apache AGE or back to relational
tables is a storage change behind an unchanged contract with producers untouched.
That reasoning is the rejected ADR's own, adopted verbatim — it was right about
the seam, and it is the reason this decision is cheap to be wrong about.

**Rebuild, don't migrate.** There is no migration path from `floats-agent`'s
`graph_nodes`/`graph_edges` and there does not need to be one: replay the log.

## Rejected, unchanged from the other ADR

- FalkorDB / Neptune — cloud-first, weak offline.
- DuckDB as the primary graph — analytics, not a live upsert graph.

Apache AGE moves from "deferred" to the named fallback if the RLS gap above ever
has to close at the database layer: it would put the graph back inside the
Postgres that already has RLS, at the cost of the query shape point 4 bought.
