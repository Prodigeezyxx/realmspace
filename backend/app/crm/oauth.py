"""
OAuth2, for the three adapters that need it.

HubSpot and Pipedrive take a long-lived token and are done. Salesforce, Zoho and
Dynamics hand out access tokens that expire in an hour, which means every one of
them has to exchange something at the start of a call. That exchange is the same
shape three times — a form POST to a token endpoint, a bearer token out — so it
is written once here and the adapters supply only the two things that differ:
where to ask, and what to ask with.

## The token is cached on the adapter instance, and that is the whole strategy

`crm.adapter_for` builds an adapter per delivery, so the cache lives for one
lead: at most a refresh and then two or three calls that reuse it. That is
deliberately not a process-wide cache. A shared one would have to be invalidated
when an admin revokes an integration, keyed by tenant so one client's token
could never answer another client's call, and made safe across the consumer
tasks — three ways to leak a credential across a tenant boundary, bought in
exchange for saving one HTTP round trip per lead.

`expires_in` is honoured with a margin because a token that expires between our
check and the CRM's is a 401 on a lead, and the retry it causes costs far more
than refreshing a minute early. The margin only ever shortens the window: a
token whose whole life is less than the margin is exchanged again on the next
call rather than held past the moment it stops working.

## A refusal here is not retryable

A token endpoint that rejects a refresh token is telling us the connection is
over — revoked, rotated, or the client uninstalled the app. `base.Consumer` will
still retry it the fixed number of times; what `retryable=False` changes is the
sentence an admin reads on `/ops`, which should say "go and reconnect this
account" rather than "it will clear".
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app.crm.base import AdapterError

log = logging.getLogger(__name__)

#: Refresh this long before the token actually expires. See the module docstring.
EXPIRY_MARGIN = dt.timedelta(seconds=60)

#: What to assume when a token endpoint does not say. Every provider here does
#: say; this is the floor for one that stops.
DEFAULT_LIFETIME = dt.timedelta(minutes=30)


class OAuthTokenMixin:
    """Adds `_access_token()` to an adapter that talks to a token endpoint."""

    _token: str | None = None
    _token_expires_at: dt.datetime | None = None

    def _token_endpoint(self) -> tuple[str, str]:
        """`(base_url, path)` for this provider's token exchange."""
        raise NotImplementedError

    def _token_form(self) -> dict[str, str]:
        """The form body — the grant, and the credential it is granted against."""
        raise NotImplementedError

    async def _access_token(self) -> str:
        """A live access token, exchanging for one only when we have to."""
        now = dt.datetime.now(dt.timezone.utc)
        if self._token and self._token_expires_at and now < self._token_expires_at:
            return self._token

        base_url, path = self._token_endpoint()
        body = await self._call(  # type: ignore[attr-defined]
            "POST",
            path,
            base_url=base_url,
            data=self._token_form(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            # The exchange *is* the authentication; asking `_auth_headers` for a
            # bearer here would recurse into this method.
            authenticated=False,
        )

        token = (body or {}).get("access_token")
        if not token:
            raise AdapterError(
                f"{self.provider} returned no access_token from its token "  # type: ignore[attr-defined]
                f"endpoint: {body!r} — the connection needs to be re-authorised",
                retryable=False,
            )

        lifetime = DEFAULT_LIFETIME
        expires_in = (body or {}).get("expires_in")
        if expires_in:
            try:
                lifetime = dt.timedelta(seconds=int(expires_in))
            except (TypeError, ValueError):
                log.warning(
                    "%s returned an unreadable expires_in %r; assuming %s",
                    self.provider,  # type: ignore[attr-defined]
                    expires_in,
                    DEFAULT_LIFETIME,
                )

        self._token = str(token)
        # Refresh a margin early, but never cache *past* the lifetime we were
        # given: `max(lifetime - MARGIN, MARGIN)` did the second thing, so a
        # token good for 45s was held for 60 and the last 15 were guaranteed
        # 401s. Clamped from above by the lifetime and from below by a floor, so
        # a short token is simply not cached rather than cached too long.
        usable = min(lifetime, max(lifetime - EXPIRY_MARGIN, dt.timedelta(0)))
        self._token_expires_at = now + usable
        self._on_token(body or {})
        return self._token

    def _on_token(self, body: dict[str, Any]) -> None:
        """Anything else the token response carried.

        Salesforce returns the org's `instance_url` here, which is the host every
        subsequent call has to go to; overriding this is how it takes it.
        """

    async def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._access_token()}"}
