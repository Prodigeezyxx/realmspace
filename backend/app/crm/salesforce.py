"""
Salesforce — `integrations.md` §4's second adapter, "enterprise default; highest
ASP deals need it".

## The upsert is real, and it costs the client one custom field

Salesforce's upsert is `PATCH /sobjects/Lead/{externalIdField}/{value}`, and it
only works against a field the org has marked as an External ID. There is no
stock field with that flag, so the client creates one — a text field, unique,
external id — and names it in the credential document. `healthcheck` checks it
exists, because the alternative is discovering it on the first lead of a
three-day activation.

The value we key on is the handoff's `dedupe_key` (`tenant:email`), not the email
itself. Both handoff stages of one visitor carry the same one, which is what
makes the second push an update; and keying on our own identifier rather than on
theirs means a client who later changes an email in Salesforce does not get a
second lead from us the next time that person walks into a booth.

## Why `Lead` and not `Contact`

A Salesforce Contact belongs to an Account — an existing customer relationship.
Somebody who scanned a badge at a stand has no account, and creating one to hold
them would put fictitious companies in a client's CRM. `Lead` is the object
Salesforce ships for exactly this, and the client's own conversion process is
what turns it into a Contact if it earns it.

## Mandatory fields we cannot get from a badge

Salesforce will not create a Lead without `LastName` and `Company`. See
`app/crm/lead_fields.py` for why the answer is a placeholder that says so rather
than a surname guessed from an email address.

## What retract actually does

`DELETE /sobjects/Lead/{id}` moves the record to the org's Recycle Bin, where it
is restorable for 15 days. That is a removal, not an erasure, and the dispatch
row says which — the same honesty `hubspot.py` shows about its own 90-day bin. A
true erasure is the erasure job's problem, where the question is answered for
every destination at once.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from app.crm import register
from app.crm.base import AdapterError, CrmAdapter
from app.crm.http import CrmHttp
from app.crm.lead_fields import (
    UNKNOWN_COMPANY,
    flatten,
    mappable_fields,
    split_name,
)
from app.crm.oauth import OAuthTokenMixin

log = logging.getLogger(__name__)

#: Pinned, for the reason `hubspot.py` pins its own: a client's CRM outlives our
#: assumptions about the API that wrote into it.
API_VERSION = "v62.0"

DEFAULT_LOGIN_URL = "https://login.salesforce.com"

#: The custom External ID field a client creates for us. Overridable, because an
#: org with a naming convention will have one.
DEFAULT_EXTERNAL_ID_FIELD = "realmspace_dedupe_key__c"

#: Handoff contact fields that map onto stock Lead fields.
STANDARD_FIELDS = {"company": "Company", "title": "Title"}


@register
class SalesforceAdapter(OAuthTokenMixin, CrmHttp, CrmAdapter):
    provider = "salesforce"

    required_credential_fields = (
        "instance_url",
        "client_id",
        "client_secret",
        "refresh_token",
    )
    optional_credential_fields = (
        "login_url",
        "external_id_field",
        "default_company",
    )

    @classmethod
    def parse_secret(cls, raw: str) -> dict[str, Any]:
        return cls._parse_json_credential(raw)

    @classmethod
    def capabilities(cls) -> dict[str, Any]:
        return {
            "custom_fields": True,
            # True in the sense the UI needs — a withdrawal removes the record.
            # What that costs in fidelity is in the module docstring.
            "retract": True,
            "activities": False,
        }

    # ── mapping ───────────────────────────────────────────────────────────────

    def map(self, handoff: dict[str, Any]) -> dict[str, Any] | None:
        contact = handoff.get("contact") or {}
        email = contact.get("email")
        if not email:
            log.info(
                "salesforce: handoff %s has no email; nothing to key a lead on",
                handoff.get("dedupe_key"),
            )
            return None

        dedupe_key = handoff.get("dedupe_key")
        if not dedupe_key:
            # The upsert keys on it, so without one there is no way to make a
            # second push an update rather than a duplicate lead.
            raise AdapterError(
                "salesforce cannot upsert a handoff with no dedupe_key",
                retryable=False,
            )

        first, last = split_name(contact.get("name"))
        fields: dict[str, Any] = {
            "Email": email,
            "LastName": last,
            "Company": contact.get("company")
            or self._credential.get("default_company")
            or UNKNOWN_COMPANY,
            self._external_id_field: dedupe_key,
        }
        if first:
            fields["FirstName"] = first
        for field, salesforce_field in STANDARD_FIELDS.items():
            value = contact.get(field)
            if value:
                fields[salesforce_field] = value

        mapped = mappable_fields(handoff)
        for field, salesforce_field in self._field_map.items():
            value = mapped.get(field)
            if value is None:
                log.debug(
                    "salesforce: field %r is mapped to %r but this handoff has "
                    "no value for it",
                    field,
                    salesforce_field,
                )
                continue
            fields[salesforce_field] = flatten(value)

        return {"external_id_value": dedupe_key, "fields": fields}

    # ── the calls ─────────────────────────────────────────────────────────────

    async def upsert(
        self, payload: dict[str, Any], *, external_id: str | None = None
    ) -> str | None:
        """Create or update one Lead, keyed on our own dedupe key.

        `external_id` is not needed and not used: Salesforce's upsert addresses
        the record by the external id field, so the record can be found without
        knowing what Salesforce called it.
        """
        if payload is None:
            return None

        key = quote(str(payload["external_id_value"]), safe="")
        response = await self._request(
            "PATCH",
            f"/services/data/{API_VERSION}/sobjects/Lead/{self._external_id_field}/{key}",
            json=payload["fields"],
        )

        body = response.json() if response.content else None
        record_id = (body or {}).get("id")
        if record_id:
            return str(record_id)

        # An update answers 204 with no body in every API version we support, so
        # the id has to be asked for. One extra call, and only on the update
        # path — the alternative is a `crm_link` with no `external_id`, which is
        # a withdrawal that cannot be carried out.
        found = await self._call(
            "GET",
            f"/services/data/{API_VERSION}/sobjects/Lead/{self._external_id_field}/{key}",
            params={"fields": "Id"},
        )
        record_id = (found or {}).get("Id")
        if not record_id:
            raise AdapterError(
                f"salesforce accepted the upsert of {payload['external_id_value']!r} "
                f"and no lead can be found under it: {body!r}",
                # The upsert is idempotent on the external id, so a repeat cannot
                # duplicate the lead — and an accepted write with nothing behind
                # it is more likely replication lag than a permanent state.
                retryable=True,
            )
        return str(record_id)

    async def retract(self, external_id: str) -> str:
        """Delete the Lead. See the module docstring on what "delete" means."""
        try:
            await self._call(
                "DELETE", f"/services/data/{API_VERSION}/sobjects/Lead/{external_id}"
            )
        except AdapterError as exc:
            if getattr(exc, "status", None) == 404:
                return f"lead {external_id} was already gone from salesforce"
            raise
        return (
            f"lead {external_id} moved to salesforce's recycle bin — removed from "
            "the CRM, restorable by the client for 15 days"
        )

    async def healthcheck(self) -> tuple[bool, str]:
        """Can we authenticate, and does the field the upsert needs exist?

        Two questions in one call, because a valid credential pointed at an org
        with no External ID field is a connection that tests green and fails on
        every lead.
        """
        try:
            described = await self._call(
                "GET", f"/services/data/{API_VERSION}/sobjects/Lead/describe"
            )
        except AdapterError as exc:
            return False, str(exc)

        names = {
            field.get("name") for field in (described or {}).get("fields") or []
        }
        if self._external_id_field not in names:
            return False, (
                f"salesforce reachable, but this org has no Lead field named "
                f"{self._external_id_field!r} — create it as a unique text "
                "External ID, or name the one you already have in the credential"
            )
        return True, (
            f"salesforce reachable, token accepted, upserting on "
            f"{self._external_id_field}"
        )

    # ── where, and with what ──────────────────────────────────────────────────

    @property
    def _external_id_field(self) -> str:
        return (
            self._credential.get("external_id_field") or DEFAULT_EXTERNAL_ID_FIELD
        )

    def _base_url(self) -> str:
        """The org's own host.

        Taken from the credential, and replaced by whatever the token exchange
        returns — a sandbox refresh, an org migration or a My Domain change all
        move the host, and the token response is the only thing that knows.
        """
        return str(self._credential["instance_url"]).rstrip("/")

    def _token_endpoint(self) -> tuple[str, str]:
        login_url = self._credential.get("login_url") or DEFAULT_LOGIN_URL
        return str(login_url).rstrip("/"), "/services/oauth2/token"

    def _token_form(self) -> dict[str, str]:
        return {
            "grant_type": "refresh_token",
            "client_id": self._credential["client_id"],
            "client_secret": self._credential["client_secret"],
            "refresh_token": self._credential["refresh_token"],
        }

    def _on_token(self, body: dict[str, Any]) -> None:
        instance_url = body.get("instance_url")
        if instance_url:
            self._credential["instance_url"] = instance_url

