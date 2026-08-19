"""
Which AI providers this deployment can talk to, and the one place a key is opened.

The same shape as `app/crm/__init__.py`, and deliberately **not** the same
registry. That one is a registry of *destinations*: `consumers/crm_delivery.py`
fans a `handoff.lead` out over every active row a tenant has in
`tenant_integration`. A model provider living in that list would be offered
somebody's contact details to deliver.

Migration 0010 adds `tenant_integration.kind` so the two share a table without
sharing a meaning — `crm` for a destination, `llm` for a model — and the delivery
consumer asks for `crm` explicitly.

## Decryption happens here and nowhere else

`provider_for` is the only caller of `secrets.decrypt` for an AI credential, the
same rule `crm.adapter_for` follows. The routers hand back `secret_hint`, the
repository stores ciphertext it cannot read, and a provider receives the key
already open.

## What a tenant with no provider gets

`DeterministicProvider`, not an error and not silence. Open decision 2 is still
open, so this is the ordinary case rather than a degraded one — and the answer it
gives carries a basis saying which of the two answered. See `app/llm/stub.py`.
"""

from __future__ import annotations

from app import secrets
from app.llm.base import Completion, LlmError, LlmProvider
from app.models import TenantIntegration

#: provider name → class. Filled by the decorator at the foot of this file.
registry: dict[str, type[LlmProvider]] = {}

#: The value `tenant_integration.kind` carries for these.
KIND = "llm"


def register(provider: type[LlmProvider]) -> type[LlmProvider]:
    registry[provider.provider] = provider
    return provider


def known(provider: str) -> bool:
    return provider in registry


def parse_secret(provider: str, raw: str) -> None:
    """Check a credential before it is stored. Raises with the reason."""
    cls = registry.get(provider)
    if cls is None:
        raise LlmError(
            f"no AI provider {provider!r} in this build", retryable=False
        )
    cls.parse_secret(raw)


def fallback() -> LlmProvider:
    """What answers when a tenant has configured nothing.

    A function rather than a module-level instance: a provider holds a
    credential, and a shared instance is the shape that later grows one by
    accident.
    """
    return registry["deterministic"].for_tenant(secret="", config={})


def provider_for(integration: TenantIntegration | None) -> LlmProvider:
    """Open a stored credential and hand back a usable provider.

    `None` is the ordinary case — no key configured — and returns the
    deterministic provider rather than raising, because Ask must answer either
    way and the answer says which of the two produced it.
    """
    if integration is None:
        return fallback()

    cls = registry.get(integration.provider)
    if cls is None:
        raise LlmError(
            f"no adapter for AI provider {integration.provider!r} in this build",
            retryable=False,
        )
    secret = secrets.decrypt(
        integration.secret_ct,
        tenant_id=integration.tenant_id,
        provider=integration.provider,
    )
    return cls.for_tenant(secret=secret, config=integration.field_map or {})


__all__ = [
    "KIND",
    "Completion",
    "LlmError",
    "LlmProvider",
    "fallback",
    "known",
    "parse_secret",
    "provider_for",
    "register",
    "registry",
]

# Imported for its side effect, at the foot for the reason `app/crm/__init__.py`
# gives: the module registers itself with the decorator above, and a registry
# that only fills once somebody imports the right module is empty in exactly the
# process that needed it.
from app.llm import stub as _stub  # noqa: E402,F401  isort:skip
