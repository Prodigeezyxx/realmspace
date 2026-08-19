"""
The interface every AI provider implements.

Deliberately the same shape as `app/crm/base.py`, because it is the same problem:
a credential that belongs to one tenant, one place that opens it, and failures
that raise so the caller's retry policy is the only one in the system. An author
who has read that file has read most of this one.

## Construction is authentication, and there is no unauthenticated provider

`for_tenant` takes the decrypted secret, exactly as `CrmAdapter.for_tenant` does.
A provider object that exists before it has a key is an object somebody can call
`complete()` on, and the failure that produces is a 401 from a vendor rather than
a mistake caught here.

## Failure is raised, never returned

`LlmError` carries `retryable` for the sentence a human reads, not for control
flow — the same contract `AdapterError` has. A rate limit or a 5xx clears by
itself; a rejected key needs somebody to go and reconnect the account. A provider
that caught its own 429 and returned a degraded answer would be worse than one
that failed: the caller would present a guess as an answer.

## What a completion has to carry

`text` and the token counts, because `app/cost.py` has reserved the
`llm_tokens` kind since Phase 3 and named Ask as "the missing spender". A
provider that could not say what a call cost would leave the cost tile reading
zero for the one spender a client actually asks about.

Counts are the vendor's own numbers, never an estimate from character length. A
made-up token count is a made-up figure on a unit-economics tile, which is the
thing `roi-framework.md` rules out everywhere else.

## Providers do not see tenants, sessions or people

`complete` takes an assembled prompt and nothing else. Everything about who is
being asked about — and every decision about what may be read — happens before
this interface, in `app/llm/catalogue.py`. A provider is a text-in, text-out
component with no access to a database, which is what makes "the model cannot
read another tenant's activation" a structural fact rather than a promise.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar


class LlmError(RuntimeError):
    """A provider could not answer.

    `retryable` is the difference between "the vendor rate-limited us, it will
    clear" and "this key is not valid any more" — the same distinction
    `AdapterError` draws, and for the same reader.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class Completion:
    """What a provider gives back."""

    text: str
    #: The vendor's own counts. Zero from a provider that makes no call and does
    #: not pretend otherwise — see `app/llm/stub.py`.
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LlmProvider(ABC):
    """One tenant's connection to one model."""

    #: The key this provider is registered under and the value stored in
    #: `tenant_integration.provider`, alongside `kind = 'llm'`.
    provider: ClassVar[str]

    def __init__(self, *, secret: str, config: dict[str, Any] | None = None) -> None:
        self._secret = secret
        #: Reuses `tenant_integration.field_map`, which is a JSONB blob a tenant
        #: controls. For a CRM it maps fields; here it carries the model name and
        #: any per-tenant limit. Same column because it is the same thing — the
        #: part of a credential that is configuration rather than secret.
        self._config = config or {}

    @classmethod
    def for_tenant(
        cls, *, secret: str, config: dict[str, Any] | None = None
    ) -> LlmProvider:
        """Authentication, as a constructor. See the module docstring."""
        return cls(secret=secret, config=config or {})

    @classmethod
    def parse_secret(cls, raw: str) -> dict[str, Any]:
        """Check a credential is the shape this provider needs, before storing.

        Mirrors `CrmAdapter.parse_secret` and runs in the same place — at
        `PUT /v1/integrations/{provider}`, before anything is encrypted — so a
        malformed key is a 422 naming what is wrong rather than a failure on the
        first question somebody asks.
        """
        raw = raw.strip()
        if not raw:
            raise LlmError("the credential is empty", retryable=False)
        return {"token": raw}

    @abstractmethod
    async def complete(self, prompt: str, *, max_tokens: int = 512) -> Completion:
        """Answer a prompt. Raises on failure; never returns a status."""

    @abstractmethod
    async def healthcheck(self) -> tuple[bool, str]:
        """`(ok, detail)` — whether this credential can still reach the model."""

    @classmethod
    def capabilities(cls) -> dict[str, Any]:
        """What this provider can do, for a UI that should not offer what it
        cannot — and, more importantly here, for a surface that must say which
        kind of thing answered.

        `reasons` is the one that matters: True for a real model, False for the
        deterministic stand-in. Every answer this system shows carries its basis,
        because an operator who cannot tell a model's answer from a keyword match
        cannot judge either.
        """
        return {"reasons": False, "streams": False}
