"""row-level security: the database enforces tenant isolation

multi-tenant.md §2 asks for isolation "enforced at the DB layer, not just the
app". Until now it was the app: every function in repository.py takes a
tenant_id and every query uses it. That works right up until someone adds a
query that doesn't, and nothing fails to tell them.

After this, the database refuses. A connection that has not declared a tenant
sees zero rows, and one scoped to t_a cannot see t_b's data even by naming it in
a WHERE clause.

## Why a separate role, rather than just enabling RLS

Verified against this database before writing any of it:

    ALTER TABLE … ENABLE ROW LEVEL SECURITY;   -- as the owner: still all rows
    ALTER TABLE … FORCE ROW LEVEL SECURITY;    -- as a superuser: STILL all rows

A superuser bypasses policies unconditionally — FORCE does not change that. So
the application must connect as a role that is neither the owner nor a
superuser, or the policies below are decoration. That role is created here.

## What is deliberately NOT protected

`auth_user` and `api_key` stay out, and it is not an oversight. Authenticating
means reading `api_key` to find out which tenant the caller belongs to; a policy
keyed on that tenant would need the answer before the lookup that produces it.
Neither table holds customer behaviour — they are credentials, protected by
being reachable only through the auth path.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-01
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = "realmspace_app"

#: The tables that hold tenant data and can therefore be scoped.
SCOPED_TABLES = ("event_log", "consumer_cursor", "dead_letter")


def upgrade() -> None:
    # ── the application role ──────────────────────────────────────────────────
    # No SUPERUSER, no BYPASSRLS, not the owner of anything. Created without a
    # password: locally it connects over the unix socket under trust auth, and
    # any deployment sets one explicitly rather than inheriting a default from a
    # migration that lives in git.
    #
    # Roles are cluster-wide rather than per-database, so this is guarded — a
    # second database in the same cluster running these migrations must not fail
    # on an already-existing role.
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} LOGIN;
          END IF;
        END $$;
        """
    )

    # ── dead_letter needs a tenant before it can be scoped ────────────────────
    # It has only ever recorded which consumer failed and on which seq. The
    # error column holds a full traceback, which can quote the event payload —
    # so an unscoped dead_letter is a small but real cross-tenant leak.
    #
    # Phase 3's HITL review screen is per-tenant regardless, so this column is
    # needed either way; adding it now means the queue is scoped from the day it
    # first has rows in it.
    op.execute("ALTER TABLE dead_letter ADD COLUMN tenant_id TEXT;")
    op.execute(
        """
        UPDATE dead_letter d
           SET tenant_id = e.tenant_id
          FROM event_log e
         WHERE e.seq = d.event_seq;
        """
    )
    # Any row whose source event has since been removed has nothing to inherit.
    # There is no correct tenant to invent, so it gets a sentinel that belongs to
    # nobody — under the policy below that makes it invisible to every tenant,
    # which is the right failure direction.
    op.execute("UPDATE dead_letter SET tenant_id = '_orphan' WHERE tenant_id IS NULL;")
    op.execute("ALTER TABLE dead_letter ALTER COLUMN tenant_id SET NOT NULL;")
    op.execute("CREATE INDEX dead_letter_tenant_id_idx ON dead_letter (tenant_id);")

    # ── privileges ────────────────────────────────────────────────────────────
    # The app role can read and write the data, and nothing else. It cannot
    # create, drop or alter — that is Alembic's job, running as the owner.
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        f"TO {APP_ROLE};"
    )
    # BIGSERIAL columns allocate from a sequence, so INSERT alone is not enough.
    op.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE};")

    # ── the policies ──────────────────────────────────────────────────────────
    for table in SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        # FORCE so the policy applies to the table owner too. Without it, any
        # process connecting as the owner silently sees everything.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
              USING      (tenant_id = current_setting('app.tenant_id', true))
              WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
            """
        )

    # USING governs what you can see; WITH CHECK governs what you can write.
    # Both, because isolation that only covers reads still lets a caller write
    # into somebody else's tenant.
    #
    # `true` as the second argument to current_setting means "return NULL if
    # unset" instead of raising. NULL never equals tenant_id, so an unscoped
    # connection sees nothing — it fails closed. Letting it raise would turn a
    # forgotten scope into a 500 rather than an empty result, which is louder
    # but means an unscoped read and an outage look the same.

    # ── tenant discovery ──────────────────────────────────────────────────────
    # Consumers need to know which tenants exist, which is inherently a
    # cross-tenant question and therefore invisible under the policies above.
    #
    # SECURITY DEFINER runs this with the privileges of its owner rather than
    # the caller. That is a deliberate hole, so it is made as small as possible:
    # one function, no arguments, returning only tenant identifiers — never row
    # data — and executable by exactly one role.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_tenants()
        RETURNS TABLE (tenant_id TEXT)
        LANGUAGE sql
        SECURITY DEFINER
        -- Pin the search_path. Without it, a caller who can create objects
        -- could shadow a name this function resolves and have it run as the
        -- definer. Standard hardening for SECURITY DEFINER.
        SET search_path = public, pg_temp
        AS $$
          SELECT DISTINCT e.tenant_id FROM event_log e ORDER BY 1;
        $$;
        """
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION app_tenants() TO {APP_ROLE};")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app_tenants();")
    for table in SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    op.execute("DROP INDEX IF EXISTS dead_letter_tenant_id_idx;")
    op.execute("ALTER TABLE dead_letter DROP COLUMN IF EXISTS tenant_id;")
    # The role is left in place: it is cluster-wide and may own privileges in
    # other databases, so dropping it here could break something this migration
    # never created.
