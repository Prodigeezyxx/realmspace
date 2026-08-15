"""
Where a tenant hands us a credential to their CRM.

`multi-tenant.md` §2 has required this since Phase 1 and nothing implemented it,
which is the block `roadmap.md` records against the CRM adapters: "blocked on a
per-tenant credential store, which nothing in the repo has yet".

## The secret goes in and never comes back out

There is no endpoint that returns a stored credential, and that is not an
oversight to be fixed by a later "reveal" feature. The plaintext exists in this
process for the length of one PUT and afterwards only as ciphertext
(`app/secrets.py`) that one function opens (`app/crm/adapter_for`). An admin who
has lost their HubSpot token gets it from HubSpot, not from us.

`secretHint` — the last four characters — is what a UI needs to answer the only
question a reveal would be for: *is this the key I pasted?*

## Admin, not operator

`require_admin`, new for this router. multi-tenant.md §RBAC puts integrations
with the Admin role, and the reason holds up: an operator arms a rule that posts
to a room, an admin hands over a credential that writes into the client's system
of record and outlives the activation.

## Why a provider must be one this build knows

Unlike the event taxonomy, which is open by design, `PUT` refuses a provider
with no adapter. A credential stored for something nothing dispatches is a
client's live token sitting in our database achieving nothing, under an admin
who believes their CRM is connected.

## `test` writes what it found

The healthcheck result is stored on the row, not only returned, so a token that
expires between activations is visible on the screen before a lead strands on
it — which is the failure this is really for. Nobody presses test on a Tuesday.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import crm, repository, secrets
from app.auth.principal import Principal, require_admin
from app.crm.base import AdapterError
from app.db import get_session
from app.models import TenantIntegration
from app.schemas import _to_camel

router = APIRouter(prefix="/v1/integrations", tags=["integrations"])


class IntegrationIn(BaseModel):
    """A credential, and how this tenant's fields map onto the CRM's."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    #: The token, API key or refresh token. Write-only, in the strict sense: no
    #: response model in this file contains it.
    secret: str = Field(min_length=1, max_length=4096)

    #: `integrations.md` §3 — "field mapping is per-tenant config, not code".
    #: Handoff field → the CRM's own property name. Empty means an adapter sends
    #: only what it can map by itself, which for HubSpot is the standard contact
    #: fields and none of the spatial ones.
    field_map: dict[str, str] = Field(default_factory=dict)


class IntegrationOut(BaseModel):
    """Everything about an integration except the one thing it stores."""

    model_config = ConfigDict(
        alias_generator=_to_camel, populate_by_name=True, from_attributes=True
    )

    provider: str
    status: str
    secret_hint: str
    field_map: dict[str, str]
    capabilities: dict[str, object]
    last_check_at: dt.datetime | None
    last_check_ok: bool | None
    last_check_detail: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    revoked_at: dt.datetime | None


class CheckOut(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    provider: str
    ok: bool
    detail: str


def _out(row: TenantIntegration) -> IntegrationOut:
    """Shape a row for the wire.

    `capabilities` comes from the adapter class rather than the row: it is a
    property of the CRM and of this build, not of the credential, and storing it
    would let a UI keep offering a feature an adapter dropped.
    """
    adapter = crm.registry.get(row.provider)
    return IntegrationOut(
        provider=row.provider,
        status=row.status,
        secret_hint=row.secret_hint,
        field_map=row.field_map or {},
        capabilities=adapter.capabilities() if adapter else {},
        last_check_at=row.last_check_at,
        last_check_ok=row.last_check_ok,
        last_check_detail=row.last_check_detail,
        created_at=row.created_at,
        updated_at=row.updated_at,
        revoked_at=row.revoked_at,
    )


def _require_known(provider: str) -> None:
    if not crm.known(provider):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"no adapter for provider {provider!r} in this build — known "
                f"providers are {sorted(crm.registry) or 'none yet'}"
            ),
        )


@router.get("", response_model=list[IntegrationOut], summary="What this tenant has connected")
async def list_integrations(
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[IntegrationOut]:
    """Every integration, revoked ones included.

    Revoked rows stay in the list on purpose: "we disconnected HubSpot in March"
    and "we never connected it" are different answers, and only one of them
    explains why a lead from March reached a CRM.
    """
    rows = await repository.list_integrations(session, tenant_id=principal.tenant_id)
    return [_out(row) for row in rows]


@router.get("/{provider}", response_model=IntegrationOut)
async def get_integration(
    provider: str,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> IntegrationOut:
    row = await repository.get_integration(
        session, tenant_id=principal.tenant_id, provider=provider
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no {provider!r} integration for this tenant",
        )
    return _out(row)


@router.put("/{provider}", response_model=IntegrationOut, summary="Store a credential")
async def put_integration(
    provider: str,
    body: IntegrationIn,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> IntegrationOut:
    """Store or replace this tenant's credential for one provider.

    PUT rather than POST, and replace rather than patch, for the reason
    `routers/rules.py` gives about rule documents: an admin reconnecting a CRM
    is stating what the connection should now be, and a credential half-updated
    by a PATCH — new token, old field map — is a state nobody chose.

    An unset `credential_encryption_key` fails here with 503 rather than storing
    the token. See `app/secrets.py`: the alternative is a client's CRM
    credential in a database column in the clear, discovered later by somebody
    else.
    """
    _require_known(provider)

    try:
        ciphertext = secrets.encrypt(
            body.secret, tenant_id=principal.tenant_id, provider=provider
        )
    except secrets.EncryptionUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    row = await repository.upsert_integration(
        session,
        tenant_id=principal.tenant_id,
        provider=provider,
        secret_ct=ciphertext,
        secret_hint=secrets.hint(body.secret),
        field_map=body.field_map,
    )
    await session.commit()
    return _out(row)


@router.delete("/{provider}", response_model=IntegrationOut, summary="Stop using a credential")
async def revoke_integration(
    provider: str,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> IntegrationOut:
    """Revoke, keeping the row.

    DELETE the verb, revocation the effect — the resource is gone in the sense
    that matters (nothing will deliver through it again) while the record of it
    having existed stays, because a `crm_link` naming a provider whose row had
    vanished could not say who we were when we pushed that contact.

    The ciphertext survives revocation deliberately: a withdrawal arriving the
    day after an admin disconnects HubSpot still has to authenticate to HubSpot
    to retract the contact.
    """
    row = await repository.get_integration(
        session, tenant_id=principal.tenant_id, provider=provider
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no {provider!r} integration for this tenant",
        )
    await repository.revoke_integration(
        session, tenant_id=principal.tenant_id, provider=provider
    )
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.post("/{provider}/test", response_model=CheckOut, summary="Is this credential still good?")
async def test_integration(
    provider: str,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> CheckOut:
    """Ask the CRM, record the answer, return it.

    A failure is a 200 carrying `ok: false`, not a 5xx. The request succeeded —
    we asked and got an answer — and the answer is the payload. Returning an
    error status would make a UI unable to distinguish "we could not reach our
    own backend" from "your token expired", which are different things for the
    admin to do next.
    """
    row = await repository.get_integration(
        session, tenant_id=principal.tenant_id, provider=provider
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no {provider!r} integration for this tenant",
        )

    try:
        adapter = crm.adapter_for(row)
        ok, detail = await adapter.healthcheck()
    except (AdapterError, secrets.EncryptionUnavailable) as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"

    await repository.record_integration_check(
        session,
        tenant_id=principal.tenant_id,
        provider=provider,
        ok=ok,
        detail=detail,
    )
    await session.commit()
    return CheckOut(provider=provider, ok=ok, detail=detail)
