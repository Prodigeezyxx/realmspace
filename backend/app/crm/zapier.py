"""
Zapier and Make — `integrations.md` §5, "publish to their webhook so
non-technical users route it anywhere".

## They are not CRMs, and they do not need a second mechanism to be destinations

The registry in `app/crm/__init__.py` is a registry of destinations that happen
to have been CRMs so far. `consumers/crm_delivery.py` already fans a handoff out
over every provider a tenant has connected, claims each one under its own name,
retries it and strands it on `/ops` by itself; `tenant_integration` already holds
one encrypted credential per provider, per tenant. A Zap hook needs every one of
those things and nothing else, so it registers here rather than growing a
parallel path with its own idea of what a stuck delivery looks like.

The difference from `consumers/handoff_delivery.py`, which also POSTs a handoff
to a URL, is who owns the URL. That one is a **deployment** setting — one
endpoint for the whole install, the raw contract, how we dogfood adapters. This
is **per tenant**, stored, revocable, and visible on the integrations screen
beside their CRM.

## The credential is the hook URL, and the signature is not optional

A Zapier catch hook is an unguessable URL, and unguessable is not the same as
authenticated. The body is signed with `sign()` from `app/actions/webhook.py` —
the same function the rule action and the handoff webhook use, as that file asked
— and an unset signing secret refuses to send rather than sending unsigned, for
the reason that file gives: a deployment would silently downgrade the only
protection the receiver has, and the receiver cannot see the config that did it.

## `external_id` is our contact id, not the dedupe key

The obvious choice is the `dedupe_key`, since that is what the receiver upserts
on. It is also `tenant:email`, and `crm_link.external_id` is deliberately *not*
redacted when a retraction succeeds — so choosing it would leave a live email in
our database after an erasure had removed every other copy. The contact id is
stable, ours, names nobody, and travels inside the handoff we send, so a receiver
that stored it can act on the retraction.

## What a retraction here actually means

A POST saying "remove this contact". Whether the client's Zap acts on it is the
client's own business — we do not know what it wrote to, and saying otherwise
would be claiming an erasure we cannot perform. The dispatch detail says exactly
that rather than letting "retracted" imply more than happened, the same honesty
`hubspot.py` shows about its recycling bin.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlsplit

from app.actions.webhook import sign
from app.config import get_settings
from app.crm import register
from app.crm.base import AdapterError, CrmAdapter
from app.crm.http import CrmHttp

log = logging.getLogger(__name__)


class HookAdapter(CrmHttp, CrmAdapter):
    """One tenant's automation hook. Zapier and Make differ only in their name."""

    required_credential_fields = ("url",)
    optional_credential_fields = ("signing_secret",)

    @classmethod
    def parse_secret(cls, raw: str) -> dict[str, Any]:
        """A bare hook URL, or a JSON document that also carries a signing key.

        The bare form is what an admin will paste, because it is the only thing
        Zapier's UI gives them. The JSON form exists for a tenant who wants their
        own signing secret rather than the deployment's.
        """
        raw = raw.strip()
        if not raw:
            raise AdapterError("the credential is empty", retryable=False)

        credential = (
            cls._parse_json_credential(raw)
            if raw.startswith("{")
            else {"url": raw}
        )

        parsed = urlsplit(str(credential["url"]))
        if parsed.scheme != "https" or not parsed.netloc:
            raise AdapterError(
                f"{cls.provider} needs an https hook URL and got "
                f"{credential['url']!r} — a lead with somebody's name in it does "
                "not go out over plain http",
                retryable=False,
            )
        return credential

    @classmethod
    def capabilities(cls) -> dict[str, Any]:
        return {
            # There are no fields to map: the receiver gets the whole handoff and
            # picks what it wants, which is the point of a bring-your-own hook.
            "custom_fields": False,
            # True in the sense the UI needs — a withdrawal sends the notice. See
            # the module docstring for what that does and does not guarantee.
            "retract": True,
            "activities": False,
            # The one destination that can take an anonymous handoff. See the
            # module docstring.
            "anonymous": True,
        }

    # ── mapping ───────────────────────────────────────────────────────────────

    def map(self, handoff: dict[str, Any]) -> dict[str, Any] | None:
        """The contract, raw.

        No field mapping and no declining for a missing email: a hook is not a
        CRM with mandatory columns, and the receiver knows what it wants better
        than we do.

        An **anonymous** handoff is taken too, and it is the reason this adapter
        declares the capability. A CRM has nothing to do with a person who was
        never named; a receiver counting reach has, and a client who turned the
        flag on and connected a hook asked for exactly that. It is keyed on the
        `anon_id` instead, which is all there is — and which is also why no
        `crm_link` is written for one and why no retraction will ever name it.
        There is nothing to retract: a person who was never identified never
        had a record to remove.
        """
        if not (handoff.get("contact") or {}).get("id") and not handoff.get("anon_id"):
            log.info(
                "%s: handoff %s names neither a contact nor a track; there is "
                "nothing for a receiver to key on",
                self.provider,
                handoff.get("dedupe_key"),
            )
            return None
        return handoff

    # ── the calls ─────────────────────────────────────────────────────────────

    async def upsert(
        self, payload: dict[str, Any], *, external_id: str | None = None
    ) -> str | None:
        """POST the handoff, signed. Returns our own contact id — see above."""
        if payload is None:
            return None

        await self._post(
            payload,
            extra_headers={
                # The receiver's own upsert key. Both stages of one lead carry
                # the same value, which is what makes the final handoff an update
                # rather than a second lead — the same header
                # `consumers/handoff_delivery.py` sends, for the same reason.
                "X-Realmspace-Dedupe-Key": str(payload.get("dedupe_key", "")),
            },
        )
        # The contact where there is one, the track where there is not. Only the
        # first is ever written to `crm_link`, which needs a contact id — so an
        # anonymous handoff leaves no link and no retraction will look for one.
        return str((payload.get("contact") or {}).get("id") or payload["anon_id"])

    async def retract(self, external_id: str) -> str:
        await self._post(
            {
                "action": "retract",
                "contact_id": external_id,
                "reason": "consent_withdrawn",
            }
        )
        return (
            f"retraction for contact {external_id} posted to the tenant's "
            f"{self.provider} hook — what the client's automation does with it is "
            "theirs, and this is not a confirmed erasure downstream"
        )

    async def healthcheck(self) -> tuple[bool, str]:
        """Whether the hook is there and accepting.

        A ping rather than a GET: a catch hook only answers POST, and a GET
        against one reports a method error that reads like a broken URL.
        """
        try:
            await self._post({"action": "healthcheck"})
        except AdapterError as exc:
            return False, str(exc)
        return True, f"{self.provider} hook reachable and accepting posts"

    # ── one place that posts ──────────────────────────────────────────────────

    async def _post(
        self, body: dict[str, Any], *, extra_headers: dict[str, str] | None = None
    ) -> None:
        secret = (
            self._credential.get("signing_secret")
            or get_settings().webhook_signing_secret
        )
        if not secret:
            raise AdapterError(
                f"{self.provider} delivery needs a signing secret — either one on "
                "the credential or settings.webhook_signing_secret. Refusing to "
                "POST a lead unsigned, because the receiver cannot tell an "
                "unsigned delivery from a forged one",
                retryable=False,
            )

        # Serialised here rather than handed to httpx as `json=`, because the
        # signature covers exact bytes and a second serialisation with different
        # separators or key order would produce a body the receiver cannot verify.
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Realmspace-Signature": sign(secret, raw),
            **(extra_headers or {}),
        }
        await self._request(
            "POST",
            self._credential["url"],
            content=raw,
            headers=headers,
            authenticated=False,
        )

    def _base_url(self) -> str:
        """Unused for the calls — `_post` passes an absolute URL — but a base
        that pointed anywhere else would be a trap for the next method added."""
        parsed = urlsplit(str(self._credential["url"]))
        return f"{parsed.scheme}://{parsed.netloc}"

    async def _auth_headers(self) -> dict[str, str]:
        """The URL is the credential; there is no bearer token to send."""
        return {}


@register
class ZapierAdapter(HookAdapter):
    provider = "zapier"


@register
class MakeAdapter(HookAdapter):
    provider = "make"
