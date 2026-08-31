"""
The HTTP half of an AI provider, and the only module in this package that
imports `httpx`.

Deliberately `app/crm/http.py` with a different error class. That file's argument
— "a 429 from Zoho and a 429 from Pipedrive mean the same thing, so the
vocabulary lives in one place with the provider name substituted in" — is not an
argument about CRMs. It is an argument about HTTP, and it holds here with one
provider for the same reason it held there with one.

Not the same module, because the two error types are not interchangeable.
`AdapterError` is what `consumers/base.Consumer` catches to decide whether a
dispatch retries; `LlmError` is what `routers/ask.py` turns into a sentence an
operator reads. A shared mixin raising `AdapterError` would put a model outage
into the dead-letter vocabulary of somebody's CRM.

## The one httpx import

Tests stub the transport by replacing this module's `httpx`, exactly as
`tests/crm_transport.py` does for the CRM package. One seam, and nothing else in
the suite sees a changed httpx.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import httpx

from app.config import get_settings
from app.llm.base import LlmError

log = logging.getLogger(__name__)


class LlmHttp:
    """Mixin: one place that talks to a model vendor."""

    #: Named by `LlmProvider`; repeated here so this mixin can be read alone.
    provider: ClassVar[str]

    def _base_url(self) -> str:
        raise NotImplementedError

    async def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._secret}"}  # type: ignore[attr-defined]

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """One request, parsed, with this package's single error vocabulary.

        `timeout` is its own setting rather than `action_timeout_seconds`: that
        one is 2.0s because `event-bus-spec.md` §4 budgets a rule dispatch at
        under 3s end to end, and every model on this provider's allow-list
        reasons before it answers — a 2s ceiling would time out calls that were
        about to succeed. The ceiling is not the target; `/ask` reports its own
        `took_ms` and that is what the SLA is read from.
        """
        settings = get_settings()
        headers = {"Content-Type": "application/json"}
        headers.update(await self._auth_headers())

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url(),
                timeout=timeout or settings.llm_timeout_seconds,
            ) as client:
                response = await client.request(
                    method, path, json=json, headers=headers
                )
        except httpx.HTTPError as exc:
            raise LlmError(f"{self.provider} unreachable: {exc}", retryable=True) from exc

        if response.status_code >= 400:
            raise self._error(response)

        try:
            payload = response.json()
        except ValueError:
            raise LlmError(
                f"{self.provider} returned {response.status_code} with a body that "
                f"is not JSON: {response.text[:200]!r}",
                retryable=True,
            ) from None

        # A 200 carrying an `error` object. OpenRouter answers this way when an
        # upstream model fails after the request was accepted, and a caller that
        # only checked the status would read the absent `choices` as a crash
        # here rather than as the vendor's own reported failure.
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            error = payload["error"]
            code = error.get("code")
            raise LlmError(
                f"{self.provider} reported an error ({code}): "
                f"{error.get('message', '')}",
                retryable=code not in (400, 401, 403, 404),
            )

        if not isinstance(payload, dict):
            raise LlmError(
                f"{self.provider} replied with {type(payload).__name__}, not an object",
                retryable=True,
            )
        return payload

    def _error(self, response: httpx.Response) -> LlmError:
        """The mapping `app/crm/http.py` makes, for the same reasons.

        429 and 5xx clear by themselves; 401/403 need a human to go and replace
        a key; anything else 4xx is a request to fix rather than repeat.
        """
        detail = response.text[:500]
        status = response.status_code
        provider = self.provider

        if status == 429:
            message = f"{provider} rate-limited this request (429): {detail}"
            retryable = True
        elif status in (401, 403):
            message = (
                f"{provider} rejected the credential ({status}) — the key is "
                f"expired, revoked, or out of credit: {detail}"
            )
            retryable = False
        elif status == 402:
            # Named separately because it is the one an operator can act on
            # without touching the key: the account has run out of credit.
            message = f"{provider} has no credit left for this key (402): {detail}"
            retryable = False
        elif status >= 500:
            message = f"{provider} returned {status}: {detail}"
            retryable = True
        else:
            message = f"{provider} refused the request ({status}): {detail}"
            retryable = False

        error = LlmError(message, retryable=retryable)
        error.status = status  # type: ignore[attr-defined]
        return error


__all__ = ["LlmHttp"]
