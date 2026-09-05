"""the organisation registry, which signup needs and nothing had

`multi-tenant.md` §1 says "Tenant = Organization" and every table in this schema
carries a `tenant_id`, but no table ever said which organisations exist. A tenant
was whatever string a seeded `auth_user` row happened to carry.

That was liveable while the only way in was `python -m app.auth.seed` — somebody
had to type the id, so it was always one somebody meant. It stops being liveable
with `POST /v1/auth/signup`, which has to mint an id nobody has typed, guarantee
it is not already taken, and record what the organisation is actually called.

`repository.list_tenants` predicted this table in as many words: "Consumers loop
over this to know what to poll. Reading it off the log keeps this item from
needing a tenants table it would otherwise have to invent … A real registry is
Phase 6."

## `list_tenants` still reads the log, deliberately

This registry does not replace it, and pointing the consumer loop here would be a
regression. Consumers poll the tenants that have **work** — events — and an
organisation that signed up this morning and has run nothing has none. Reading
the registry instead would make every consumer poll every organisation that ever
signed up, forever, for the rest of the deployment's life.

Two questions, two answers: the registry says who exists, the log says who has
work.

## No row-level security, like the other two auth tables

`auth_user` and `api_key` are outside RLS because `get_principal` has to read
them *before* a tenant is known — `app/auth/principal.py` says so. This table has
the sharper version of the same problem: signup runs before the tenant exists at
all, so a policy keyed on the current tenant could never admit the INSERT that
creates one.

What protects it instead is that nothing reads it cross-tenant. `POST /v1/users`
looks up the caller's own organisation by the id on their verified credential,
and there is no endpoint that lists organisations.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-21
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant (
            tenant_id   TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            -- The user_id of whoever signed up. Text rather than a foreign key
            -- to auth_user: the two rows are written in one transaction and
            -- either order would make one of them reference a row that does not
            -- exist yet. The record is what matters, not the constraint.
            created_by  TEXT
        );
        """
    )
    # Organisations are created by signup, which has the address in hand and
    # nothing else, so "who signed this one up" is the only lookup that is not
    # by primary key.
    op.execute(
        "CREATE INDEX IF NOT EXISTS tenant_created_by_idx ON tenant (created_by);"
    )

    # Same grants as auth_user and api_key get in 0003. The app role reads and
    # writes this table on the signup path; it is outside RLS for the reason in
    # the docstring, not outside the app role's reach.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tenant TO realmspace_app;")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS tenant_created_by_idx;")
    op.execute("DROP TABLE IF EXISTS tenant;")
