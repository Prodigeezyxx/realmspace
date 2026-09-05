"""
The HTTP half every adapter shares.

`hubspot.py` grew a `_call` and an `_error` because "every request goes through
here, so the error vocabulary is one thing". That argument does not stop at one
adapter — it gets stronger with five. A 429 from Zoho and a 429 from Pipedrive
mean the same thing to `consumers/base.Consumer`, and an operator reading `/ops`
should not have to learn which adapter phrases a dead token which way.

So the vocabulary lives here once, with the provider name substituted in, and an
adapter is left holding only the part that is genuinely its own: which URL, which
headers, which shape of body.

## Why `retryable` is decided here and not per adapter

`base.AdapterError` carries `retryable` for the sentence in
`rule_dispatch.detail`, not for control flow. The mapping — 429 and 5xx clear by
themselves, 401/403 need a human to reconnect the account, 4xx is a request to
fix rather than repeat — is a property of HTTP and of what the consumer does
next, not of the vendor. An adapter that needs to disagree overrides `_error`,
and has to say why.

## The one import of `httpx` in the CRM package

Tests stub the transport by replacing this module's `httpx`, which is why the
adapters do not import it themselves. One seam, and every adapter's tests use
the same one.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import httpx

from app.config import get_settings
from app.crm.base import AdapterError

log = logging.getLogger(__name__)


class CrmHttp:
    """Mixin: one place that talks to somebody else's API."""

    #: Named by `CrmAdapter`; repeated here so this mixin can be read alone.
    provider: ClassVar[str]

    def _base_url(self) -> str:
        """Where this tenant's records live.

        A method rather than a constant because three of the five providers have
        a per-tenant host — Salesforce's `instance_url`, Zoho's datacentre, the
        Dynamics organisation URL — and a wrong host is a silent 401 rather than
        anything that reads like a misconfiguration.
        """
        raise NotImplementedError

    async def _auth_headers(self) -> dict[str, str]:
        """The credential, as headers. Awaited because OAuth may refresh first."""
        return {"Authorization": f"Bearer {self._credential['token']}"}  # type: ignore[attr-defined]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | list[Any] | None = None,
        data: dict[str, Any] | None = None,
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        base_url: str | None = None,
        authenticated: bool = True,
    ) -> httpx.Response:
        """One request, with this package's single error vocabulary behind it.

        Returns the response itself, for the callers that need something other
        than a JSON body — Dynamics answers an upsert with the record's id in an
        `OData-EntityId` header, and a helper that could only return a parsed
        body would have sent them back to writing their own client.

        `data` is form-encoded rather than JSON, which is what every OAuth token
        endpoint here takes. `content` is exact bytes, for the one caller whose
        body is covered by a signature — a second serialisation with different
        separators or key order produces a body the receiver cannot verify. Failures raise (`base.py`: the consumer owns retry)
        and never return a status for a caller to inspect and forget to check.
        """
        settings = get_settings()
        request_headers = dict(headers or {})
        if authenticated:
            request_headers.update(await self._auth_headers())

        try:
            async with httpx.AsyncClient(
                base_url=base_url or self._base_url(),
                timeout=settings.action_timeout_seconds,
            ) as client:
                response = await client.request(
                    method,
                    path,
                    json=json,
                    data=data,
                    content=content,
                    params=params,
                    headers=request_headers,
                )
        except httpx.HTTPError as exc:
            raise AdapterError(
                f"{self.provider} unreachable: {exc}", retryable=True
            ) from exc

        if response.status_code >= 400:
            raise self._error(response)
        return response

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | list[Any] | None = None,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        base_url: str | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any] | None:
        """`_request`, parsed. `None` when the call answered with no body."""
        response = await self._request(
            method,
            path,
            json=json,
            data=data,
            params=params,
            headers=headers,
            base_url=base_url,
            authenticated=authenticated,
        )

        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            # A 200 that is not JSON is a proxy or a login page standing in for
            # the API. Retryable, because that is usually a network in the way.
            raise AdapterError(
                f"{self.provider} returned {response.status_code} with a body "
                f"that is not JSON: {response.text[:200]!r}",
                retryable=True,
            ) from None

    def _error(self, response: httpx.Response) -> AdapterError:
        detail = response.text[:500]
        status = response.status_code
        provider = self.provider

        if status == 429:
            # `integrations.md` §8's first named failure. Retryable, and
            # `base.Consumer`'s bounded backoff is the only retry policy — a
            # second one here would disagree with it.
            message = f"{provider} rate-limited this request (429): {detail}"
            retryable = True
        elif status in (401, 403):
            message = (
                f"{provider} rejected the credential ({status}) — the token is "
                f"expired, revoked, or missing a scope: {detail}"
            )
            retryable = False
        elif status == 404:
            message = f"{provider} has no such record (404): {detail}"
            retryable = False
        elif status >= 500:
            message = f"{provider} returned {status}: {detail}"
            retryable = True
        else:
            # A 400 is usually a field the destination does not have, which is a
            # field map to fix rather than a call to repeat.
            message = f"{provider} refused the request ({status}): {detail}"
            retryable = False

        error = AdapterError(message, retryable=retryable)
        error.status = status  # type: ignore[attr-defined]
        return error
