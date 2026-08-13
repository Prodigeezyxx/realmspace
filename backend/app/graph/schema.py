"""
Migration 001 — the graph schema from docs/data-model.md.

Written by hand, like the Postgres migration 0001, so the constraints are
traceable to the doc rather than inferred by a tool.

## What a "schema" means in a graph database

Neo4j is schema-optional: you can write any property onto any node without
declaring it first. So unlike Postgres there is no CREATE TABLE here. What you
declare instead is:

  - **constraints** — rules the database enforces (uniqueness)
  - **indexes**     — structures that make lookups fast

That is the whole schema. The node shapes in data-model.md are a contract the
application keeps, not something the database checks.

## Why every key starts with tenant_id

multi-tenant.md §2: "every table carries tenant_id; every query is tenant-scoped".
Putting tenant_id first in each composite key means a lookup for one tenant
never scans another tenant's nodes, and two tenants can independently use the
same zone id without colliding.

## The Community-edition limit this schema cannot close

multi-tenant.md §2 also asks for isolation "enforced at the DB layer, not just
the app". Neo4j Community cannot do that. Verified against this instance:

    CREATE CONSTRAINT ... REQUIRE z.tenant_id IS NOT NULL
    → 51N27: Property existence constraint is not supported in community edition

There is also no row-level security and no multi-database (SHOW DATABASES
returns only `neo4j` and `system`). So nothing here can stop a node being
written without a tenant_id, and nothing can stop a query reading across
tenants. graph/repository.py is the only thing standing between us and that —
which is why tenant_id is a required argument on every function in it.

Flagged for the supervisor; closing it properly needs Enterprise or a different
store.
"""

from __future__ import annotations

# Uniqueness constraints. Creating a constraint also creates a backing index,
# so these double as the primary lookup path.
CONSTRAINTS: list[tuple[str, str]] = [
    (
        "session_tenant_id_unique",
        "FOR (s:Session) REQUIRE (s.tenant_id, s.id) IS UNIQUE",
    ),
    # Person is keyed on session too: data-model.md says anon_id is
    # "session-scoped ('P-211'), never re-used across sessions". The same
    # anon_id in a different session is a genuinely different person, and this
    # key is what makes that true in the database rather than by convention.
    (
        "person_tenant_session_anon_unique",
        "FOR (p:Person) REQUIRE (p.tenant_id, p.session_id, p.anon_id) IS UNIQUE",
    ),
    # Superseded by migration 002, which re-keys Zone on session_id. Left as it
    # was because 001 has already run on databases that exist.
    (
        "zone_tenant_id_unique",
        "FOR (z:Zone) REQUIRE (z.tenant_id, z.id) IS UNIQUE",
    ),
    (
        "object_tenant_id_unique",
        "FOR (o:Object) REQUIRE (o.tenant_id, o.id) IS UNIQUE",
    ),
    # Superseded by migration 002, as Zone above.
    (
        "surface_tenant_id_unique",
        "FOR (s:Surface) REQUIRE (s.tenant_id, s.id) IS UNIQUE",
    ),
    # Group is used by GROUP_MEMBER_OF and the "Groups in Lounge" query in
    # data-model.md but was never declared in its Nodes section. Declared here;
    # the doc is being corrected in the same change.
    (
        "group_tenant_id_unique",
        "FOR (g:Group) REQUIRE (g.tenant_id, g.id) IS UNIQUE",
    ),
    (
        "event_tenant_id_unique",
        "FOR (e:Event) REQUIRE (e.tenant_id, e.id) IS UNIQUE",
    ),
    # Insight and Frame get keys now so Phase 2/5 add writers without a
    # migration. Nothing writes them yet.
    (
        "insight_tenant_id_unique",
        "FOR (i:Insight) REQUIRE (i.tenant_id, i.id) IS UNIQUE",
    ),
]

# Plain indexes for the access patterns the constraints don't already serve.
INDEXES: list[tuple[str, str]] = [
    # "everything in this session" — the dominant query for /live and the report
    ("person_session_idx", "FOR (p:Person) ON (p.tenant_id, p.session_id)"),
    ("zone_session_idx", "FOR (z:Zone) ON (z.tenant_id, z.session_id)"),
    # zone lookups by name appear throughout data-model.md's example Cypher
    ("zone_name_idx", "FOR (z:Zone) ON (z.tenant_id, z.name)"),
    ("surface_label_idx", "FOR (s:Surface) ON (s.tenant_id, s.label)"),
    ("object_label_idx", "FOR (o:Object) ON (o.tenant_id, o.label)"),
    ("event_type_idx", "FOR (e:Event) ON (e.tenant_id, e.type)"),
]


