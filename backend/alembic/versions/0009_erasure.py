"""the one column that admits the log is not quite append-only

`EventLog`'s own docstring says "nothing ever updates or deletes a row here —
that is what append-only means, and it is why replay works". That is the design,
and it stays the design. GDPR Article 17 is the one thing that outranks it.

`consent-and-identity.md` §5 asks for "a tenant-scoped erasure job that removes a
Contact and all PII edges, keeping only anonymised aggregates". The withdrawal
path already does the graph half — it drops the `IDENTIFIED_AS` edge, redacts the
Contact to a tombstone and retracts the record from every CRM that received it.
What it cannot touch is the log, where the visitor's email is sitting inside
`consent.captured`'s contact object and inside every `handoff.lead` payload. An
erasure that leaves those is not an erasure.

## What is actually preserved, and why this is not a break with replay

The erasure rewrites `payload` and nothing else. `seq`, `event_id`, `type`,
`occurred_at` and `recorded_at` are untouched — no row is deleted, no sequence
number is reused, and the shape a consumer reads is the shape it always read. A
replay after an erasure reproduces the same events in the same order, with a name
missing from three of them. That is a weaker guarantee than "the log never
changes", and it is the guarantee the law leaves us.

## Why the stamp is a column and not a key in the payload

The payload is the thing being rewritten. A `redacted_at` inside it would be
written by the same statement that removes the evidence it is describing, and
"which events has an erasure touched" would be a question only answerable by
scanning JSON. As a column it is one indexed predicate, which is what an auditor
asking "show me everything you erased in March" needs.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-17
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE event_log ADD COLUMN redacted_at TIMESTAMPTZ;")
    # Partial: the overwhelming majority of rows are NULL forever, and an index
    # over them would be most of the log to answer a question about none of it.
    op.execute(
        """
        CREATE INDEX event_log_redacted_at_idx
          ON event_log (tenant_id, redacted_at)
          WHERE redacted_at IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS event_log_redacted_at_idx;")
    op.execute("ALTER TABLE event_log DROP COLUMN IF EXISTS redacted_at;")
