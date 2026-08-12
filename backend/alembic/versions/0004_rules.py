"""rules and their dispatches

Phase 3's two tables. ADR-002 decides they live here rather than in the graph:
"it couples 'can this booth act' to 'is Neo4j up', and it makes a rule document
dependent on which backend won the bake-off." Rules are configuration, read by
an evaluator that is already holding a SQL session.

## Why the shape is split between columns and JSONB

The evaluator's hot query runs once per event: "enabled rules for this tenant
whose trigger_type matches". At 20fps with several people in frame that is
hundreds of times a second, so the fields it filters on are columns with an
index behind them. `condition` and `action` are only ever read whole, by the
rule that already matched, so they stay JSONB — which also means a spec version
that adds a condition field needs no migration.

## No last_fired_at

The obvious column, and the one `floats-agent`'s evaluator has. ADR-002 §2
rejects it: cooldown compares the `occurred_at` of the triggering event to the
`occurred_at` of the last `rule.fired` **on the bus**. A column holding wall
time makes a replay produce different output from the original run, and it makes
the answer depend on a mutable row rather than on the log.

## rule_dispatch is the idempotency key, not a log

One row per (rule_fired_event_id, action_type), UNIQUE. ADR-002 §3 requires
per-dispatch idempotency keyed on the derived event id so "a retry after a
timeout cannot post to Slack twice". The UNIQUE constraint is the mechanism; the
outcome columns are what makes a failure legible afterwards.

## RLS

Both tables join `0003_rls.py`'s SCOPED_TABLES. Note that an unpolicied table
here would not read as "unprotected" — the app role has no policy at all on a
table with RLS enabled elsewhere, and once enabled a missing policy returns zero
rows to everyone. The failure would look like a broken evaluator, not a leak,
which is the right direction but a confusing silence.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-11
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "realmspace_app"

#: The tables this migration adds to 0003's isolation regime.
NEW_SCOPED_TABLES = ("rules", "rule_dispatch")


def upgrade() -> None:
    # ── the index the evaluator's window read needs ───────────────────────────
    # ADR-002 §1 replaces the in-memory sliding window with "a `seq`-ranged read
    # over the log, which is already indexed (tenant_id, session_id, seq)". That
    # turned out to be half true: the read is *bounded* by seq, but it is
    # *selected* by type and occurred_at — "spatial.dwell in this session in the
    # last 30 seconds". The existing index cannot serve that without scanning
    # every event in the session, which at 20fps is the whole log.
    op.execute(
        "CREATE INDEX event_log_tenant_session_type_occurred_idx "
        "ON event_log (tenant_id, session_id, type, occurred_at);"
    )

    op.execute(
        """
        CREATE TABLE rules (
          rule_id          TEXT PRIMARY KEY,          -- operator-supplied, per ADR-002
          tenant_id        TEXT NOT NULL,
          name             TEXT NOT NULL,
          trigger_type     TEXT NOT NULL,             -- any type in spec §3, not an enum
          trigger_zone_id  TEXT,                      -- optional narrowing
          condition        JSONB NOT NULL,            -- threshold | any | none
          action           JSONB NOT NULL,            -- slack | webhook | screen_swap
                                                      -- | staff_prompt | log
          enabled          BOOLEAN NOT NULL DEFAULT TRUE,
          cooldown_sec     INT NOT NULL DEFAULT 60,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    # The evaluator's per-event lookup, exactly. Partial on enabled because a
    # disabled rule is never a candidate and there is no query that wants one
    # alongside the enabled ones.
    op.execute(
        "CREATE INDEX rules_tenant_id_trigger_type_idx "
        "ON rules (tenant_id, trigger_type) WHERE enabled;"
    )

    op.execute(
        """
        CREATE TABLE rule_dispatch (
          id               BIGSERIAL PRIMARY KEY,
          tenant_id        TEXT NOT NULL,
          -- The derived event_id of the rule.fired that caused this dispatch.
          -- Same firing replayed = same id = the UNIQUE below refuses the
          -- second claim, which is what stops a double Slack post.
          fired_event_id   UUID NOT NULL,
          rule_id          TEXT NOT NULL,
          action_type      TEXT NOT NULL,
          status           TEXT NOT NULL,             -- claimed | delivered | failed
          detail           TEXT,                      -- provider response or error
          attempts         INT NOT NULL DEFAULT 0,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          completed_at     TIMESTAMPTZ,
          UNIQUE (fired_event_id, action_type)
        );
        """
    )
    op.execute(
        "CREATE INDEX rule_dispatch_tenant_id_rule_id_idx "
        "ON rule_dispatch (tenant_id, rule_id);"
    )

    # 0003 granted on ALL TABLES as they existed then, which does not reach a
    # table created afterwards. Without this the app role can see the policy and
    # nothing else.
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON rules, rule_dispatch TO {APP_ROLE};"
    )
    op.execute(f"GRANT USAGE ON SEQUENCE rule_dispatch_id_seq TO {APP_ROLE};")

    for table in NEW_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
              USING      (tenant_id = current_setting('app.tenant_id', true))
              WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
            """
        )


def downgrade() -> None:
    for table in NEW_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute("DROP TABLE IF EXISTS rule_dispatch;")
    op.execute("DROP TABLE IF EXISTS rules;")
    op.execute("DROP INDEX IF EXISTS event_log_tenant_session_type_occurred_idx;")
