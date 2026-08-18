"""
Zoho CRM — `integrations.md` §4's fourth adapter, "strong in our SAM (Africa +
emerging markets)".

## The datacentre is part of the credential, because the wrong one is a 401

Zoho runs separate, non-interchangeable datacentres — `.com`, `.eu`, `.in`,
`.com.au`, `.jp` — and an account in one is invisible to the others. A token
issued in the EU sent to the `.com` host does not fail as "wrong region"; it
fails as an authentication error, which reads to an admin as a bad token and
sends them off to rotate a credential that was fine. So the region is a declared
field of the credential document and both hosts are derived from it.

`integrations.md` §4 puts Zoho in our SAM specifically because of where our
clients are. Getting this wrong would be getting it wrong for exactly them.

## The upsert is Zoho's own

`POST /crm/v6/Leads/upsert` with `duplicate_check_fields: ["Email"]` creates or
updates in one call, so `external_id` is not needed here — the same position
HubSpot is in, and for the same reason: a search-then-create has a race the
floor would find, and `integrations.md` §2 says what it costs.

## Mandatory fields, and what retract does

Zoho will not create a Lead without `Last_Name`, and a `Company` is required for
the standard layout — see `app/crm/lead_fields.py` for why the answer is a
placeholder that says nobody gave us one, rather than a surname guessed from an
email. `DELETE /crm/v6/Leads/{id}` moves the record to the org's Recycle Bin,
restorable for 60 days; the dispatch row says so rather than letting "retracted"
imply more than happened.

## Zoho answers 200 with failure inside

Unlike the others here, a rejected record comes back inside a `data` array with
`"code": "SUCCESS"` replaced by an error code, on an HTTP 200 — so
`CrmHttp._error` never sees it. Every call therefore checks the body, and raises
the way an HTTP error would. A destination that reported success because the
transport succeeded would put leads on the delivered pile that Zoho refused.
"""

from __future__ import annotations

import logging
from typing import Any

from app.crm import register
from app.crm.base import AdapterError, CrmAdapter
from app.crm.http import CrmHttp
from app.crm.lead_fields import UNKNOWN_COMPANY, flatten, mappable_fields, split_name
from app.crm.oauth import OAuthTokenMixin

log = logging.getLogger(__name__)

API_VERSION = "v6"

#: The datacentre suffixes Zoho operates. Checked rather than interpolated
#: blindly, so a typo is a refused credential instead of a request to a domain
#: somebody else owns.
REGIONS = ("com", "eu", "in", "com.au", "jp", "com.cn", "sa", "ca")

DEFAULT_REGION = "com"

#: Handoff contact fields that map onto stock Lead fields.
STANDARD_FIELDS = {"title": "Designation"}

#: Per-record codes that mean "there is no such record", which is a retraction
#: that has nothing left to do rather than a retraction that failed. Zoho answers
#: a delete for an id it does not have with one of these inside an HTTP 200.
GONE_CODES = ("INVALID_DATA", "RESOURCE_NOT_FOUND")


