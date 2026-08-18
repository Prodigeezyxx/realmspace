"""
HubSpot — the reference adapter.

`integrations.md` §4 puts it first ("easiest API, common in mid-market; first
adapter (reference impl)") and `roadmap.md`'s open decision 3 confirms it. Phase
4's acceptance names it directly: *"a LeadHandoff lands in HubSpot with
spatial_intent fields, a withdrawal retracts it"*.

## The upsert is HubSpot's own upsert, not a search-then-create

`POST /crm/objects/{version}/contacts/batch/upsert` with `idProperty: "email"`
creates or updates in one call. The obvious alternative — search by email, then
create or patch — has a race the trade-show floor will find: the two handoff
stages of one visitor can be in flight together after a delivery retry, both
search, both miss, both create. `integrations.md` §2 states the requirement
plainly: "a create-only implementation will duplicate every lead in the client's
CRM", and a search-then-create is a create-only implementation with extra steps.

A batch endpoint for one contact is not an oversight: it is the only shape
HubSpot offers that takes `idProperty`.

## A handoff with no email is declined, not invented

`consumers/attribution.py` keys on `tenant:email|anon_id`, so an anonymous
handoff reaches here with nothing HubSpot can match on. Creating a contact from
an `anon_id` would put a person in the client's CRM who does not exist and whom
no salesperson can ever contact. `map` returns `None` and the delivery consumer
records that nothing was sent.

## What `retract` actually does, said plainly

`DELETE /crm/objects/{version}/contacts/{id}` moves the contact to HubSpot's
recycling bin, where the client can restore it for 90 days. That is a removal,
not an erasure, and the dispatch detail says so rather than letting "retracted"
imply more than happened. A true erasure is HubSpot's GDPR delete, which needs
account-level GDPR features this adapter does not assume; it belongs with
Phase 4's own erasure job (`roadmap.md`, still open) where the same question has
to be answered for every destination at once.

A 404 succeeds quietly. The re-anonymiser is retryable and a withdrawal can be
replayed from the log, so "already gone" is the expected second outcome.

## Custom properties come from the tenant's field map

`integrations.md` §3: "field mapping is per-tenant config, not code — customers
map `spatial_intent.lead_score` → their CRM's custom field in the UI." A booth's
`lead_score` has no home in a stock HubSpot portal, and inventing property names
would fail every request with a validation error on a property the client never
created. Unmapped fields are dropped, and the standard contact fields
(email/name/company/title) go regardless because every portal has them.
"""

from __future__ import annotations

import logging
from typing import Any

from app.crm import register
from app.crm.base import AdapterError, CrmAdapter
from app.crm.http import CrmHttp
from app.crm.lead_fields import flatten, mappable_fields

log = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"

#: HubSpot's date-versioned API. Pinned rather than tracking "latest" for the
#: reason `lead_score_basis` carries a version: a client's CRM outlives our
#: assumptions about the API that wrote into it, and a silently moving version
#: changes what a field means without anything here changing.
API_VERSION = "2026-03"

#: Handoff fields that map onto HubSpot's stock contact properties. These need
#: no configuration because every portal has them.
STANDARD_PROPERTIES = {
    "name": "firstname",
    "company": "company",
    "title": "jobtitle",
}


@register
class HubSpotAdapter(CrmHttp, CrmAdapter):
    provider = "hubspot"

    @classmethod
    def capabilities(cls) -> dict[str, Any]:
        return {
            "custom_fields": True,
            # True in the sense the UI needs — a withdrawal removes the record.
            # What that costs in fidelity is in `retract`'s docstring and in the
            # detail line it returns.
            "retract": True,
            "activities": False,
        }

    # ── mapping ───────────────────────────────────────────────────────────────

    def map(self, handoff: dict[str, Any]) -> dict[str, Any] | None:
        contact = handoff.get("contact") or {}
        email = contact.get("email")
        if not email:
            log.info(
                "hubspot: handoff %s has no email; nothing to key a contact on",
                handoff.get("dedupe_key"),
            )
            return None

        properties: dict[str, Any] = {"email": email}
        for field, hubspot_property in STANDARD_PROPERTIES.items():
            value = contact.get(field)
            if value:
                properties[hubspot_property] = value

        # Everything a booth knows that a CRM does not. Only what the tenant has
        # mapped is sent — see the module docstring. The key set is shared with
        # every other adapter, so a client who changes CRM keeps their mapping.
        mappable = mappable_fields(handoff)
        for field, hubspot_property in self._field_map.items():
            value = mappable.get(field)
            if value is None:
                log.debug(
                    "hubspot: field %r is mapped to %r but this handoff has no "
                    "value for it",
                    field,
                    hubspot_property,
                )
                continue
            properties[hubspot_property] = flatten(value)

        return {"id": email, "idProperty": "email", "properties": properties}

    # ── the calls ─────────────────────────────────────────────────────────────

    async def upsert(
        self, payload: dict[str, Any], *, external_id: str | None = None
    ) -> str | None:
        """One contact, created or updated, returning HubSpot's own id.

        `external_id` is ignored: the upsert is HubSpot's own and keyed on the
        email, so knowing what it called the record last time adds nothing. The
        adapters that need it are the ones with no native upsert.
        """
        if payload is None:
            return None

        body = await self._call(
            "POST",
            f"/crm/objects/{API_VERSION}/contacts/batch/upsert",
            json={"inputs": [payload]},
        )
        results = (body or {}).get("results") or []
        if not results or not results[0].get("id"):
            raise AdapterError(
                f"hubspot accepted the upsert and returned no contact id: {body!r}",
                # Retryable: an upsert is idempotent on the email, so trying
                # again cannot duplicate the contact, and an empty body from a
                # 200 is more likely a blip than a permanent state.
                retryable=True,
            )
        return str(results[0]["id"])

    async def retract(self, external_id: str) -> str:
        """Remove the contact. See the module docstring on what "remove" means."""
        try:
            await self._call(
                "DELETE", f"/crm/objects/{API_VERSION}/contacts/{external_id}"
            )
        except AdapterError as exc:
            if getattr(exc, "status", None) == 404:
                return f"contact {external_id} was already gone from hubspot"
            raise
        return (
            f"contact {external_id} moved to hubspot's recycling bin — removed "
            "from the CRM, restorable by the client for 90 days"
        )

    async def healthcheck(self) -> tuple[bool, str]:
        try:
            await self._call(
                "GET",
                f"/crm/objects/{API_VERSION}/contacts",
                params={"limit": 1},
            )
        except AdapterError as exc:
            return False, str(exc)
        return True, "hubspot reachable, token accepted"

    # ── where ─────────────────────────────────────────────────────────────────

    def _base_url(self) -> str:
        """One host for every portal — the exception among the five adapters."""
        return BASE_URL
