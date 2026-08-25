"""
Which pricing tier an organisation is on.

`multi-tenant.md` §5 has said since Phase 1 that the `gtm.md` tiers become
enforceable *because* the system is multi-tenant, and nothing anywhere recorded
which tier a tenant had bought. `app/plans.py` is what reads this column.

## The backfill is `pavilion`, and new signups are `booth`

Two different answers on purpose. `booth` allows one camera and thirty days;
putting every organisation that already exists on it would start refusing the
four-camera sessions, the demo data and the recorded session every phase
acceptance in `roadmap.md` was verified against — a regression dressed as a
feature. So rows that exist when this migration runs are moved to `pavilion`,
and the column default (`booth`) catches everything created afterwards.

`app/plans.LEGACY_PLAN` has to stay equal to the backfill: a tenant whose
registry row predates 0011 has no row at all, and it resolves through that
constant. The same organisation must not be on two tiers depending on whether
anybody wrote its name down.

## The CHECK

A typo'd plan must not silently resolve to "unlimited". `limits_for` raises on
an unknown string for the same reason: the failure of a limits system should be
a refusal, not an exemption.

`tenant` is outside RLS by design (see 0011's docstring — signup runs before the
tenant exists, so a policy keyed on the current tenant could never admit the
INSERT). This column changes nothing there; it is read by primary key, for a
tenant already derived from a verified credential.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-25
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

#: Kept literal rather than imported from `app.plans`. A migration is a
#: statement about what the database looked like on the day it ran; importing
#: the constant would let a later code change rewrite history.
PLANS = ("booth", "pavilion", "campaign", "partner")


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tenant ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL "
        "DEFAULT 'booth';"
    )

    # Every organisation that exists right now. New ones take the column
    # default. See the docstring for why the two differ.
    op.execute("UPDATE tenant SET plan = 'pavilion';")

    allowed = ", ".join(f"'{p}'" for p in PLANS)
    op.execute(
        f"ALTER TABLE tenant ADD CONSTRAINT tenant_plan_known "
        f"CHECK (plan IN ({allowed}));"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tenant DROP CONSTRAINT IF EXISTS tenant_plan_known;")
    op.execute("ALTER TABLE tenant DROP COLUMN IF EXISTS plan;")