@register
class ZohoAdapter(OAuthTokenMixin, CrmHttp, CrmAdapter):
    provider = "zoho"

    required_credential_fields = ("client_id", "client_secret", "refresh_token")
    optional_credential_fields = ("region", "default_company")

    @classmethod
    def parse_secret(cls, raw: str) -> dict[str, Any]:
        credential = cls._parse_json_credential(raw)
        region = credential.get("region") or DEFAULT_REGION
        if region not in REGIONS:
            raise AdapterError(
                f"zoho region {region!r} is not one Zoho operates — it is one of "
                f"{list(REGIONS)}, and the wrong one authenticates as a bad token",
                retryable=False,
            )
        credential["region"] = region
        return credential

    @classmethod
    def capabilities(cls) -> dict[str, Any]:
        return {"custom_fields": True, "retract": True, "activities": False}

    # ── mapping ───────────────────────────────────────────────────────────────

    def map(self, handoff: dict[str, Any]) -> dict[str, Any] | None:
        contact = handoff.get("contact") or {}
        email = contact.get("email")
        if not email:
            log.info(
                "zoho: handoff %s has no email; nothing to key a lead on",
                handoff.get("dedupe_key"),
            )
            return None

        first, last = split_name(contact.get("name"))
        record: dict[str, Any] = {
            "Email": email,
            "Last_Name": last,
            "Company": contact.get("company")
            or self._credential.get("default_company")
            or UNKNOWN_COMPANY,
            "Lead_Source": "realmspace",
        }
        if first:
            record["First_Name"] = first
        for field, zoho_field in STANDARD_FIELDS.items():
            value = contact.get(field)
            if value:
                record[zoho_field] = value

        mapped = mappable_fields(handoff)
        for field, zoho_field in self._field_map.items():
            value = mapped.get(field)
            if value is None:
                log.debug(
                    "zoho: field %r is mapped to %r but this handoff has no value "
                    "for it",
                    field,
                    zoho_field,
                )
                continue
            record[zoho_field] = flatten(value)

        return {"data": [record], "duplicate_check_fields": ["Email"]}

    # ── the calls ─────────────────────────────────────────────────────────────

    async def upsert(
        self, payload: dict[str, Any], *, external_id: str | None = None
    ) -> str | None:
        """One Lead, created or updated, keyed on the email by Zoho itself."""
        if payload is None:
            return None

        body = await self._call("POST", f"/crm/{API_VERSION}/Leads/upsert", json=payload)
        record = _first_record(body, action="upsert")
        record_id = (record.get("details") or {}).get("id")
        if not record_id:
            raise AdapterError(
                f"zoho accepted the upsert and returned no lead id: {body!r}",
                # The upsert is keyed on the email, so a repeat cannot duplicate.
                retryable=True,
            )
        return str(record_id)

    async def retract(self, external_id: str) -> str:
        try:
            body = await self._call(
                "DELETE", f"/crm/{API_VERSION}/Leads/{external_id}"
            )
            if body is not None:
                # An empty body is a delete that happened and said nothing.
                # Zoho's own answer is a `data` array, so this is the tolerant
                # reading of a proxy or a future version trimming a 204.
                _first_record(body, action="delete")
        except AdapterError as exc:
            if getattr(exc, "status", None) == 404 or getattr(
                exc, "code", None
            ) in GONE_CODES:
                # Zoho reports a record that is not there in the body of an
                # HTTP 200, not as a 404 — see `_first_record`. Without this the
                # 404 arm never fires for Zoho and a replayed withdrawal, which
                # `repository.links_for_contact` says "should find nothing left
                # to do", instead retries to exhaustion and parks on `/ops`
                # claiming a retraction failed that had already succeeded.
                return f"lead {external_id} was already gone from zoho"
            raise
        return (
            f"lead {external_id} moved to zoho's recycle bin — removed from the "
            "CRM, restorable by the client for 60 days"
        )

    async def healthcheck(self) -> tuple[bool, str]:
        try:
            await self._call(
                "GET", f"/crm/{API_VERSION}/Leads", params={"per_page": 1}
            )
        except AdapterError as exc:
            return False, str(exc)
        region = self._credential["region"]
        return True, f"zoho reachable in the .{region} datacentre, token accepted"

    # ── where, and with what ──────────────────────────────────────────────────

    def _base_url(self) -> str:
        return f"https://www.zohoapis.{self._credential['region']}"

    def _token_endpoint(self) -> tuple[str, str]:
        return (
            f"https://accounts.zoho.{self._credential['region']}",
            "/oauth/v2/token",
        )

    def _token_form(self) -> dict[str, str]:
        return {
            "grant_type": "refresh_token",
            "client_id": self._credential["client_id"],
            "client_secret": self._credential["client_secret"],
            "refresh_token": self._credential["refresh_token"],
        }


def _first_record(body: dict[str, Any] | None, *, action: str) -> dict[str, Any]:
    """The one record we sent, or the reason Zoho refused it on an HTTP 200.

    See the module docstring: a per-record error arrives inside a successful
    response, so it has to be raised here or a refused lead is recorded as
    delivered. `INVALID_DATA` and `MANDATORY_NOT_FOUND` are a field map to fix
    rather than a call to repeat, so they are not retryable; anything else is
    left retryable, because guessing that an unfamiliar code is permanent is the
    error that loses a lead.
    """
    records = (body or {}).get("data") or []
    if not records:
        raise AdapterError(
            f"zoho answered the {action} with no record: {body!r}", retryable=True
        )

    record = records[0]
    code = record.get("code")
    if code and code != "SUCCESS":
        error = AdapterError(
            f"zoho refused the {action} ({code}): {record.get('message')!r} "
            f"{record.get('details') or {}}",
            retryable=code not in ("INVALID_DATA", "MANDATORY_NOT_FOUND", "DUPLICATE_DATA"),
        )
        # Carried the way `CrmHttp._error` carries `status`, and for the same
        # reason: `retract` has to tell "no such record" from "refused", and the
        # code is the only thing that says which on an HTTP 200.
        error.code = code  # type: ignore[attr-defined]
        raise error
    return record
