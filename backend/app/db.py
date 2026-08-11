"""
SQL schema for the event bus + relational graph projection.

Mirrors docs/event-bus-spec.md and docs/adr/001-graph-store.md.
SQLite and Postgres share the same logical tables; dialect tweaks are applied
at connect time (AUTOINCREMENT vs BIGSERIAL, etc.).
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from app.config import get_settings

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_log (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id     TEXT UNIQUE NOT NULL,
  tenant_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  type         TEXT NOT NULL,
  payload      TEXT NOT NULL,
  occurred_at  TEXT NOT NULL,
  recorded_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_tenant_session_seq
  ON event_log (tenant_id, session_id, seq);
CREATE INDEX IF NOT EXISTS idx_event_type ON event_log (type);

CREATE TABLE IF NOT EXISTS consumer_cursor (
  consumer     TEXT NOT NULL,
  tenant_id    TEXT NOT NULL,
  last_seq     INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (consumer, tenant_id)
);

CREATE TABLE IF NOT EXISTS dead_letter (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  consumer     TEXT NOT NULL,
  event_seq    INTEGER NOT NULL,
  error        TEXT NOT NULL,
  attempts     INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL,
  resolved_at  TEXT
);

CREATE TABLE IF NOT EXISTS graph_nodes (
  tenant_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  node_kind    TEXT NOT NULL,
  node_id      TEXT NOT NULL,
  props        TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (tenant_id, session_id, node_kind, node_id)
);

CREATE TABLE IF NOT EXISTS graph_edges (
  tenant_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  edge_kind    TEXT NOT NULL,
  from_id      TEXT NOT NULL,
  to_id        TEXT NOT NULL,
  props        TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (tenant_id, session_id, edge_kind, from_id, to_id)
);

CREATE TABLE IF NOT EXISTS auth_users (
  user_id      TEXT PRIMARY KEY,
  email        TEXT UNIQUE NOT NULL,
  display_name TEXT,
  org_id       TEXT NOT NULL,
  role         TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
  rule_id      TEXT PRIMARY KEY,
  tenant_id    TEXT NOT NULL,
  name         TEXT NOT NULL,
  trigger_type TEXT NOT NULL,
  trigger_config TEXT NOT NULL,
  condition_config TEXT NOT NULL,
  action_type  TEXT NOT NULL,
  action_config TEXT NOT NULL,
  enabled      INTEGER NOT NULL DEFAULT 1,
  cooldown_sec INTEGER NOT NULL DEFAULT 60,
  last_fired_at TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rules_tenant ON rules (tenant_id, enabled);

CREATE TABLE IF NOT EXISTS action_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id      TEXT NOT NULL,
  tenant_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  event_seq    INTEGER NOT NULL,
  action_type  TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',
  payload      TEXT NOT NULL,
  attempts     INTEGER NOT NULL DEFAULT 0,
  last_error   TEXT,
  created_at   TEXT NOT NULL,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_action_log_rule ON action_log (rule_id, created_at);

-- Phase 4: Attribute — consent, identity, intent, lead handoff
CREATE TABLE IF NOT EXISTS consents (
  consent_id   TEXT PRIMARY KEY,
  tenant_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  anon_id      TEXT,
  tier         TEXT NOT NULL,
  method       TEXT NOT NULL,
  contact_email TEXT,
  contact_name TEXT,
  contact_phone TEXT,
  copy_version TEXT NOT NULL,
  captured_at  TEXT NOT NULL,
  withdrawn_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_consents_tenant ON consents (tenant_id, session_id);

CREATE TABLE IF NOT EXISTS identities (
  identity_id  TEXT PRIMARY KEY,
  consent_id   TEXT NOT NULL,
  tenant_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  anon_id      TEXT NOT NULL,
  contact_email TEXT NOT NULL,
  contact_name TEXT,
  contact_phone TEXT,
  resolved_at  TEXT NOT NULL,
  UNIQUE(tenant_id, session_id, anon_id)
);

CREATE TABLE IF NOT EXISTS intent_scores (
  tenant_id    TEXT NOT NULL,
  session_id   TEXT NOT NULL,
  anon_id      TEXT NOT NULL,
  score        INTEGER NOT NULL DEFAULT 0,
  tier         TEXT NOT NULL DEFAULT 'cold',
  zones_visited INTEGER NOT NULL DEFAULT 0,
  deep_engagements INTEGER NOT NULL DEFAULT 0,
  surface_interactions INTEGER NOT NULL DEFAULT 0,
  computed_at  TEXT NOT NULL,
  PRIMARY KEY (tenant_id, session_id, anon_id)
);

CREATE TABLE IF NOT EXISTS lead_handoffs (
  handoff_id       TEXT PRIMARY KEY,
  dedupe_key       TEXT UNIQUE NOT NULL,
  tenant_id        TEXT NOT NULL,
  session_id       TEXT NOT NULL,
  anon_id          TEXT NOT NULL,
  contact_email    TEXT,
  contact_name     TEXT,
  contact_phone    TEXT,
  attribution_model TEXT NOT NULL DEFAULT 'first_touch',
  intent_score     INTEGER NOT NULL DEFAULT 0,
  intent_tier      TEXT NOT NULL DEFAULT 'cold',
  consent_id       TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'pending',
  crm_status       TEXT DEFAULT 'pending',
  payload          TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  synced_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_handoffs_tenant ON lead_handoffs (tenant_id, session_id);

-- Phase 4: CRM connection configs
CREATE TABLE IF NOT EXISTS crm_connections (
  tenant_id    TEXT NOT NULL,
  crm_type     TEXT NOT NULL,
  api_config   TEXT NOT NULL,
  field_mapping TEXT NOT NULL DEFAULT '{}',
  health_status TEXT DEFAULT 'unknown',
  last_checked_at TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (tenant_id, crm_type)
);
"""


