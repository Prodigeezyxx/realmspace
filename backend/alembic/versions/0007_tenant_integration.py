"""where a tenant's CRM credential lives

`multi-tenant.md` §2 has asked for this since Phase 1 — "each tenant's
CRM/enrichment credentials are stored encrypted, per-tenant, never shared" — and
nothing in the repo could hold one, which is the block `roadmap.md` records
against the CRM adapters and which `routers/outcomes.py` names in its docstring
as the reason outcomes are typed in by a human.

## Why the secret is BYTEA and there is no plaintext column beside it

There is nowhere to put an unencrypted token, deliberately. A nullable
`secret_plain` "for local development" is how the first production row ends up
in the clear. `app/secrets.py` refuses to write at all when no key is
configured, and this schema gives that refusal nowhere to fall back to.

## Why `revoked_at` rather than DELETE

The same reason `api_key` keeps one (`auth/models.py`): a credential whose id
turns up in a delivery record afterwards should still be identifiable. It also
means "we stopped using your key" and "we never had one" are different rows
rather than the same absence.

## Why one row per (tenant, provider) rather than a credential id

An adapter asks one question — "what am I authenticating to HubSpot as, for this
tenant?" — and there is exactly one answer at a time. A surrogate key would allow
two active HubSpot credentials for one tenant, which is a state no consumer could
choose between and which no UI would ever intend.

## RLS

Joins the isolation regime of 0003/0004 on the same terms. This is the table
where a cross-tenant read matters most, so it gets the strongest form the
database offers: ENABLE + FORCE, so neither the owner nor a superuser-owned
connection sees across tenants either.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

APP_ROLE = "realmspace_app"

NEW_SCOPED_TABLES = ("tenant_integration",)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tenant_integration (
          tenant_id          TEXT NOT NULL,
          -- 'hubspot' | 'salesforce' | … Open string rather than an enum, for
          -- the reason rules.trigger_type is: the next adapter should not need
          -- a migration to exist, and integrations.md §7 has enrichment
          -- providers arriving through the same door.
          provider           TEXT NOT NULL,
          -- nonce || AES-256-GCM ciphertext, bound to (tenant_id, provider) as
          -- associated data. There is no plaintext column, on purpose.
          secret_ct          BYTEA NOT NULL,
          -- last 4 characters, so a UI can answer "is this the key I pasted?"
          secret_hint        TEXT NOT NULL DEFAULT '',
          -- integrations.md §3: "field mapping is per-tenant config, not code".
          field_map          JSONB NOT NULL DEFAULT '{}'::jsonb,
          status             TEXT NOT NULL DEFAULT 'active',   -- active | revoked
          -- What healthcheck() last said, so a failing credential is visible
          -- before a lead is stranded on it rather than after.
          last_check_at      TIMESTAMPTZ,
          last_check_ok      BOOLEAN,
          last_check_detail  TEXT,
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          revoked_at         TIMESTAMPTZ,
          PRIMARY KEY (tenant_id, provider)
        );
        """
    )

    # The delivery consumer's lookup: "every active integration for this
    # tenant". Partial, because a revoked row is never a candidate.
    op.execute(
        "CREATE INDEX tenant_integration_active_idx "
        "ON tenant_integration (tenant_id) WHERE status = 'active';"
    )

    # 0003's GRANT covered the tables that existed then; a new one needs its own
    # (0004 learned this the same way).
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_integration TO {APP_ROLE};"
    )

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
    op.execute("DROP TABLE IF EXISTS tenant_integration;")
