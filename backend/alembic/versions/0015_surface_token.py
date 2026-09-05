"""
A tablet at a touchpoint, and the credential it is allowed to hold.

`roadmap.md` Phase 2 recorded that surface interactions have their whole path
built — the wizard writes `Surface` nodes, `graph_writer` draws
`INTERACTED_WITH`, `scorecard.ts` scores the Engagement layer — and that **no
producer was invented**, because "real interactions need booth hardware". A
tablet running a browser page is that hardware, and this is what authorises one.

## Not a device API key

`api_key` already exists and is what `perception/bus_client.py` presents. It is
the wrong credential here for two reasons, and the second is the deciding one.
It is tenant-wide, so a tablet on a stand in public would hold the same
authority as the camera box in the back office. And it cannot be revoked without
a rotation that takes the camera down with it — on the device most likely to be
picked up and walked off with.

So this follows `report_share` (0013) instead: 32 random bytes, stored as a
sha256, shown once, revocable on its own. A tablet left in a taxi is one UPDATE.

## What one is worth if it leaks

**One** `surface.touched` event per POST, on one activation, for one surface —
the ids come off this row and never from the request, which is the shape 0013's
docstring argues for and `docs/adr/003-nl-query-catalogue.md` argues for again.
It reads nothing. A stranger holding it can inflate one touchpoint's tally on
one activation, which is visible in the report as a number that does not match
the floor, and is not a data breach.

That exposure is why the fields are here and not in the payload: a token that
carried its own `surface_id` from the request body would let one tablet post as
any touchpoint on the stand.

## No row-level security, for 0013's reason

Read before the caller is known at all — the request has no credential, only a
token, and the tenant it should be scoped to is a column on the row being looked
up. A policy keyed on `app.tenant_id` could never admit that SELECT. What stands
in for it: the lookup key is a digest of 32 random bytes, every operator-side
query filters on the caller's verified tenant, and the routes that accept a
token are two, in one file, neither of which reads an event.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-31
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

APP_ROLE = "realmspace_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS surface_token (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT        NOT NULL,
            session_id    TEXT        NOT NULL,
            -- The touchpoint this tablet is. Taken from here on every POST and
            -- never from the request, so one tablet cannot post as another.
            surface_id    TEXT        NOT NULL,
            -- The digest, never the token. There is deliberately no column that
            -- could hold the plaintext.
            token_sha256  TEXT        NOT NULL UNIQUE,
            hint          TEXT        NOT NULL,
            label         TEXT,
            created_by    TEXT        NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at    TIMESTAMPTZ NOT NULL,
            revoked_at    TIMESTAMPTZ,
            last_used_at  TIMESTAMPTZ,
            use_count     INTEGER     NOT NULL DEFAULT 0
        );
        """
    )

    # The tablet's lookup, which happens on every tap.
    op.execute(
        "CREATE INDEX IF NOT EXISTS surface_token_token_idx "
        "ON surface_token (token_sha256);"
    )
    # The operator's list, per activation.
    op.execute(
        "CREATE INDEX IF NOT EXISTS surface_token_session_idx "
        "ON surface_token (tenant_id, session_id);"
    )

    # 0003's GRANT covered the tables that existed then; a new one needs its own
    # (0004, 0007 and 0013 each learned this the same way).
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON surface_token TO {APP_ROLE};"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS surface_token;")
