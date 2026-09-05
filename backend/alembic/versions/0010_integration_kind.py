"""what a stored credential is for, not just who it is with

`tenant_integration` holds one credential per `(tenant, provider)` and nothing
says what kind of thing the provider is. That was fine while every row was a
destination for a lead. Phase 5 adds a second kind — an AI provider for Ask and
the SDR — and `consumers/crm_delivery.py` fans a `handoff.lead` out over *every
active row* a tenant has. Without this column, storing an Anthropic key would
enrol it as a CRM: the delivery consumer would look up an adapter for
`"anthropic"`, fail to find one, and record a failed delivery against a
credential that was never a destination — and the first admin to add one would
see their leads start stranding on `/ops`.

## Why a column and not a second table

The two kinds want exactly the same columns, the same forced RLS, the same
"encrypted, per tenant, bound to `(tenant, provider)`" story in `app/secrets.py`,
and the same admin screen. A second table would duplicate all of it to express
one word. `rules.trigger_type` made the same call for the same reason.

## Why the default is `crm` and not NULL

Every row that exists when this runs is a destination — the only two providers
shipped are CRM adapters and the bring-your-own hooks — so `crm` is not a guess,
it is what those rows already are. A nullable column would push the question to
every reader, and the readers are consumers deciding whether to send somebody's
lead somewhere.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenant_integration "
        "ADD COLUMN kind TEXT NOT NULL DEFAULT 'crm';"
    )
    # The delivery consumer's question is "which destinations does this tenant
    # have?", which is now kind *and* status. The existing active index answers
    # half of it.
    op.execute(
        """
        CREATE INDEX tenant_integration_kind_idx
          ON tenant_integration (tenant_id, kind)
          WHERE status = 'active';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS tenant_integration_kind_idx;")
    op.execute("ALTER TABLE tenant_integration DROP COLUMN IF EXISTS kind;")
