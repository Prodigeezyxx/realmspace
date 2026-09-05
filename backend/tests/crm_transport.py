"""
One seam for stubbing an outbound CRM call, shared by five adapter test modules.

`app/crm/http.py` is the only module in the CRM package that imports `httpx`, so
replacing its `httpx` is enough to intercept every adapter's traffic. Patched at
that module rather than at `httpx.AsyncClient`, so nothing else in the suite —
the webhook delivery, the rule actions — sees a changed httpx.

Each adapter's own test file supplies the handler, because what is worth
asserting is what was actually sent: the endpoint, the upsert key, the mapped
properties. That is where a field map lands in the wrong place.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from app.crm import http as crm_http


def stub_transport(monkeypatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Route every adapter request through `handler`, with no network."""

    class _Httpx:
        HTTPError = httpx.HTTPError

        @staticmethod
        def AsyncClient(**kwargs):  # noqa: N802 — mirrors httpx's own name
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(crm_http, "httpx", _Httpx)
