"""
Which CRMs this deployment can talk to, and the one place a credential is opened.

`registry` maps a provider name to an adapter class, the same shape
`app/actions/__init__.py` uses for rule actions so the two extension points read
alike. Adding a CRM is adding a module and a line here.

## A closed set, unlike the event taxonomy

`event-bus-spec.md` §3's types are additive and open — a rule may name a trigger
whose producer has not shipped. Providers are the opposite: a credential stored
for a provider nothing can dispatch is a client's token sitting in our database
doing nothing, and an admin who believes their CRM is connected. So
`PUT /v1/integrations/{provider}` refuses a name that is not here.

## Decryption happens here and nowhere else

`adapter_for` is the only caller of `secrets.decrypt` for a tenant credential.
Keeping it to one function is what makes "the plaintext never leaves this call"
a property somebody can check rather than a convention — the routers hand back
`secret_hint`, the repository stores ciphertext it cannot read, and an adapter
receives the secret already open.
"""

from __future__ import annotations

from app import secrets
from app.crm.base import AdapterError, CrmAdapter
from app.models import TenantIntegration

#: provider name → adapter class. `integrations.md` §4's build order is HubSpot,
#: Salesforce, Pipedrive, Zoho, Dynamics; each lands here as it is written.
registry: dict[str, type[CrmAdapter]] = {}


def register(adapter: type[CrmAdapter]) -> type[CrmAdapter]:
    """Add an adapter to the registry under its own `provider`."""
    registry[adapter.provider] = adapter
    return adapter


def known(provider: str) -> bool:
    return provider in registry


def parse_secret(provider: str, raw: str) -> None:
    """Check a credential is the shape this provider needs, before storing it.

    Called by `PUT /v1/integrations/{provider}` so a malformed credential is a
    422 naming the missing fields, rather than a token that stores cleanly and
    fails on the first lead of a three-day activation. Raises `AdapterError`;
    the parsed value is deliberately thrown away, because the only place that
    should hold an open credential is `adapter_for`.
    """
    cls = registry.get(provider)
    if cls is None:
        raise AdapterError(
            f"no adapter for provider {provider!r} in this build", retryable=False
        )
    cls.parse_secret(raw)


def adapter_for(integration: TenantIntegration) -> CrmAdapter:
    """Open a stored credential and hand back a usable adapter.

    Raises `AdapterError` rather than `KeyError` for an unregistered provider:
    the callers are a consumer and a router, and both want one thing to catch.
    A row whose provider has since been removed from a build is a real state —
    an adapter withdrawn between deployments — and it should read as "this
    deployment cannot talk to that CRM", not as a crash.
    """
    cls = registry.get(integration.provider)
    if cls is None:
        raise AdapterError(
            f"no adapter for provider {integration.provider!r} in this build",
            retryable=False,
        )
    secret = secrets.decrypt(
        integration.secret_ct,
        tenant_id=integration.tenant_id,
        provider=integration.provider,
    )
    return cls.for_tenant(secret=secret, field_map=integration.field_map or {})


__all__ = [
    "AdapterError",
    "CrmAdapter",
    "adapter_for",
    "known",
    "parse_secret",
    "register",
    "registry",
]

# Imported for their side effect: each adapter module registers itself with the
# decorator above, and a registry that only fills up once somebody imports the
# right module is a registry that is empty in exactly the process that needed
# it. At the foot of the file because the adapters import `register` from here.
#
# In `integrations.md` §4's build order, which is also roughly the order a
# client is likely to be on one of them.
from app.crm import hubspot as _hubspot  # noqa: E402,F401  isort:skip
from app.crm import salesforce as _salesforce  # noqa: E402,F401  isort:skip
from app.crm import pipedrive as _pipedrive  # noqa: E402,F401  isort:skip
from app.crm import zoho as _zoho  # noqa: E402,F401  isort:skip
from app.crm import dynamics as _dynamics  # noqa: E402,F401  isort:skip

# Not CRMs. `integrations.md` §5's bring-your-own hooks, which need a per-tenant
# credential, a claim, a retry and a row on `/ops` and nothing else — so they are
# destinations in this registry rather than a parallel path with its own idea of
# what a stuck delivery looks like.
from app.crm import zapier as _zapier  # noqa: E402,F401  isort:skip