# ── Migration 002 — Zone and Surface are per session ────────────────────────
#
# 001 keyed both on (tenant_id, id), so a zone id was tenant-global. Zones are
# not: the wizard draws them per activation, `prune_zones` deletes them per
# activation, and `zones_for_session` reads them back per activation. Person
# already had this right — data-model.md calls anon_id "session-scoped, never
# re-used across sessions", and its key says so.
#
# What the old key did, found by publishing two sessions that both used a zone
# called `z_left`: the second publish did not create a second zone. MERGE found
# the first session's node, overwrote its session_id, and moved it — so session
# one's zones vanished from its own report, and its ENTERED and DWELLED_IN edges
# went with the node into a session they did not belong to. Silent both ways:
# the publish response echoes the zones read straight back, so it looked fine.
#
# Surface is worse and gets the same fix. It carries `trigger_count`, so two
# sessions sharing a surface id shared one counter, and a sponsor's usage figure
# for one activation silently included taps from another.
#
# Nodes written before this migration keep whatever session_id they have. Any
# that never got one are excluded from a composite constraint by Neo4j rather
# than blocking it, which is the right failure direction for a schema change
# that must not refuse to apply on a live graph.
REKEY_002: list[str] = [
    "DROP CONSTRAINT zone_tenant_id_unique IF EXISTS",
    "DROP CONSTRAINT surface_tenant_id_unique IF EXISTS",
    "CREATE CONSTRAINT zone_tenant_session_id_unique IF NOT EXISTS "
    "FOR (z:Zone) REQUIRE (z.tenant_id, z.session_id, z.id) IS UNIQUE",
    "CREATE CONSTRAINT surface_tenant_session_id_unique IF NOT EXISTS "
    "FOR (s:Surface) REQUIRE (s.tenant_id, s.session_id, s.id) IS UNIQUE",
]

#: The downgrade puts 001's keys back. It can fail where 002 was doing its job —
#: two sessions legitimately holding the same zone id cannot both exist under a
#: tenant-global key — which is the honest outcome rather than one to paper over.
REKEY_002_DOWN: list[str] = [
    "DROP CONSTRAINT zone_tenant_session_id_unique IF EXISTS",
    "DROP CONSTRAINT surface_tenant_session_id_unique IF EXISTS",
    "CREATE CONSTRAINT zone_tenant_id_unique IF NOT EXISTS "
    "FOR (z:Zone) REQUIRE (z.tenant_id, z.id) IS UNIQUE",
    "CREATE CONSTRAINT surface_tenant_id_unique IF NOT EXISTS "
    "FOR (s:Surface) REQUIRE (s.tenant_id, s.id) IS UNIQUE",
]


# ── Migration 003 — Contact and ConsentEvent (Phase 4) ──────────────────────
#
# `consent-and-identity.md` §3 declares both nodes; nothing has ever created
# them, because until Phase 4 nothing was allowed to name a visitor.
#
# **Both are keyed per tenant, not per session, and that asymmetry is the
# point.** A Person is session-scoped — `anon_id` is "never re-used across
# sessions", so the same id in a different activation is a different human. A
# Contact is the opposite: it is the same person across every activation they
# ever attend, which is the whole reason attribution has anything to attach to.
# Keying a Contact per session would mean one visitor at three events was three
# contacts, and the CFO one-pager Phase 4 ends in would be counting them thrice.
#
# ConsentEvent is tenant-keyed for a blunter reason: it is evidence. It has to
# outlive the session it was taken in, because a withdrawal argued six months
# later is settled by the record of what was shown, and a record that was
# cleaned up with its activation cannot settle anything.
#
# No constraint on `Contact.email`. A person may legitimately consent under one
# address at two activations, and a unique index would refuse the second — which
# is a refusal to record consent somebody actually gave. Deduplication by email
# is an attribution decision, made where attribution is, with the evidence in
# front of it.
CONSENT_003: list[str] = [
    "CREATE CONSTRAINT contact_tenant_id_unique IF NOT EXISTS "
    "FOR (c:Contact) REQUIRE (c.tenant_id, c.id) IS UNIQUE",
    "CREATE CONSTRAINT consent_tenant_id_unique IF NOT EXISTS "
    "FOR (e:ConsentEvent) REQUIRE (e.tenant_id, e.id) IS UNIQUE",
    # Withdrawal arrives with whichever identifier the person has, and email is
    # the one a visitor at a kiosk knows.
    "CREATE INDEX contact_email_idx IF NOT EXISTS "
    "FOR (c:Contact) ON (c.tenant_id, c.email)",
]

CONSENT_003_DOWN: list[str] = [
    "DROP INDEX contact_email_idx IF EXISTS",
    "DROP CONSTRAINT consent_tenant_id_unique IF EXISTS",
    "DROP CONSTRAINT contact_tenant_id_unique IF EXISTS",
]


def statements() -> list[str]:
    """Migration 001 as a list of Cypher statements.

    IF NOT EXISTS on every statement makes the whole migration re-runnable —
    the same property the Postgres migration gets from Alembic's version table.
    """
    out = [
        f"CREATE CONSTRAINT {name} IF NOT EXISTS {body}" for name, body in CONSTRAINTS
    ]
    out += [f"CREATE INDEX {name} IF NOT EXISTS {body}" for name, body in INDEXES]
    return out


def drop_statements() -> list[str]:
    """The downgrade. Indexes backing a constraint are dropped with it."""
    return [f"DROP INDEX {name} IF EXISTS" for name, _ in INDEXES] + [
        f"DROP CONSTRAINT {name} IF EXISTS" for name, _ in CONSTRAINTS
    ]
