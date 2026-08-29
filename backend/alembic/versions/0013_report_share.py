"""
A report a client can open without an account.

`roadmap.md` carried this as the last unbuilt Phase 6 item that needed nothing
external: *"a signed, expiring, read-only URL needing no account"*. Until now
"Share with client" granted a viewer seat, which is what `multi-tenant.md` §3
means by Viewer and which requires the client to sign in — so the deliverable
the product is sold on could not be handed to the person it was made for.

## The token is stored hashed and shown once

`secrets.token_urlsafe(32)`, kept as a sha256 digest and returned exactly once
at creation. The same shape `tenant_integration` (0007) uses for credentials,
for the same reason: a value nobody can read back cannot leak out of the
database, and a `hint` makes a list legible without the secret being
retrievable.

Revocation is why this is a row and not a JWT. A signed token cannot be
withdrawn without a denylist, and a denylist is a table — so the token may as
well live in one, where `revoked_at` is a single UPDATE.

## No row-level security, for 0011's reason in its sharpest form

`auth_user`, `api_key` and `tenant` are outside RLS because they are read
*before* a tenant is known. This table is read before the caller is known at
all: the whole point is a request with no credential, whose only claim is a
token, and whose tenant is a **column on the row being looked up**. A policy
keyed on `app.tenant_id` could never admit that SELECT.

What protects it instead:

  - the lookup key is a sha256 of 32 random bytes, so a row cannot be found
    without already holding the capability it grants;
  - every operator-side query filters on the caller's verified `tenant_id`
    explicitly, and `tests/test_share.py` asserts one tenant cannot list or
    revoke another's;
  - the readers it feeds are three, they are the only routes that accept a
    token, and each resolves its own `(tenant, session)` rather than taking
    either from the request.

## Revoked rows are kept

`revoked_at` rather than a DELETE, so "was this link ever live, who created it,
and how often was it opened" survives the revocation. A link that has been
handed to a client is a fact about the activation, and the answer to "did they
ever see this" should not be destroyed by withdrawing it.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-29
"""

from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

APP_ROLE = "realmspace_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS report_share (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT        NOT NULL,
            session_id    TEXT        NOT NULL,
            -- The digest, never the token. There is deliberately no column
            -- that could hold the plaintext.
            token_sha256  TEXT        NOT NULL UNIQUE,
            -- Last four characters, so a list is legible. Enough to recognise a
            -- link you are holding, not enough to reconstruct one.
            hint          TEXT        NOT NULL,
            label         TEXT,
            created_by    TEXT        NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at    TIMESTAMPTZ NOT NULL,
            revoked_at    TIMESTAMPTZ,
            last_viewed_at TIMESTAMPTZ,
            view_count    INTEGER     NOT NULL DEFAULT 0
        );
        """
    )

    # The reader's lookup, which happens on every page load of a shared report.
    op.execute(
        "CREATE INDEX IF NOT EXISTS report_share_token_idx "
        "ON report_share (token_sha256);"
    )
    # The operator's list, per activation.
    op.execute(
        "CREATE INDEX IF NOT EXISTS report_share_session_idx "
        "ON report_share (tenant_id, session_id);"
    )

    # 0003's GRANT covered the tables that existed then; a new one needs its own
    # (0004 and 0007 each learned this the same way).
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON report_share TO {APP_ROLE};"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS report_share;")
