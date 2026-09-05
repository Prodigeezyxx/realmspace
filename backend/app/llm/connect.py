"""
Store a tenant's AI credential. An operator action on the box, not an endpoint.

`PUT /v1/integrations/{provider}` validates against `crm.registry` and
`repository.upsert_integration` defaults `kind="crm"`, so until today there was
no path in the system that stored an LLM key at all. This is the smallest one
that does not weaken anything.

## Why not the router, yet

`/v1/integrations` is the *destinations* surface. `consumers/crm_delivery.py`
fans a `handoff.lead` out over every active row of `kind = 'crm'`, and that
filter is the whole reason migration 0010 added the column — a model provider in
the destinations list would be offered somebody's contact details to deliver.
Teaching the router two registries means teaching the surface, the `/test`
branch and the delivery filter about the difference at once, and none of that is
needed to answer the question a real key can now answer.

It also matches what the key actually is. This is one shared team key, not a
client's own credential; the self-serve endpoint belongs with the second, and
`app/plans.py` gives the precedent for the first — the CLI it grew for exactly
the same reason, that the endpoint which should own this does not exist yet.

## Usage

    python -m app.llm.connect openrouter <tenant-id> --key sk-or-v1-…
    python -m app.llm.connect openrouter <tenant-id>          # reads stdin
    python -m app.llm.connect openrouter <tenant-id> --model google/gemini-3.7-flash
    python -m app.llm.connect check <tenant-id>

The key is read from `--key` or stdin and never printed back: what comes out is
`secrets.hint`, the same four characters the routers return. Prefer stdin —
an argument is in the shell history of a key three people share.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app import llm, repository, secrets
from app.db import SessionLocal, scope_to_tenant
from app.llm.base import LlmError


async def _connect(provider: str, tenant_id: str, key: str, model: str | None) -> None:
    if not llm.known(provider):
        raise SystemExit(
            f"no AI provider {provider!r} in this build. "
            f"Providers are {sorted(llm.registry)}."
        )
    try:
        llm.parse_secret(provider, key)
    except (LlmError, ValueError) as exc:
        raise SystemExit(f"that credential was refused: {exc}") from None

    field_map = {"model": model} if model else {}
    if model:
        # Refuse a model this provider will not accept, here rather than on the
        # first question somebody asks. Construction is authentication, so the
        # provider is built with the key it will actually use.
        try:
            _ = llm.registry[provider].for_tenant(secret=key, config=field_map).model  # type: ignore[attr-defined]
        except LlmError as exc:
            raise SystemExit(str(exc)) from None

    async with SessionLocal() as session:
        # RLS fails closed: without the scope this INSERT is refused rather than
        # written somewhere wrong.
        await scope_to_tenant(session, tenant_id)
        row = await repository.upsert_integration(
            session,
            tenant_id=tenant_id,
            provider=provider,
            secret_ct=secrets.encrypt(key, tenant_id=tenant_id, provider=provider),
            secret_hint=secrets.hint(key),
            field_map=field_map,
            kind=llm.KIND,
        )
        await session.commit()
        print(f"{tenant_id}: {provider} stored as {row.secret_hint} (kind={row.kind})")
        print(f"  model  {field_map.get('model') or 'the provider default'}")
        print("  Ask, the SDR drafts and the insight consumer now answer with the model.")


async def _check(tenant_id: str) -> None:
    """What this tenant's key can actually reach. Costs no tokens."""
    async with SessionLocal() as session:
        await scope_to_tenant(session, tenant_id)
        integration = await repository.get_integration_of_kind(
            session, tenant_id=tenant_id, kind=llm.KIND, active_only=True
        )
    provider = llm.provider_for(integration)
    ok, detail = await provider.healthcheck()
    print(f"{tenant_id}: {provider.provider} — {'ok' if ok else 'FAILING'}")
    print(f"  {detail}")
    if not ok:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = parser.add_subparsers(dest="command", required=True)

    store = sub.add_parser("openrouter", help="store an OpenRouter key for a tenant")
    store.add_argument("tenant_id")
    store.add_argument("--key", help="the key; omit to read it from stdin")
    store.add_argument(
        "--model",
        help="override the provider default — see app/llm/openrouter.py ALLOWED",
    )

    checker = sub.add_parser("check", help="ask the stored key whether it still works")
    checker.add_argument("tenant_id")

    args = parser.parse_args()

    if args.command == "check":
        asyncio.run(_check(args.tenant_id))
        return

    key = args.key or sys.stdin.read()
    asyncio.run(_connect(args.command, args.tenant_id, key.strip(), args.model))


if __name__ == "__main__":
    main()
