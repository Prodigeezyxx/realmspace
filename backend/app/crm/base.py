"""
The interface every CRM connector implements.

`integrations.md` §3 wrote this down before any of it existed:

    Adapter interface:
      - authenticate(tenant_credentials)        # OAuth2 / API key, per tenant
      - map(handoff) -> destination_payload     # field mapping (configurable)
      - upsert(payload) -> external_id          # create/update, dedupe-aware
      - retract(external_id)                    # consent withdrawal / erasure
      - healthcheck() -> status
      - capabilities() -> {custom_fields, retract, activities, ...}

This is that, with three things the doc left open now decided.

## `authenticate` is construction, not a method you can forget to call

An adapter is built from a credential (`for_tenant`) and cannot exist without
one. The alternative — a constructed adapter with an `authenticate()` you call
afterwards — has a window in which an unauthenticated adapter is a live object
somebody can call `upsert` on, and the failure that produces is a 401 from a
client's CRM rather than a mistake caught here.

## `upsert` may decline, and declining is not failure

`integrations.md` §2 allows an anonymous handoff, and `consumers/attribution.py`
builds a `dedupe_key` of `tenant:email|anon_id` — so a handoff without an email
is ordinary and reaches an adapter that has nothing to key on. Returning `None`
says "there was nothing here to send"; raising would park a lead on `/ops` that
no human action could resolve.

## A credential is not always one string

HubSpot's is a bearer token and fits in the one encrypted column
`tenant_integration` has. Salesforce, Zoho and Dynamics are OAuth2 and need four
or five fields between them. Rather than widen the table — and re-do
`app/secrets.py`'s binding of a ciphertext to its `(tenant, provider)` — the
secret stays one opaque string and an OAuth adapter's string is a JSON document.

`parse_secret` is what makes that safe rather than merely convenient. It runs at
`PUT /v1/integrations/{provider}`, before anything is encrypted, so an admin who
pastes a HubSpot token into the Salesforce slot is told which fields are missing
there and then. The alternative is a credential that stores cleanly, tests
cleanly if nobody presses test, and fails on the first lead of the activation.

## `upsert` is told the id we recorded last time

HubSpot and Zoho have a native upsert and need nothing. Salesforce and Dynamics
upsert only against a field the client had to create in their own org, and
Pipedrive has no upsert at all — so for those three, "create or update" has to be
decided from something we know. `crm_link` (migration 0008) is exactly that
knowledge: one row per `(tenant, provider, contact_id)` holding the CRM's own id
from the last successful push. The delivery consumer reads it and passes it here.

Adapters with a native upsert ignore the argument. The others update when given
one and fall back to search-then-create only on a genuine first push, which
narrows `hubspot.py`'s duplication argument to the one case it cannot cover:
two stages of the same brand-new contact in flight at the same instant.

## Failure is raised, never returned

Same rule `app/actions/__init__.py` states for rule actions, for the same
reason: `consumers/base.Consumer` already owns retry, exponential backoff and
dead-lettering. An adapter that caught its own 429 and returned a status would
be a second retry policy, disagreeing with the first, and a lead that never
reached the client's CRM would never reach the `/ops` queue either.

`integrations.md` §8 names the failures this covers — "CRMs rate-limit (429), go
down, and change APIs" — and the answer to all three is the same: raise, and let
the loop that was built for it decide.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, ClassVar


class AdapterError(RuntimeError):
    """An adapter could not do what it was asked.

    Carries `retryable` for the human reading `/ops`, not for control flow: the
    consumer retries everything a fixed number of times regardless. What the
    flag changes is the sentence in `rule_dispatch.detail`, which is the
    difference between "HubSpot rate-limited us, it will clear" and "this token
    is not valid any more, go and reconnect it".
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class CrmAdapter(ABC):
    """One tenant's connection to one CRM."""

    #: The key this adapter is registered under and the value stored in
    #: `tenant_integration.provider`. Also what a `crm.retract`'s `destination`
    #: names when it names one at all.
    provider: ClassVar[str]

    #: The fields an OAuth adapter's credential document must carry, and the ones
    #: it may. Empty here because the default credential is a bare token.
    required_credential_fields: ClassVar[tuple[str, ...]] = ()
    optional_credential_fields: ClassVar[tuple[str, ...]] = ()

    def __init__(self, *, secret: str, field_map: dict[str, str]) -> None:
        self._secret = secret
        #: The credential, opened into its parts. For HubSpot that is
        #: `{"token": …}`; for an OAuth provider it is the parsed document.
        self._credential = self.parse_secret(secret)
        #: `integrations.md` §3: "field mapping is per-tenant config, not code —
        #: customers map `spatial_intent.lead_score` → their CRM's custom field".
        self._field_map = field_map or {}

    # ── credentials ───────────────────────────────────────────────────────────

    @classmethod
    def parse_secret(cls, raw: str) -> dict[str, Any]:
        """The stored string, as this adapter's credential.

        Called twice in a credential's life: at `PUT /v1/integrations/{provider}`
        so a malformed document is a 422 rather than a stranded lead, and again
        on construction so an adapter never has to re-parse it mid-call.

        The default treats the whole string as a token, which is what HubSpot
        stores and what every row written before this existed contains.
        """
        raw = raw.strip()
        if not raw:
            raise AdapterError("the credential is empty", retryable=False)
        return {"token": raw}

    @classmethod
    def _parse_json_credential(cls, raw: str) -> dict[str, Any]:
        """A JSON credential document, checked against the declared fields.

        Shared by the OAuth adapters rather than written four times, because
        four copies is four different error messages for the same mistake and
        the admin reading them has no way to know they are the same mistake.
        """
        try:
            document = json.loads(raw)
        except ValueError as exc:
            raise AdapterError(
                f"{cls.provider} expects a JSON credential with the fields "
                f"{list(cls.required_credential_fields)}, and this is not JSON: {exc}",
                retryable=False,
            ) from exc

        if not isinstance(document, dict):
            raise AdapterError(
                f"{cls.provider} expects a JSON object with the fields "
                f"{list(cls.required_credential_fields)}, not a "
                f"{type(document).__name__}",
                retryable=False,
            )

        missing = [
            field
            for field in cls.required_credential_fields
            if not str(document.get(field) or "").strip()
        ]
        if missing:
            raise AdapterError(
                f"{cls.provider} credential is missing {missing} — it needs "
                f"{list(cls.required_credential_fields)}"
                + (
                    f" and may also carry {list(cls.optional_credential_fields)}"
                    if cls.optional_credential_fields
                    else ""
                ),
                retryable=False,
            )

        known = set(cls.required_credential_fields) | set(
            cls.optional_credential_fields
        )
        return {k: v for k, v in document.items() if k in known}

    @classmethod
    def for_tenant(
        cls, *, secret: str, field_map: dict[str, str] | None = None
    ) -> CrmAdapter:
        """`authenticate(tenant_credentials)`, as a constructor.

        The secret arrives decrypted, from the one place that decrypts
        (`app/crm/__init__.py: adapter_for`). Nothing else in the codebase
        should be calling `secrets.decrypt` for a credential.
        """
        return cls(secret=secret, field_map=field_map or {})

    @abstractmethod
    def map(self, handoff: dict[str, Any]) -> dict[str, Any] | None:
        """A `LeadHandoff/v1` payload as this CRM's own shape.

        Returns `None` when this handoff has nothing this CRM can key on — see
        the module docstring. Pure and synchronous on purpose: it is the part
        worth testing exhaustively without a network, and the part a client
        will ask to see when a field lands in the wrong place.
        """

    @abstractmethod
    async def upsert(
        self, payload: dict[str, Any], *, external_id: str | None = None
    ) -> str | None:
        """Create or update, returning the CRM's own id for the record.

        `external_id` is what this CRM called the record the last time we pushed
        this contact, read from `crm_link` by the delivery consumer, or `None` on
        a first push. Adapters with a native upsert ignore it; see the module
        docstring for the three that cannot.

        **Genuinely an upsert.** `integrations.md` §2: "a handoff goes out twice
        … so an adapter's `upsert` must genuinely be an upsert — a create-only
        implementation will duplicate every lead in the client's CRM."

        The returned id is what `crm_link` stores and what `retract` is later
        called with. `None` mirrors `map`: nothing was sent, nothing to record.
        """

    @abstractmethod
    async def retract(self, external_id: str) -> str:
        """Undo a push, for `crm.retract`. Returns a line for the dispatch row.

        Retracting something already retracted must succeed quietly. The
        re-anonymiser is retryable and a withdrawal can be replayed from the
        log, so "not found" is the expected second outcome and not an error.
        """

    @abstractmethod
    async def healthcheck(self) -> tuple[bool, str]:
        """`(ok, detail)` — whether this credential can still talk to the CRM.

        Called by `POST /v1/integrations/{provider}/test` and stored on the row,
        so an expired token is visible on the admin screen before a lead
        strands on it.
        """

    @classmethod
    def capabilities(cls) -> dict[str, Any]:
        """What this CRM can do, for a UI that should not offer what it cannot.

        `integrations.md` §3: "capabilities() lets the UI hide features a CRM
        can't do (e.g. no retract)". A default that claims nothing is the safe
        direction — an adapter that forgets to declare `retract` gets a UI with
        the button hidden, not a withdrawal that silently does nothing.
        """
        return {
            "custom_fields": False,
            "retract": False,
            "activities": False,
            # Can this destination take a handoff with no `contact` at all —
            # `integrations.md` §2's anonymous handoff? False for every CRM, and
            # not as a limitation: there is no record to create for somebody who
            # was never named, and `map` would decline it anyway. True for a
            # bring-your-own hook, whose receiver is counting reach rather than
            # keeping contacts. `consumers/crm_delivery.py` reads it to decide
            # which destinations an anonymous handoff is even offered to.
            "anonymous": False,
        }