def _sqlite_path(url: str) -> Path:
    # sqlite:///./data/realmspace.db  or  sqlite:////abs/path
    raw = url.removeprefix("sqlite:///")
    if raw.startswith("./") or (not raw.startswith("/") and ":" not in raw[:2]):
        base = Path(__file__).resolve().parent.parent
        return (base / raw.lstrip("./")).resolve()
    return Path(raw).resolve()


def init_db() -> None:
    """Open (or reopen) the connection and apply schema."""
    global _conn
    settings = get_settings()
    if not settings.is_sqlite:
        # Postgres path reserved — for tonight we require SQLite unless
        # psycopg is wired. Callers still get a clear error.
        raise RuntimeError(
            "Postgres URL set but driver not wired in this build. "
            "Use DATABASE_URL=sqlite:///./data/realmspace.db for local edge, "
            "or install the upcoming postgres extra."
        )

    path = _sqlite_path(settings.database_url)
    path.parent.mkdir(parents=True, exist_ok=True)

    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.execute("PRAGMA foreign_keys=ON;")
        _conn.executescript(SQLITE_SCHEMA)
        _conn.commit()
        _seed_rbac(_conn)


def _seed_rbac(conn: sqlite3.Connection) -> None:
    """RBAC skeleton: Floats operator for local demos."""
    row = conn.execute("SELECT 1 FROM auth_users WHERE user_id = ?", ("u_demo_operator",)).fetchone()
    if row:
        return
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO auth_users (user_id, email, display_name, org_id, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("u_demo_operator", "operator@floats.demo", "Demo Operator", "t_floats", "operator", now),
    )
    conn.execute(
        """
        INSERT INTO auth_users (user_id, email, display_name, org_id, role, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("u_demo_admin", "admin@floats.demo", "Demo Admin", "t_floats", "admin", now),
    )
    conn.commit()


def backend_name() -> str:
    settings = get_settings()
    if settings.is_sqlite:
        return f"sqlite:{_sqlite_path(settings.database_url).name}"
    parsed = urlparse(settings.database_url)
    return f"postgres:{parsed.hostname}/{parsed.path.lstrip('/')}"


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    if _conn is None:
        init_db()
    assert _conn is not None
    with _lock:
        yield _conn


def execute(sql: str, params: tuple[Any, ...] | list[Any] = ()) -> sqlite3.Cursor:
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur


def fetchall(sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return list(conn.execute(sql, params).fetchall())


def fetchone(sql: str, params: tuple[Any, ...] | list[Any] = ()) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()
