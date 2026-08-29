"""
Retention actually removing something.

`gtm.md` sells "30-day data retention" on Booth and "90-day retention" on
Pavilion, and until now nothing was ever removed: `app/plans.retention_floor`
clamps what a client may *read* and every row stayed forever. A client's data
outlived the window they bought and was merely invisible to them, which
`roadmap.md` recorded rather than left to be discovered.

## Two columns, not one

`event_log.redacted_at` already exists and already means something: **this
person asked to be forgotten**, stamped by the Article 17 erasure job. A purge
is a different fact — this aged out — and the erasure receipt's counts are
something an auditor reads. Sharing the column would start including rows
nobody requested in an answer to "what did this erasure remove".

So `purged_at`, beside it and never instead of it.

## The watermark is the interesting half

A purge empties payloads and leaves the rows. That keeps derived event ids
resolvable and `dead_letter.event_seq` pointing at something — but it means a
replay from seq 0 would feed consumers empty events and rebuild a *wrong* graph,
quietly, because most consumers skip what they cannot read.

`tenant_purge_watermark` records how far a tenant has been purged, and
`repository.reset_cursor` refuses to rewind below it. The wrong replay becomes
unwritable rather than merely wrong, which is the shape ADR-003 chose for a
tenant-omitting query and the share router chose for reading a second
activation.

Under RLS like every other tenant-scoped table: unlike `report_share` (0013),
this is only ever read by a caller whose tenant is already established.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-29
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

APP_ROLE = "realmspace_app"
SCOPED = ("tenant_purge_watermark",)


def upgrade() -> None:
    # Nullable, and null means "never purged" — the same shape `redacted_at`
    # uses, so "what did this touch" stays an honest answer rather than a list
    # of everything it looked at.
    op.execute("ALTER TABLE event_log ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ;")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_purge_watermark (
            tenant_id   TEXT PRIMARY KEY,
            -- Every event at or below this seq has had its payload emptied, so
            -- no consumer may be rewound past it and expect a correct graph.
            purged_before_seq BIGINT      NOT NULL,
            -- The floor the last purge was computed against, for the receipt to
            -- be checkable later against a plan that may since have changed.
            purged_through    TIMESTAMPTZ NOT NULL,
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )

    # 0003's GRANT covered the tables that existed then; a new one needs its own
    # (0004, 0007 and 0013 each learned this the same way).
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_purge_watermark TO {APP_ROLE};"
    )

    for table in SCOPED:
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
    for table in SCOPED:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.execute("DROP TABLE IF EXISTS tenant_purge_watermark;")
    op.execute("ALTER TABLE event_log DROP COLUMN IF EXISTS purged_at;")
