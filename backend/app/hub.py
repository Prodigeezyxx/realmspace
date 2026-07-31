"""
In-process WebSocket fan-out.

The last hop of the pipeline: `event_log` → broadcast consumer → here → browser.

Rooms are keyed `(tenant_id, session_id)`, which is the isolation boundary. A
socket only ever joins one room and only ever receives that room's events —
there is no code path that sends an event to a room it does not belong to, which
matters because the socket is currently unauthenticated (see routers/live.py) and
room membership is the only thing separating tenants on this hop.

Deliberately in-process and stateless. One edge box runs one process
(`multi-tenant.md` §2), so a shared broker would be infrastructure with no job.
If the backend is ever run multi-process, this is the piece that needs replacing
with Redis pub/sub — the callers would not change.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

log = logging.getLogger(__name__)


class Hub:
    def __init__(self) -> None:
        self._rooms: dict[tuple[str, str], set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(tenant_id: str, session_id: str) -> tuple[str, str]:
        return (tenant_id, session_id)

    async def join(self, tenant_id: str, session_id: str, ws: WebSocket) -> None:
        """Add an already-accepted socket to a room.

        Accepting the handshake is the caller's job — the endpoint needs to send
        a `hello` frame before any broadcast can reach the client, and doing the
        accept here would make that ordering ambiguous.
        """
        async with self._lock:
            self._rooms.setdefault(self._key(tenant_id, session_id), set()).add(ws)

    async def leave(self, tenant_id: str, session_id: str, ws: WebSocket) -> None:
        async with self._lock:
            key = self._key(tenant_id, session_id)
            room = self._rooms.get(key)
            if not room:
                return
            room.discard(ws)
            if not room:
                del self._rooms[key]  # don't leak empty rooms across a long session

    async def broadcast(self, tenant_id: str, session_id: str, message: dict) -> int:
        """Send to every socket in one room. Returns how many got it.

        A send that fails means the client is gone — a closed tab, a laptop lid,
        a dropped conference wifi. Those are collected and removed rather than
        raised, because one dead browser must not stop the broadcast loop and
        take the live feed down for everyone else in the booth.
        """
        async with self._lock:
            sockets = list(self._rooms.get(self._key(tenant_id, session_id), ()))

        if not sockets:
            return 0

        delivered = 0
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(message)
                delivered += 1
            except Exception:  # noqa: BLE001 — a dead client is expected, not exceptional
                dead.append(ws)

        for ws in dead:
            await self.leave(tenant_id, session_id, ws)
        if dead:
            log.info(
                "dropped %s dead socket(s) from %s/%s", len(dead), tenant_id, session_id
            )
        return delivered

    async def room_size(self, tenant_id: str, session_id: str) -> int:
        async with self._lock:
            return len(self._rooms.get(self._key(tenant_id, session_id), ()))

    async def total_sockets(self) -> int:
        async with self._lock:
            return sum(len(room) for room in self._rooms.values())


#: One hub per process, shared by the endpoint and the broadcast consumer.
hub = Hub()
