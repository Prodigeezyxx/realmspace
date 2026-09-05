"""auth: users and device api keys

Hand-written like 0001, and for the same reason — the schema should be readable
next to the doc it implements rather than inferred from a model file.

No seed data here. Dev credentials are created by `python -m app.auth.seed`
instead: a migration runs everywhere it is applied, so putting known credentials
in one would plant them in every environment that ever runs `alembic upgrade`,
including one nobody meant to be local. A command has to be chosen deliberately.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE auth_user (
          user_id      TEXT PRIMARY KEY,
          email        TEXT UNIQUE NOT NULL,
          display_name TEXT,
          tenant_id    TEXT NOT NULL,       -- the tenant this person resolves to
          role         TEXT NOT NULL,       -- multi-tenant.md §3
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX auth_user_tenant_id_idx ON auth_user (tenant_id);")

    op.execute(
        """
        CREATE TABLE api_key (
          key_id     TEXT PRIMARY KEY,
          key_hash   TEXT UNIQUE NOT NULL,  -- sha256 of the key; the key is never stored
          tenant_id  TEXT NOT NULL,
          label      TEXT NOT NULL,         -- "booth-1 camera", so a human can revoke the right one
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          revoked_at TIMESTAMPTZ           -- set rather than deleting, so a key
                                           -- seen in a log is still identifiable
        );
        """
    )
    op.execute("CREATE INDEX api_key_tenant_id_idx ON api_key (tenant_id);")


def downgrade() -> None:
    op.execute("DROP TABLE api_key;")
    op.execute("DROP TABLE auth_user;")
