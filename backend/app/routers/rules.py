"""
Rule authoring — the CRUD half of ADR-002.

ADR-002's first reason for rules being data at all is that "an operator writes
rules, not an engineer. The composer UI is plain-English → spec → operator
confirms. That is only possible if a rule is a value the UI can build, show back,
and store." This is where it gets stored.

## Who may write one

Reading is `require_reader`; writing is `require_rule_author`. A rule is not a
report — saving one arms an action that will post to Slack or change what a
screen in the room is showing, without anybody in the loop afterwards.

That was an operator's decision until the roles were made to mean something.
`multi-tenant.md` §3 puts "build agents" with Analyst/Marketer, and an agent *is*
a rule (ADR-002: the browser's `AgentDefinition`s compile to the same document).
Reading stays open to every role, because seeing what is armed is part of
watching the floor — it is arming it that belongs to the person whose job the
agents are.

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

import datetime as dt
import hashlib
import json
import re
import secrets as pysecrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from neo4j import AsyncSession as GraphSession
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm, plans, repository
from app.auth.principal import Principal, require_reader, require_rule_author
from app.cost import meter
from app.db import get_session
from app.graph import repository as graph_repo
from app.graph.driver import get_graph_session
from app.llm import budget, prompts, rule_shapes
from app.llm.base import LlmError, unfenced
from app.schemas import ComposedRule, RuleIn, RuleOut, _to_camel

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
    principal: Principal = Depends(require_rule_author),
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

    # `gtm.md` calls a rule a "custom agent" and caps them per tier. The count
    # only applies to a **new** rule: PUT is create-or-replace, and editing the
    # second of two rules on a two-agent plan must not be refused for being the
    # third. Disabled rules count — a disabled rule is one the operator can arm
    # without asking anybody.
    held, is_replacement = await repository.rule_slots(
        session, tenant_id=principal.tenant_id, rule_id=rule_id
    )
    if not is_replacement:
        plans.enforce(
            plan=await plans.plan_for(session, principal.tenant_id),
            limit_name="max_agents",
            requested=held + 1,
            noun="agents",
            noun_singular="agent",
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
        # `exclude_none` so an unset narrowing is absent rather than an
        # explicit null: the evaluator reads with `.get()` either way, and a
        # document read back in the composer should say what the operator
        # wrote and not carry a field for every filter they did not use.
        condition=body.condition.model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
        action=body.action.model_dump(by_alias=True, mode="json"),
        enabled=body.enabled,
        cooldown_sec=body.cooldown_sec,
    )
    await session.commit()
    return RuleOut.model_validate(row)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: str,
    principal: Principal = Depends(require_rule_author),
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


# ── the composer ─────────────────────────────────────────────────────────────
#
# ADR-002's first argument, finally built: "an operator writes rules, not an
# engineer. The composer UI is plain-English → spec → operator confirms."
# `PRD.md` §6.5 names the model half. This is the translation step; the confirming
# is the browser's, and the arming is the PUT above.


#: Types the taxonomy registers and nothing produces yet (`event-bus-spec.md` §3).
#: A rule may name one — pre-registration exists so it can — and an operator
#: should be told it will not fire until a producer lands, because a rule that is
#: armed, correct and silent is the failure this codebase keeps finding.
UNPRODUCED = ("rfid.read", "spatial.tagged", "intent.scored")

#: Offered to the model as `triggerType`s. The taxonomy in the order a floor
#: happens, so a model reading it top to bottom meets the spatial events first.
COMPOSABLE_EVENTS = (
    "spatial.zone_enter",
    "spatial.zone_exit",
    "spatial.dwell",
    "spatial.passby",
    "spatial.occupancy",
    "spatial.group",
    "spatial.gaze",
    "surface.touched",
    "surface.interaction",
    "insight.generated",
    "drift.detected",
    "session.started",
    "session.ended",
    *UNPRODUCED,
)


class ComposeIn(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    instruction: str = Field(min_length=1, max_length=500)
    #: Which activation's zones the rule may name. Rules are stored per tenant
    #: rather than per session, so this narrows the *vocabulary* and not where
    #: the rule will run.
    session_id: str = Field(min_length=1)


class ComposeOut(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    instruction: str
    #: The document, unsaved, with an id minted here. None when nothing was
    #: built, and then `reason` says why.
    rule: dict[str, Any] | None = None
    reason: str = ""
    #: Things true of the rule that an operator should read before arming it —
    #: a trigger nothing produces, an action that had to be narrowed. Never a
    #: silent correction: `roi-framework.md`'s rule about absences, applied to a
    #: document instead of a figure.
    warnings: list[str] = Field(default_factory=list)
    #: `deterministic` or the provider's name. Never absent, for the reason
    #: `/v1/ask` gives: an operator who cannot tell a model's draft from a
    #: keyword match cannot judge either.
    basis: str
    #: What this can build, sent on a refusal so it is not a dead end.
    can_build: list[dict[str, Any]] = Field(default_factory=list)
    took_ms: int = 0


@router.post("/compose", response_model=ComposeOut, summary="Plain English → a rule document")
async def compose_rule(
    body: ComposeIn,
    principal: Principal = Depends(require_rule_author),
    session: AsyncSession = Depends(get_session),
    graph: GraphSession = Depends(get_graph_session),
) -> ComposeOut:
    """Turn an instruction into a rule document. **Nothing is armed here.**

    The rule `consumers/sdr.py` states for a follow-up draft: this composes and
    does not send. What comes back is a document the operator reads as a
    sentence, edits, and arms with the PUT above — which is the same request the
    form makes when they write one by hand, so there is one write path and one
    place a plan limit or a role is enforced.

    Behind `require_rule_author` rather than `require_reader`: handing a document
    to somebody who cannot save it is a dead end, and this spends tokens.
    """
    started = time.monotonic()
    asked_at = dt.datetime.now(dt.timezone.utc)

    zones = await graph_repo.zones_for_session(
        graph,
        tenant_id=principal.tenant_id,
        session_id=body.session_id,
        include_undrawn=True,
    )

    integration = await repository.get_integration_of_kind(
        session, tenant_id=principal.tenant_id, kind=llm.KIND, active_only=True
    )
    provider = llm.provider_for(integration)
    basis = provider.provider

    may_call, _ = await budget.within_budget(
        session, tenant_id=principal.tenant_id, spender="compose"
    )
    if not may_call or not provider.capabilities().get("reasons"):
        # The floor, and the two ways of reaching it are one branch on purpose:
        # a spent budget and no provider at all give an operator the same thing,
        # and `budget.py` argues that a spent budget is a provider outage rather
        # than an error.
        provider = llm.fallback()
        basis = provider.provider

    if basis == llm.fallback().provider:
        body_dict, reason = rule_shapes.deterministic_compose(body.instruction, zones)
        tokens = 0
    else:
        body_dict, reason, tokens = await _compose(provider, body.instruction, zones)

    if tokens:
        await meter(
            session,
            tenant_id=principal.tenant_id,
            session_id=body.session_id,
            kind="llm_tokens",
            amount=float(tokens),
            unit="tokens",
            occurred_at=asked_at,
            # The instruction and the moment, for `routers/ask.py`'s reason: two
            # composes of the same wording in one activation are two spends, and
            # a cause without the timestamp made the second vanish into the
            # first's derived id.
            cause=(
                "compose",
                hashlib.sha256(
                    body.instruction.strip().lower().encode()
                ).hexdigest()[:16],
                asked_at.isoformat(),
            ),
            detail={"provider": basis, "spender": "compose"},
        )
        await session.commit()

    if body_dict is None:
        return ComposeOut(
            instruction=body.instruction,
            reason=reason or "I could not turn that into a rule.",
            basis=basis,
            can_build=rule_shapes.menu(),
            took_ms=_since(started),
        )

    # Before the document is read for the response: `_warnings` takes the
    # narrowing marker off it, so the rule an operator sees is a rule and not a
    # rule plus a note to itself.
    warnings = _warnings(body_dict, zones)

    return ComposeOut(
        instruction=body.instruction,
        rule={
            **body_dict,
            # Minted here, never by the model and never by the phrase: a
            # composed rule that reused an existing id would replace it on the
            # first press of Arm.
            "ruleId": _mint_id(body_dict.get("name") or body.instruction),
            "enabled": True,
        },
        warnings=warnings,
        basis=basis,
        took_ms=_since(started),
    )


async def _compose(
    provider, instruction: str, zones: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str, int]:
    """Ask the provider for a document. Never trusts what comes back.

    Three failures are told apart because an operator's next move differs:
    the provider was unreachable, it replied with something that is not the
    shape asked for, or it built a rule this system will not accept. The third
    is reported with the validator's own words — `useRules.save` already shows a
    422 verbatim for the same reason: the backend's validator is the authority,
    and paraphrasing it here would be another place that knows the rule language.
    """
    try:
        completion = await provider.complete(
            prompts.compose_prompt(
                instruction=instruction,
                zones=zones,
                event_types=list(COMPOSABLE_EVENTS),
            ),
            max_tokens=1200,
        )
    except LlmError as exc:
        return None, f"the AI provider could not answer: {exc}", 0

    tokens = completion.total_tokens
    try:
        replied = json.loads(unfenced(completion.text))
    except ValueError:
        return None, "the AI provider replied with something that is not JSON", tokens

    if not isinstance(replied, dict):
        return None, "the AI provider did not reply with an object", tokens
    if not replied.get("rule"):
        return None, str(replied.get("reason") or "").strip(), tokens

    draft, outward = _narrow_action(replied["rule"])

    try:
        composed = ComposedRule.model_validate(draft)
    except ValidationError as exc:
        return (
            None,
            "the AI provider built something this system will not accept: "
            + "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()
            ),
            tokens,
        )

    known = {str(z["id"]) for z in zones}
    for named in (composed.trigger_zone_id, composed.condition.zone_id,
                  getattr(composed.action, "zone_id", None)):
        if named is not None and named not in known:
            # Not corrected to the nearest zone. A rule armed on a zone the
            # operator did not mean is armed, plausible and silent, which is
            # worse than being asked again.
            return (
                None,
                f"it named a zone this activation does not have ({named!r}). "
                f"The zones here are: {', '.join(sorted(known)) or 'none yet'}",
                tokens,
            )

    document = composed.model_dump(by_alias=True, exclude_none=True)
    if outward:
        document["_outward"] = outward
    return document, "", tokens


#: What an operator would call the actions a composed rule may not carry.
OUTWARD = {
    "slack": "a Slack post",
    "webhook": "a webhook",
    "screen_swap": "a screen swap",
}


def _narrow_action(rule: Any) -> tuple[Any, str | None]:
    """Swap an outward-facing action for `log`, and say which was asked for.

    Refusing the whole document was the first version and it threw away the
    useful half: the *condition* is what the operator described and the hard part
    to get right, while the destination is one field they were always going to
    fill in themselves. So the rule survives with an action that stays inside the
    room, and `_warnings` says what to change.

    The channel or URL the model proposed is **dropped rather than reported**.
    Naming it back would put an address nobody chose in front of an operator as
    though it were a real one, which is the invention this narrowing exists to
    prevent.
    """
    if not isinstance(rule, dict):
        return rule, None
    action = rule.get("action")
    if not isinstance(action, dict):
        return rule, None
    kind = action.get("type")
    if kind not in OUTWARD:
        return rule, None
    return (
        {
            **rule,
            "action": {
                "type": "log",
                "message": str(action.get("message") or rule.get("name") or "matched"),
            },
        },
        str(kind),
    )


def _warnings(rule: dict[str, Any], zones: list[dict[str, Any]]) -> list[str]:
    """What is true of this document that an operator should read first."""
    said: list[str] = []
    outward = rule.pop("_outward", None)
    if outward:
        said.append(
            f"You asked for {OUTWARD[outward]}. A composed rule can only prompt "
            "the floor or write to the log, because a destination outside the "
            "room is an address somebody has to choose — the condition below is "
            "the part that was written for you. Change the action to send it "
            "on."
        )
    trigger = str(rule.get("triggerType") or "")
    if trigger in UNPRODUCED:
        said.append(
            f"Nothing produces {trigger} yet, so this rule will not fire until "
            "something does. The type is registered ahead of its producer on "
            "purpose (event-bus-spec.md §3)."
        )
    if not zones:
        said.append(
            "This activation has no zones configured, so the rule applies to "
            "every zone."
        )
    return said


def _mint_id(name: str) -> str:
    """`r_<slug>_<6 hex>` — readable, and unique per compose.

    `presets.py` prefixes a preset `preset_` "so a saved preset is
    distinguishable from a rule an operator wrote"; this is the other half of
    that convention. The random tail rather than the slug alone: composing twice
    from the same wording is two candidates, and colliding ids would make the
    second silently replace the first on the way in.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")[:24] or "rule"
    return f"r_{slug}_{pysecrets.token_hex(3)}"


def _since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
