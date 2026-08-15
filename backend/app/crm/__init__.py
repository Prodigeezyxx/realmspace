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
    "register",
    "registry",
]
