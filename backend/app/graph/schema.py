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
    (
        "zone_tenant_id_unique",
        "FOR (z:Zone) REQUIRE (z.tenant_id, z.id) IS UNIQUE",
    ),
    (
        "object_tenant_id_unique",
        "FOR (o:Object) REQUIRE (o.tenant_id, o.id) IS UNIQUE",
    ),
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
