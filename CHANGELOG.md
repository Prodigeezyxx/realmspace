# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **ADR 001 — Graph store decision:** relational node/edge tables in the same SQL DB as the event bus (SQLite locally / Postgres when available). Neo4j/AGE deferred until Ask needs native Cypher (`docs/adr/001-graph-store.md`).
- **Edge API (`backend/`):** FastAPI service with append-only event bus, graph writer consumer, graph snapshot API, WebSocket fan-out, and RBAC skeleton (`GET /v1/auth/resolve`).
- **SQLite default store** for zero-ops local/edge runs; Postgres schema + `docker-compose.yml` ready for when Docker is available.
- **Perception → bus:** `realmspace.py --bus-url http://127.0.0.1:8000` posts `session.*` and throttled `perception.detection` RealmEvents.
- **Dashboard remote bus bridge:** `NEXT_PUBLIC_BUS_URL` enables dual-write from `emit()`, WebSocket replay/mirror into the local durable log, and a StatusBar bus pill.
- Live detector now emits `perception.detection` onto the durable bus (local + remote when configured).

### Changed

- Roadmap Phase 1 items partially progressed (see checkbox updates in `docs/roadmap.md`).

## [0.1.0] — prior prototype

- Next.js dashboard (landing, live TF.js tracking, twin, ask, agents, report).
- Client-side durable event log + contracts + multi-tenant context + ROI scorecard.
- Phase-0 YOLO perception stub (stdout JSON).
