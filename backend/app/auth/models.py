"""
Who is allowed to talk to this backend.

Two tables because there are two kinds of caller and they are not alike:

  auth_user   a person. Has an email, a role, and a tenant.
  api_key     a machine. Has no email and no role — only a tenant and the
              ability to write events.

Keeping them apart rather than modelling a device as a user with a funny role
means a device can never accidentally inherit a permission a human role grows
later.

A third table joined them in Phase 6, and it is not a caller:

  tenant      the organisation both of the above belong to.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

#: Roles from multi-tenant.md §3. "analyst" covers Analyst/Marketer and
#: "viewer" covers Viewer/Client — the doc pairs them.
USER_ROLES = ("admin", "operator", "analyst", "viewer")

#: What an API key carries. Not in USER_ROLES on purpose: it is not a human role
#: and must never be assignable to a person by a typo.
DEVICE_ROLE = "producer"


class Tenant(Base):
    """An organisation. `multi-tenant.md` §1: "Tenant = Organization."

    Every table in this schema has carried a `tenant_id` since the first
    migration and none of them ever said which organisations exist — a tenant
    was whatever string a seeded `auth_user` row happened to hold. That was
    liveable while somebody had to type the id by hand. `POST /v1/auth/signup`
    has to mint one nobody has typed, prove it is free, and record what the
    organisation is actually called.

    **Not** the list consumers poll. `repository.list_tenants` reads the event
    log and keeps doing so: consumers want the tenants with work, and an
    organisation that signed up this morning and has run nothing has none.
    """

    __tablename__ = "tenant"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    #: The `user_id` of whoever signed up. Deliberately not a foreign key — the
    #: two rows are written in one transaction, and either order would leave one
    #: of them pointing at something that does not exist yet.
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Which `gtm.md` tier this organisation is on (migration 0012). What it
    #: means is in `app/plans.py`; a CHECK constraint keeps a typo out, because
    #: an unrecognised plan must be a refusal rather than an exemption.
    plan: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'booth'")
    )

    __table_args__ = (Index("tenant_created_by_idx", "created_by"),)


class AuthUser(Base):
    """A human. Resolves to exactly one tenant and one role.

    Field names match the other track's `auth_users` so `/v1/auth/resolve`
    returns an identical shape and POD 3 writes one integration.

    One tenant per user is a simplification, and a deliberate one for Phase 1 —
    multi-tenant.md §1 says a kit runs one organisation's activation at a time.
    Users belonging to several orgs is a Phase 6 concern and would need a join
    table; nothing here blocks that.
    """

    __tablename__ = "auth_user"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (Index("auth_user_tenant_id_idx", "tenant_id"),)


class ApiKey(Base):
    """A device credential: perception, an RFID bridge, a kiosk.

    ## The hash is not optional

    `key_hash` holds SHA-256 of the key; the key itself is shown once at
    creation and never stored. If this table leaks, the keys in it are not
    usable — which is the entire reason to hash.

    SHA-256 rather than bcrypt is deliberate. Bcrypt exists to make *guessing*
    expensive, which matters for human-chosen passwords. These are 256 bits of
    randomness — there is no dictionary and no feasible search — so a slow KDF
    would add latency to every producer request and buy nothing. Different
    threat, different tool.

    `revoked_at` rather than deleting the row: a revoked key that reappears in a
    log should still be identifiable.
    """

    __tablename__ = "api_key"

    key_id: Mapped[str] = mapped_column(Text, primary_key=True)
    key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("api_key_tenant_id_idx", "tenant_id"),)
