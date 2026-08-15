"""where a contact actually went

`consumers/reanonymise.py` emits `crm.retract` with `destination: "all"`, and
says why in a comment: *"until the attribution consumer records where a handoff
actually went (Phase 4's later half), the honest value is 'everywhere' rather
than a named CRM this deployment may not even use"*. This table is that record,
and it is what turns "all" into the two CRMs that really received something.

## Why a table rather than a property on the graph Contact

The withdrawal deletes the `IDENTIFIED_AS` edge and redacts the Contact — that
is the whole of `consumers/reanonymise.py`. A note stored on the Contact saying
"pushed to HubSpot as 51234" would be redacted along with it, moments before the
retract consumer needed to read it. The record of where we sent somebody has to
outlive the record of who they were.

## This table holds PII, and the retract redacts it

`dedupe_key` is `tenant:email` (`consumers/attribution.py`). It is here because
it is the only handle an outcome or a ledger row shares with a CRM push. When a
retraction succeeds the key is redacted the same way `attribution/ledger.py`
redacts it — same function, not a second copy — while `external_id`,
`retracted_at` and the detail stay. The row surviving and being empty of them is
the answer to "did you go and delete it?", exactly as it is in the ledger.

`external_id` is not redacted: it is HubSpot's own identifier for a record we
have just asked HubSpot to remove, and an audit that cannot name the record
cannot check that it went.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

APP_ROLE = "realmspace_app"

NEW_SCOPED_TABLES = ("crm_link",)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE crm_link (
          id            BIGSERIAL PRIMARY KEY,
          tenant_id     TEXT NOT NULL,
          provider      TEXT NOT NULL,
          -- Our Contact, which the re-anonymiser names in `crm.retract`.
          contact_id    TEXT NOT NULL,
          -- Theirs. What retract() is called with.
          external_id   TEXT NOT NULL,
          -- PII (tenant:email). Redacted when the retraction succeeds.
          dedupe_key    TEXT NOT NULL,
          -- The activation the push came from, so /ops and the ledger can join
          -- a link back to the session that produced it.
          session_id    TEXT NOT NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          retracted_at  TIMESTAMPTZ,
          retract_detail TEXT,
          -- One link per contact per destination. The two handoff stages of one
          -- visitor upsert the same row, which is the same claim the shared
          -- dedupe_key makes at the CRM end.
          UNIQUE (tenant_id, provider, contact_id)
        );
        """
    )
    # The retract consumer's lookup: "everywhere this contact was pushed".
    op.execute(
        "CREATE INDEX crm_link_tenant_contact_idx ON crm_link (tenant_id, contact_id);"
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON crm_link TO {APP_ROLE};")
    op.execute(f"GRANT USAGE ON SEQUENCE crm_link_id_seq TO {APP_ROLE};")

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
    op.execute("DROP TABLE IF EXISTS crm_link;")
