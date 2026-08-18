"""
Pipedrive — `integrations.md` §4's third adapter, "SMB/agency favourite".

## The one adapter with no upsert at all, and what stands in for it

HubSpot has `batch/upsert`, Zoho has `Leads/upsert`, Salesforce and Dynamics
upsert against a key the client configures. Pipedrive has none of that: a Person
is created by POST and changed by PATCH, and finding out which to use is our
problem.

`hubspot.py` calls search-then-create "a create-only implementation with extra
steps", and it is right — but only about the case where the search is the *only*
thing deciding. Here it is the last resort, behind something better:

  1. `crm_link` (migration 0008) holds the Person id from the last push of this
     contact, and the delivery consumer passes it in as `external_id`. That
     covers every push after the first, which is every `final` stage of a lead
     the `identified` stage already delivered.
  2. Only on a genuine first push do we search by email, and update if the
     client already had that person from another source — which is the right
     answer anyway, since creating a second Person for an email their CRM
     already knows is the duplication we are trying to avoid.

What is left uncovered is one case: both stages of a brand-new contact in flight
at the same instant, both searching, both missing, both creating. The dispatch
claim rules out the ordinary version of this (a retry of the same stage), and the
two stages are normally a session apart — `identified` fires at the badge scan
and `final` at `session.ended`. A badge scanned in the last second of an
activation is the window, and it is stated here rather than hidden because the
recovery is a client merging two Persons, not silent data loss.

## Retract is a real delete

`DELETE /api/v2/persons/{id}` marks the Person deleted and Pipedrive purges it
some hours later; there is no user-facing recycle bin to restore from. That is
closer to erasure than HubSpot's or Salesforce's bins, and the dispatch detail
says so — an operator answering "what happened to my data" should not have to
know which CRM the client uses to know what the answer is.
"""

from __future__ import annotations

import logging
from typing import Any

from app.crm import register
from app.crm.base import AdapterError, CrmAdapter
from app.crm.http import CrmHttp
from app.crm.lead_fields import flatten, mappable_fields, split_name

log = logging.getLogger(__name__)

BASE_URL = "https://api.pipedrive.com"

#: v2 is the current Persons API. `users/me` is only on v1, which is why the
#: healthcheck below reads from a different version than everything else.
API = "/api/v2"


@register
class PipedriveAdapter(CrmHttp, CrmAdapter):
    provider = "pipedrive"

    @classmethod
    def capabilities(cls) -> dict[str, Any]:
        return {"custom_fields": True, "retract": True, "activities": False}

    # ── mapping ───────────────────────────────────────────────────────────────

    def map(self, handoff: dict[str, Any]) -> dict[str, Any] | None:
        contact = handoff.get("contact") or {}
        email = contact.get("email")
        if not email:
            log.info(
                "pipedrive: handoff %s has no email; nothing to key a person on",
                handoff.get("dedupe_key"),
            )
            return None

        first, last = split_name(contact.get("name"))
        person: dict[str, Any] = {
            "name": contact.get("name") or (last if first is None else f"{first} {last}"),
            "emails": [{"value": email, "primary": True, "label": "work"}],
        }
        if contact.get("title"):
            person["job_title"] = contact["title"]

        # Pipedrive's custom fields are addressed by a 40-character hash, not by
        # a name, and they live under their own object rather than at the top
        # level. A tenant's field map holds those hashes; nothing here can guess
        # one, which is the same reason `hubspot.py` sends only what is mapped.
        mapped = mappable_fields(handoff)
        custom: dict[str, Any] = {}
        for field, pipedrive_key in self._field_map.items():
            value = mapped.get(field)
            if value is None:
                log.debug(
                    "pipedrive: field %r is mapped to %r but this handoff has no "
                    "value for it",
                    field,
                    pipedrive_key,
                )
                continue
            custom[pipedrive_key] = flatten(value)
        if custom:
            person["custom_fields"] = custom

        # Carried alongside rather than inside: the search fallback needs the
        # email, and `company` has no home on a Pipedrive Person — it is an
        # Organization, which this adapter deliberately does not create. See the
        # note in `upsert`.
        return {"person": person, "email": email, "company": contact.get("company")}

    # ── the calls ─────────────────────────────────────────────────────────────

    async def upsert(
        self, payload: dict[str, Any], *, external_id: str | None = None
    ) -> str | None:
        """Update the Person we already pushed, or find one, or create one.

        No Organization is created for `company`. Pipedrive models it as a
        separate object with its own deduplication, and inventing one per booth
        visitor would litter a client's CRM with companies their sales team
        never agreed to. The value travels in a mapped custom field if the
        tenant wants it.
        """
        if payload is None:
            return None

        person = payload["person"]

        if external_id:
            try:
                await self._call("PATCH", f"{API}/persons/{external_id}", json=person)
                return str(external_id)
            except AdapterError as exc:
                if getattr(exc, "status", None) != 404:
                    raise
                # Deleted in Pipedrive since our last push. Fall through and
                # create it again rather than failing — the client removed a
                # record, they did not ask us to stop sending leads.
                log.info(
                    "pipedrive: person %s is gone; creating a new one", external_id
                )

        found = await self._search(payload["email"])
        if found:
            await self._call("PATCH", f"{API}/persons/{found}", json=person)
            return str(found)

        body = await self._call("POST", f"{API}/persons", json=person)
        created = ((body or {}).get("data") or {}).get("id")
        if not created:
            raise AdapterError(
                f"pipedrive accepted the person and returned no id: {body!r}",
                # Not idempotent, and that is exactly why this is retryable: the
                # retry searches first and finds the person this call created.
                retryable=True,
            )
        return str(created)

    async def retract(self, external_id: str) -> str:
        try:
            await self._call("DELETE", f"{API}/persons/{external_id}")
        except AdapterError as exc:
            if getattr(exc, "status", None) == 404:
                return f"person {external_id} was already gone from pipedrive"
            raise
        return (
            f"person {external_id} deleted from pipedrive — marked deleted now "
            "and purged by pipedrive shortly after, with no restore for the client"
        )

    async def healthcheck(self) -> tuple[bool, str]:
        try:
            await self._call("GET", "/v1/users/me")
        except AdapterError as exc:
            return False, str(exc)
        return True, "pipedrive reachable, api token accepted"

    # ── where, and with what ──────────────────────────────────────────────────

    async def _search(self, email: str) -> str | None:
        """The id of an existing Person with this exact email, if there is one."""
        body = await self._call(
            "GET",
            f"{API}/persons/search",
            params={"term": email, "fields": "email", "exact_match": "true", "limit": 1},
        )
        items = ((body or {}).get("data") or {}).get("items") or []
        for entry in items:
            found = (entry.get("item") or {}).get("id")
            if found:
                return str(found)
        return None

    def _base_url(self) -> str:
        return BASE_URL

    async def _auth_headers(self) -> dict[str, str]:
        """Pipedrive's own header, not a bearer.

        The alternative it also accepts is `?api_token=` in the query string,
        which would put a client's credential into every proxy log between here
        and them.
        """
        return {"x-api-token": self._credential["token"]}
