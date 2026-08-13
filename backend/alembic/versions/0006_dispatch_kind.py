"""rule_dispatch answers for handoffs too

Phase 4's handoff delivery has exactly the problem `rule_dispatch` was built for:
an outbound call to somebody else's endpoint, which is not idempotent and cannot
be made so from this side, so the decision to make it has to be taken in a
database we control before the call goes out.

Rather than a second table with the same three states, the same UNIQUE
constraint and a second copy of the reasoning, this widens the one that exists.
The table's own docstring always said it "exists for its UNIQUE constraint" on
`(fired_event_id, action_type)` — that constraint is about *a cause and an
outbound act*, and a rule firing was only ever the first kind of cause.

## Why `kind` rather than nothing at all

The claim works without it: a handoff's `event_id` is unique on the log, so the
UNIQUE constraint already keeps two deliveries apart from each other and from
every rule dispatch. `kind` exists for the human on `/ops`, who otherwise sees a
stranded row whose `rule_id` column holds a session id and has no way to tell a
stuck Slack post from a stuck lead — two problems with very different urgency.

Defaulted to `'rule'` so every existing row is correctly labelled without a
backfill, and NOT NULL so a new writer has to say which it is.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-13
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # kind: 'rule' | 'handoff'
    op.execute(
        "ALTER TABLE rule_dispatch ADD COLUMN kind TEXT NOT NULL DEFAULT 'rule';"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE rule_dispatch DROP COLUMN IF EXISTS kind;")
