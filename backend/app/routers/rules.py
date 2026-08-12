"""
Rule authoring — the CRUD half of ADR-002.

ADR-002's first reason for rules being data at all is that "an operator writes
rules, not an engineer. The composer UI is plain-English → spec → operator
confirms. That is only possible if a rule is a value the UI can build, show back,
and store." This is where it gets stored.

## Who may write one

Reading is `require_reader`; writing is `require_operator`. A rule is not a
report — saving one arms an action that will post to Slack or change what a
screen in the room is showing, without anybody in the loop afterwards. That is
an operator's decision.

## Tenant isolation

`tenantId` is never read from the body. It comes from the authenticated
principal, as it does everywhere else in this API (`auth/principal.py`: "tenant_id
is derived from a verified credential"). Both tables are under forced row-level
security besides, so a cross-tenant write is refused by the database and not only
by this file.

## Why there is no partial update

PUT replaces the whole document. The composer sends a rule as it should now read,
not a patch — and a rule half-edited by a PATCH is exactly the state that makes
"why did this fire?" unanswerable from the stored document, which is ADR-002's
second reason for the whole design.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.auth.principal import Principal, require_operator, require_reader
from app.db import get_session
from app.schemas import RuleIn, RuleOut

router = APIRouter(prefix="/v1/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
async def list_rules(
    enabled_only: bool = Query(False, alias="enabledOnly"),
    principal: Principal = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
) -> list[RuleOut]:
    """Every rule for the caller's tenant.

    Unfiltered by trigger type on purpose: this is the composer's list, and an
    operator looking for "the rule that keeps pinging me" does not know which
    event type it names.
    """
    rows = await repository.list_rules(
        session, tenant_id=principal.tenant_id, enabled_only=enabled_only
    )
    return [RuleOut.model_validate(row) for row in rows]


@router.get("/{rule_id}", response_model=RuleOut)
async def get_rule(
    rule_id: str,
    principal: Principal = Depends(require_reader),
    session: AsyncSession = Depends(get_session),
) -> RuleOut:
    row = await repository.get_rule(
        session, tenant_id=principal.tenant_id, rule_id=rule_id
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no rule {rule_id!r}"
        )
    return RuleOut.model_validate(row)


@router.put("/{rule_id}", response_model=RuleOut)
async def put_rule(
    rule_id: str,
    body: RuleIn,
    principal: Principal = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> RuleOut:
    """Create or replace one rule.

    The path and the body must agree on `ruleId`. They could be reconciled
    silently — the path is authoritative, so the body's value could just be
    ignored — but a composer that sends a mismatched pair is confused about which
    rule it is editing, and the failure mode of guessing is that an operator
    overwrites the wrong rule and finds out when it fires.
    """
    if body.rule_id != rule_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"ruleId in the body ({body.rule_id!r}) is not the one in the "
            f"path ({rule_id!r})",
        )

    row = await repository.upsert_rule(
        session,
        tenant_id=principal.tenant_id,
        rule_id=rule_id,
        name=body.name,
        trigger_type=body.trigger_type,
        trigger_zone_id=body.trigger_zone_id,
        # by_alias so what lands in JSONB is the camelCase wire document, which
        # is what the evaluator, the browser preview and a human reading the row
        # all expect. Storing snake_case here would make the stored rule a third
        # dialect, which is the split-brain ADR-002 exists to end.
        condition=body.condition.model_dump(by_alias=True, mode="json"),
        action=body.action.model_dump(by_alias=True, mode="json"),
        enabled=body.enabled,
        cooldown_sec=body.cooldown_sec,
    )
    await session.commit()
    return RuleOut.model_validate(row)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: str,
    principal: Principal = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> None:
    removed = await repository.delete_rule(
        session, tenant_id=principal.tenant_id, rule_id=rule_id
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no rule {rule_id!r}"
        )
    await session.commit()
