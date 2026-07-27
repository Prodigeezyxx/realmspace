"""In-process WebSocket hub for live event fan-out."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from app.models import RealmEvent


class Hub:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    @staticmethod
    def key(tenant_id: str, session_id: str) -> str:
        return f"{tenant_id}::{session_id}"

    async def connect(self, tenant_id: str, session_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._rooms[self.key(tenant_id, session_id)].add(ws)

    async def disconnect(self, tenant_id: str, session_id: str, ws: WebSocket) -> None:
        async with self._lock:
            room = self._rooms.get(self.key(tenant_id, session_id))
            if room:
                room.discard(ws)
                if not room:
                    del self._rooms[self.key(tenant_id, session_id)]

    async def broadcast(self, event: RealmEvent) -> None:
        room_key = self.key(event.tenantId, event.sessionId)
        async with self._lock:
            sockets = list(self._rooms.get(room_key, set()))
        dead: list[WebSocket] = []
        payload: dict[str, Any] = {"type": "event", "event": event.model_dump()}
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(event.tenantId, event.sessionId, ws)


hub = Hub()
