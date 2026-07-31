"""
Create local development credentials.

    python -m app.auth.seed
    python -m app.auth.seed --tenant t_other --email me@example.com

## Why a command and not a migration

A migration runs everywhere it is applied. Putting known credentials in one
plants them in every environment that ever runs `alembic upgrade` — including a
deployment nobody meant to be a demo. Seeded credentials should require somebody
to have typed something.

## Why the API key is printed once

Only its SHA-256 hash is stored, so this is genuinely the only time the key
exists in readable form. Re-running the command mints a new one rather than
recovering the old, because recovering it would mean it had been stored, which
would defeat hashing it.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets

from sqlalchemy import select

from app.auth.models import ApiKey, AuthUser
from app.auth.tokens import generate_api_key, issue_token
from app.db import SessionLocal

DEFAULT_TENANT = "t_floats"
DEFAULT_EMAIL = "admin@floats.demo"


async def seed(tenant_id: str, email: str, role: str) -> None:
    async with SessionLocal() as session:
        existing = (
            await session.execute(select(AuthUser).where(AuthUser.email == email))
        ).scalar_one_or_none()

        if existing is None:
            user = AuthUser(
                user_id=f"u_{secrets.token_hex(6)}",
                email=email,
                display_name="Local Dev",
                tenant_id=tenant_id,
                role=role,
            )
            session.add(user)
            await session.flush()
        else:
            user = existing

        key_id, plaintext, key_hash = generate_api_key()
        session.add(
            ApiKey(
                key_id=key_id,
                key_hash=key_hash,
                tenant_id=tenant_id,
                label="local dev producer",
            )
        )
        await session.commit()

        token = issue_token(
            subject=user.user_id, tenant_id=user.tenant_id, role=user.role
        )

    print(f"\n  tenant   {tenant_id}")
    print(f"  user     {user.email}  ({user.role})")
    print("\n  Device key — for producers (perception, RFID, kiosk). Shown once:")
    print(f"    X-API-Key: {plaintext}")
    print("\n  User token — for reads and the WebSocket. Expires; re-issue with")
    print(f"    curl -X POST localhost:8000/v1/auth/token -H 'content-type: application/json' \\")
    print(f"         -d '{{\"email\":\"{user.email}\"}}'")
    print(f"    Authorization: Bearer {token}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="seed local dev credentials")
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--role", default="admin", help="multi-tenant.md §3")
    args = parser.parse_args()
    asyncio.run(seed(args.tenant, args.email.lower(), args.role))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
