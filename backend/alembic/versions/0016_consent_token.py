"""
A consent kiosk, and the credential a printed QR code is allowed to carry.

`roadmap.md` Phase 4 built the whole identified half of the funnel — capture →
`consumers/identity.py` → `IDENTIFIED_AS` → attribution → five CRM adapters →
the SDR draft → the ledger — and recorded that "the **surfaces** themselves
(badge/QR/kiosk hardware) are not built". So the only producer of a
`consent.captured` was `curl`. This authorises the surface, exactly as 0015
authorised the tablet that closed the same gap for `surface.interaction`.

## Its own table, not a `kind` column on `surface_token`

The two credentials are the same shape and must not be the same row. A kiosk's
token is printed on a QR code stuck to a plinth, which is the most copyable
credential in this system; sharing a table would mean one leaked photograph
could also post `surface.touched` and inflate a touchpoint's tally, and it would
mean revoking the kiosk revoked the tablet beside it.

## What one is worth if it leaks

**One** `consent.given` per POST, on one activation, from one kiosk — the ids
come off this row and never from the request, which is 0013's and 0015's shape.
It reads the activation's consent copy and nothing else: no visitor, no count,
no report. A stranger holding it can put a consent on the log for a person who
does not exist, which is visible as a `Contact` with no path and no outcome, and
is not a disclosure of anybody's data.

## No row-level security, for 0013's and 0015's reason

The row is looked up before the caller is known — the request carries a token
and nothing else, and the tenant it should be scoped to is a column on the row
being read. What stands in for a policy: the lookup key is a digest of 32 random
bytes, every operator-side query filters on the caller's verified tenant, and
`scope_to_tenant` runs immediately after the lookup, so the append that follows
is under the same policy as any other producer's.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-03
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

APP_ROLE = "realmspace_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS consent_token (
            id            TEXT PRIMARY KEY,
            tenant_id     TEXT        NOT NULL,
            session_id    TEXT        NOT NULL,
            -- The kiosk this is, as a `Surface` on the activation. Taken from
            -- here on every POST and never from the request, so one kiosk
            -- cannot capture on behalf of another — and so the zone that
            -- attributes the consent is the operator's, not the caller's.
            surface_id    TEXT        NOT NULL,
            -- The digest, never the token. There is deliberately no column
            -- that could hold the plaintext.
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

    # The visitor's lookup, which happens on every scan.
    op.execute(
        "CREATE INDEX IF NOT EXISTS consent_token_token_idx "
        "ON consent_token (token_sha256);"
    )
    # The operator's list, per activation.
    op.execute(
        "CREATE INDEX IF NOT EXISTS consent_token_session_idx "
        "ON consent_token (tenant_id, session_id);"
    )

    # 0003's GRANT covered the tables that existed then; a new one needs its
    # own (0004, 0007, 0013 and 0015 each learned this the same way).
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON consent_token TO {APP_ROLE};"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS consent_token;")
