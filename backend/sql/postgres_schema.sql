-- realmspace Postgres schema (event bus + relational graph)
-- Applied by docker-compose on first boot.
-- See docs/event-bus-spec.md and docs/adr/001-graph-store.md

CREATE TABLE IF NOT EXISTS event_log (
  seq          BIGSERIAL PRIMARY KEY,
  event_id     UUID UNIQUE NOT NULL,
  tenant_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  type         TEXT NOT NULL,
  payload      JSONB NOT NULL,
  occurred_at  TIMESTAMPTZ NOT NULL,
  recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_event_tenant_session_seq
  ON event_log (tenant_id, session_id, seq);
CREATE INDEX IF NOT EXISTS idx_event_type ON event_log (type);

CREATE TABLE IF NOT EXISTS consumer_cursor (
  consumer     TEXT NOT NULL,
  tenant_id    TEXT NOT NULL,
  last_seq     BIGINT NOT NULL DEFAULT 0,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (consumer, tenant_id)
);

CREATE TABLE IF NOT EXISTS dead_letter (
  id           BIGSERIAL PRIMARY KEY,
  consumer     TEXT NOT NULL,
  event_seq    BIGINT NOT NULL,
  error        TEXT NOT NULL,
  attempts     INT NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS graph_nodes (
  tenant_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  node_kind    TEXT NOT NULL,
  node_id      TEXT NOT NULL,
  props        JSONB NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, session_id, node_kind, node_id)
);

CREATE TABLE IF NOT EXISTS graph_edges (
  tenant_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  edge_kind    TEXT NOT NULL,
  from_id      TEXT NOT NULL,
  to_id        TEXT NOT NULL,
  props        JSONB NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, session_id, edge_kind, from_id, to_id)
);

CREATE TABLE IF NOT EXISTS auth_users (
  user_id      TEXT PRIMARY KEY,
  email        TEXT UNIQUE NOT NULL,
  display_name TEXT,
  org_id       TEXT NOT NULL,
  role         TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO auth_users (user_id, email, display_name, org_id, role)
VALUES
  ('u_demo_operator', 'operator@floats.demo', 'Demo Operator', 't_floats', 'operator'),
  ('u_demo_admin', 'admin@floats.demo', 'Demo Admin', 't_floats', 'admin')
ON CONFLICT (user_id) DO NOTHING;
