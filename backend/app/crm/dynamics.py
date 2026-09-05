"""
Microsoft Dynamics 365 — `integrations.md` §4's fifth adapter,
"enterprise/regulated".

## The upsert is OData's, addressed by an alternate key

`PATCH /api/data/v9.2/leads(emailaddress1='someone@example.com')` creates the
lead if the key matches nothing and updates it if it does — one call, no race.
It needs `emailaddress1` registered as an **alternate key** on the `lead` entity,
which is a setting in the client's org, not something an API call can arrange.
`healthcheck` therefore checks for the key as well as the credential, for the
reason `salesforce.py` gives about its External ID field: a connection that tests
green and fails on every lead is worse than one that fails at setup.

## Why the id comes back in a header, and why we ask for a body instead

An OData upsert answers 204 with the record's URI in an `OData-EntityId` header
and no body at all. Reading that header is possible — `CrmHttp._request` exists
partly for it — but the id then has to be parsed out of a URI with a regular
expression, and `crm_link.external_id` is what a withdrawal is later carried out
with. So the request asks for `Prefer: return=representation` and reads
`leadid` from the JSON, with the header kept as the fallback for an org
configured to refuse the preference.

## Client credentials, not a refresh token

Salesforce and Zoho authenticate as a user who granted us access; Dynamics
authenticates as an application registered in the client's Azure AD tenant, with
an application user inside the org. That is the shape enterprises actually
approve — no individual's session to expire when they leave the company — and it
is why this credential carries an Azure `tenant_id` rather than a refresh token.

Note the collision: `tenant_id` in this document is Microsoft's, not ours.
`app/secrets.py` binds the ciphertext to *our* tenant, so the two never meet, but
the name is worth flagging to anybody reading a stored credential.

## What retract does

`DELETE /api/data/v9.2/leads({guid})` is a real delete: Dynamics has no
user-facing recycle bin for it, and the record is gone. That is stronger than
HubSpot's or Salesforce's bins, and the dispatch detail says so.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from app.crm import register
from app.crm.base import AdapterError, CrmAdapter
from app.crm.http import CrmHttp
from app.crm.lead_fields import UNKNOWN_COMPANY, flatten, mappable_fields, split_name
from app.crm.oauth import OAuthTokenMixin

log = logging.getLogger(__name__)

API_VERSION = "v9.2"

DEFAULT_AUTHORITY = "https://login.microsoftonline.com"

#: The alternate key the upsert addresses. Overridable, because an org that
#: already keys leads on something else should not have to add a second one.
DEFAULT_ALTERNATE_KEY = "emailaddress1"

#: Handoff contact fields that map onto stock lead attributes.
STANDARD_FIELDS = {"company": "companyname", "title": "jobtitle"}


@register
class DynamicsAdapter(OAuthTokenMixin, CrmHttp, CrmAdapter):
    provider = "dynamics"

    required_credential_fields = (
        "resource_url",
        "tenant_id",
        "client_id",
        "client_secret",
    )
    optional_credential_fields = ("authority", "alternate_key", "default_company")

    @classmethod
    def parse_secret(cls, raw: str) -> dict[str, Any]:
        return cls._parse_json_credential(raw)

    @classmethod
    def capabilities(cls) -> dict[str, Any]:
        return {"custom_fields": True, "retract": True, "activities": False}

    # ── mapping ───────────────────────────────────────────────────────────────

    def map(self, handoff: dict[str, Any]) -> dict[str, Any] | None:
        contact = handoff.get("contact") or {}
        email = contact.get("email")
        if not email:
            log.info(
                "dynamics: handoff %s has no email; nothing to key a lead on",
                handoff.get("dedupe_key"),
            )
            return None

        first, last = split_name(contact.get("name"))
        attributes: dict[str, Any] = {
            "emailaddress1": email,
            "lastname": last,
            # `subject` is the lead's Topic, and Dynamics requires it. The
            # activation is the truthful answer to "what is this lead about".
            "subject": (handoff.get("activation") or {}).get("name")
            or "realmspace activation",
        }
        if first:
            attributes["firstname"] = first
        for field, attribute in STANDARD_FIELDS.items():
            value = contact.get(field)
            if value:
                attributes[attribute] = value
        attributes.setdefault(
            "companyname",
            self._credential.get("default_company") or UNKNOWN_COMPANY,
        )

        mapped = mappable_fields(handoff)
        for field, attribute in self._field_map.items():
            value = mapped.get(field)
            if value is None:
                log.debug(
                    "dynamics: field %r is mapped to %r but this handoff has no "
                    "value for it",
                    field,
                    attribute,
                )
                continue
            attributes[attribute] = flatten(value)

        return {"key": email, "attributes": attributes}

    # ── the calls ─────────────────────────────────────────────────────────────

    async def upsert(
        self, payload: dict[str, Any], *, external_id: str | None = None
    ) -> str | None:
        """One lead, created or updated by OData against the alternate key.

        `external_id` is not used: the alternate key addresses the record without
        it, which is what makes this a real upsert rather than a search.
        """
        if payload is None:
            return None

        key = quote(str(payload["key"]), safe="")
        response = await self._request(
            "PATCH",
            f"/api/data/{API_VERSION}/leads({self._alternate_key}='{key}')",
            json=payload["attributes"],
            headers={"Prefer": "return=representation"},
        )

        record_id = None
        if response.content:
            try:
                record_id = (response.json() or {}).get("leadid")
            except ValueError:
                record_id = None
        if not record_id:
            record_id = _id_from_entity_header(response.headers.get("OData-EntityId"))
        if not record_id:
            raise AdapterError(
                "dynamics accepted the upsert and gave back neither a leadid nor "
                f"an OData-EntityId: {response.status_code} {response.text[:200]!r}",
                # Addressed by the alternate key, so a repeat cannot duplicate.
                retryable=True,
            )
        return str(record_id)

    async def retract(self, external_id: str) -> str:
        try:
            await self._call(
                "DELETE", f"/api/data/{API_VERSION}/leads({external_id})"
            )
        except AdapterError as exc:
            if getattr(exc, "status", None) == 404:
                return f"lead {external_id} was already gone from dynamics"
            raise
        return (
            f"lead {external_id} deleted from dynamics — a real delete, with no "
            "recycle bin for the client to restore it from"
        )

    async def healthcheck(self) -> tuple[bool, str]:
        """Can we authenticate, and is the key the upsert addresses registered?"""
        try:
            await self._call("GET", f"/api/data/{API_VERSION}/WhoAmI")
            keys = await self._call(
                "GET",
                f"/api/data/{API_VERSION}/EntityDefinitions(LogicalName='lead')/Keys",
                params={"$select": "KeyAttributes,LogicalName"},
            )
        except AdapterError as exc:
            return False, str(exc)

        registered = {
            attribute
            for key in (keys or {}).get("value") or []
            for attribute in key.get("KeyAttributes") or []
        }
        if self._alternate_key not in registered:
            return False, (
                f"dynamics reachable, but the lead entity has no alternate key on "
                f"{self._alternate_key!r} — register one in the org, or name the "
                "key you already have in the credential"
            )
        return True, (
            f"dynamics reachable, application user accepted, upserting on "
            f"{self._alternate_key}"
        )

    # ── where, and with what ──────────────────────────────────────────────────

    @property
    def _alternate_key(self) -> str:
        return self._credential.get("alternate_key") or DEFAULT_ALTERNATE_KEY

    def _base_url(self) -> str:
        return str(self._credential["resource_url"]).rstrip("/")

    def _token_endpoint(self) -> tuple[str, str]:
        authority = self._credential.get("authority") or DEFAULT_AUTHORITY
        return (
            str(authority).rstrip("/"),
            f"/{self._credential['tenant_id']}/oauth2/v2.0/token",
        )

    def _token_form(self) -> dict[str, str]:
        return {
            "grant_type": "client_credentials",
            "client_id": self._credential["client_id"],
            "client_secret": self._credential["client_secret"],
            # Azure AD v2 wants a scope, and for Dataverse it is the org URL with
            # `/.default` — which asks for exactly the permissions the client
            # already granted the application, and nothing more.
            "scope": f"{self._base_url()}/.default",
        }


def _id_from_entity_header(header: str | None) -> str | None:
    """The guid out of `…/leads(00000000-0000-0000-0000-000000000000)`.

    The fallback for an org that refuses `return=representation`. Deliberately
    literal — anything cleverer would be a URI parser for one known shape.
    """
    if not header or "(" not in header or not header.rstrip().endswith(")"):
        return None
    return header.rstrip()[header.index("(") + 1 : -1] or None
