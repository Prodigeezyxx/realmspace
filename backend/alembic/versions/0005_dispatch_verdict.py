"""a human's verdict on a stranded dispatch

`repository.claim_dispatch` refuses a row sitting at `claimed`, and says why: a
process died between the claim and the outcome, so whether Slack got the message
is genuinely unknown, and "it needs a human rather than a guess."

There was no way for that human to answer. `get_dispatch`'s docstring said it was
"for `/ops`" and no route read it, so a dispatch stranded by a crash blocked its
`(fired_event_id, action_type)` pair permanently — the one state in the whole
Phase 3 chain with no exit.

## Why two new columns rather than reusing `detail` and `completed_at`

The verdict could have been written into `detail` as text, with `completed_at`
for the timestamp, and no migration. That loses the distinction that matters
most: a `delivered` recorded by the dispatcher is a 200 from Slack, and a
`delivered` recorded by an operator is a person saying they saw the message in
the channel. Both are legitimate; they are not the same evidence, and an audit
that cannot tell them apart is worth less than one that can.

So `resolved_by` is the record that a human intervened, and it is NULL for every
row the dispatcher closed by itself.

## Why the index is partial

Stranded rows are the rare case — a handful over an activation against one per
action dispatched. `/ops` asks only ever "which rows are still `claimed` and old
enough to be worrying", so the index carries only those.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-13
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE rule_dispatch
          ADD COLUMN resolved_by TEXT,          -- user_id of the operator who ruled
          ADD COLUMN resolved_at TIMESTAMPTZ;   -- when they did
        """
    )
    # The `/ops` query, exactly: this tenant's rows still at `claimed`, oldest
    # first. `created_at` is in the index so the staleness cutoff is served by it
    # rather than by a filter on top of it.
    op.execute(
        "CREATE INDEX rule_dispatch_stranded_idx "
        "ON rule_dispatch (tenant_id, created_at) WHERE status = 'claimed';"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS rule_dispatch_stranded_idx;")
    op.execute(
        "ALTER TABLE rule_dispatch "
        "DROP COLUMN IF EXISTS resolved_by, "
        "DROP COLUMN IF EXISTS resolved_at;"
    )
