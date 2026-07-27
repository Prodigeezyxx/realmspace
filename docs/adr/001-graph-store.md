# ADR 001 — Graph store for the edge spine

**Status:** Accepted (2026-07-27)  
**Context:** `roadmap.md` open decision #1 — Neo4j vs embedded SQLite/DuckDB for the edge box.  
**Deciders:** build session on `genspark_ai_developer`.

## Decision

**Use the same SQL database as the event bus for the live graph projection.**

| Layer | Store | Notes |
|---|---|---|
| Event bus (source of truth) | Postgres in production; **SQLite by default for local/edge** | Schema matches `event-bus-spec.md` |
| Live graph | **Relational node/edge tables** in that same DB | Projection of the bus; rebuildable via replay |
| Analytics / ROI rollups | SQL views / later DuckDB | Not the live graph of record |
| Cypher / Neo4j / AGE | **Deferred** | Revisit when Ask-the-Room needs native Cypher (P2+) |

## Why not Neo4j on the edge kit (yet)

1. Conference kits must run **offline on a laptop** with near-zero ops.
2. Session graphs are small (one booth, hours) — SQL joins are enough for KPIs + graph writer.
3. Docs already mandate **Postgres as the append-only bus**; one process beats bus+Neo4j.
4. Ask can still target an allow-listed query library that compiles to SQL until AGE/Neo4j is justified.

## Consequences

- Graph writer is a **bus consumer** that upserts into `graph_nodes` / `graph_edges`.
- Snapshot API returns the shape in `dashboard/src/lib/contracts/graph.ts`.
- Migrating to Apache AGE or Neo4j later is a **storage swap behind the same snapshot contract** — producers stay untouched.
- DuckDB remains optional for heavy ROI reports, fed by replaying `event_log`.

## Rejected for P1

- Neo4j Community on every booth kit (ops/RAM weight)
- FalkorDB / Neptune (cloud-first, weak offline)
- DuckDB as primary graph (analytics, not live upsert graph)
