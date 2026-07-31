"""
Posting events to the realmspace bus, with an offline buffer.

Ported from the implementation on the `floats-agent` branch, with authentication
and a couple of hardening fixes. Kept as a separate module from
`realmspace.py` for two reasons:

  - `realmspace.py` imports cv2 at module scope, so nothing without OpenCV can
    import it. The buffer logic is the part of this with real correctness risk
    and it should be testable without a camera.
  - The RFID bridge and the kiosk SDK (Week 1 tasks 1.11 and 1.12) need exactly
    this behaviour. They should import it rather than write it again.

## Why the buffer exists

`event-bus-spec.md` §1: *"Conference WiFi is unreliable → the bus must run
locally on the edge device and survive going offline."* An activation runs for
days on a network nobody controls. A dropped connection must cost nothing.

## The two things that make replay safe

**`event_id` is assigned before buffering, not before sending.** A buffered
event keeps the id it was born with, so when the connection returns and it is
finally posted, the log's `UNIQUE(event_id)` recognises it. Minting the id at
send time would turn every reconnect into a pile of duplicate rows — and the
duplicates would be silent, showing up later as inflated dwell in a report.

**Replay stops at the first failure.** The remaining lines stay in the file, in
order. Draining past a failure would reorder events; dropping them would lose
the session.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

#: Requests are short. A camera loop cannot block for seconds on a dead network
#: — a slow timeout here shows up as dropped frames.
TIMEOUT_SECONDS = 2.0

DEFAULT_BUFFER = ".bus-buffer.jsonl"


class BusClient:
    """HTTP bridge to the realmspace edge API, tolerant of the network vanishing.

    Disabled (a no-op) when constructed with no base_url, so `realmspace.py`
    runs exactly as before for anyone who just wants stdout.
    """

    def __init__(
        self,
        base_url: str,
        tenant_id: str,
        session_id: str,
        api_key: str = "",
        buffer_file: str = DEFAULT_BUFFER,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.api_key = api_key
        self.enabled = bool(base_url)
        self.buffer_file = buffer_file

    # ── transport ─────────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            # Producers authenticate with a device key, not a user token — a
            # camera cannot do an interactive login. The key can only write.
            headers["X-API-Key"] = self.api_key
        return headers

    def _send(self, body: dict[str, Any]) -> bool:
        """POST one event. True on 2xx, False on anything else.

        `/v1/events` rather than `/events`: both backends accept it, so the same
        script works against either without knowing which it is talking to.

        Note what is *not* retried here — a 401 or a 403 is a configuration
        mistake, not a network blip, and buffering against it would silently
        fill the disk with events that will never be accepted. They are reported
        loudly and dropped.
        """
        request = urllib.request.Request(
            f"{self.base_url}/v1/events",
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                response.read()
            return True
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                _warn("bus_auth_error", status=exc.code,
                      hint="check --api-key / REALMSPACE_API_KEY and --tenant-id")
                return True  # "handled" — do not buffer what will never be accepted
            if 400 <= exc.code < 500:
                _warn("bus_rejected", status=exc.code, type=body.get("type"))
                return True  # malformed event; buffering it would jam the queue
            return False  # 5xx — the server's problem, worth retrying
        except (urllib.error.URLError, TimeoutError, OSError):
            return False  # unreachable — this is what the buffer is for

    # ── buffer ────────────────────────────────────────────────────────────────

    def _buffer(self, body: dict[str, Any]) -> None:
        try:
            with open(self.buffer_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(body) + "\n")
        except OSError as exc:
            _warn("bus_buffer_error", error=str(exc))

    def _drop_buffer(self) -> None:
        try:
            os.remove(self.buffer_file)
        except OSError:
            pass

    def replay(self) -> int:
        """Flush buffered events oldest-first. Returns how many were sent.

        Stops at the first failure and rewrites the file with what is left, so
        ordering survives a connection that comes back unreliably.
        """
        if not os.path.exists(self.buffer_file):
            return 0
        try:
            with open(self.buffer_file, encoding="utf-8") as handle:
                lines = [line for line in handle.read().splitlines() if line.strip()]
        except OSError:
            return 0
        if not lines:
            self._drop_buffer()
            return 0

        sent = 0
        remaining: list[str] = []
        for index, line in enumerate(lines):
            try:
                body = json.loads(line)
            except json.JSONDecodeError:
                # A half-written line from a hard power cut. Skipping it is
                # right: one truncated event must not wedge the whole buffer
                # forever, and it is one event out of a session.
                _warn("bus_buffer_corrupt_line")
                continue
            if self._send(body):
                sent += 1
            else:
                remaining = lines[index:]
                break

        if remaining:
            try:
                with open(self.buffer_file, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(remaining) + "\n")
            except OSError:
                pass
            if sent:
                _warn("bus_replay", sent=sent, pending=len(remaining))
        else:
            self._drop_buffer()
            if sent:
                _warn("bus_replay_done", sent=sent)
        return sent

    # ── the one method callers use ────────────────────────────────────────────

    def post(
        self, event_type: str, payload: dict[str, Any], event_id: str | None = None
    ) -> None:
        """Send an event, or buffer it if the bus is unreachable. Never raises.

        A camera loop must not die because a server did — every failure here
        ends up on stderr and in the buffer, not as an exception.
        """
        if not self.enabled:
            return

        body = {
            "tenantId": self.tenant_id,
            "sessionId": self.session_id,
            "type": event_type,
            "payload": payload,
            # Assigned HERE, before any send or buffer attempt. This is what
            # makes replay idempotent rather than duplicating.
            "eventId": event_id or str(uuid.uuid4()),
            "occurredAt": int(time.time() * 1000),
        }

        # Catch up before adding to the queue, so buffered events stay ahead of
        # new ones and the log's order matches what actually happened.
        self.replay()

        if not self._send(body):
            self._buffer(body)
            _warn("bus_buffered", event_type=event_type)

    def pending(self) -> int:
        """How many events are waiting. For status output and tests."""
        try:
            with open(self.buffer_file, encoding="utf-8") as handle:
                return sum(1 for line in handle if line.strip())
        except OSError:
            return 0


def _warn(kind: str, **fields: Any) -> None:
    """Structured to stderr, so stdout stays a clean event stream for piping."""
    print(json.dumps({"type": kind, **fields}), file=sys.stderr, flush=True)
