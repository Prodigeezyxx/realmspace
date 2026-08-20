"""
The producer's offline buffer, as executable claims.

`event-bus-spec.md` §1 promises the edge survives going offline. This is where
that promise is either true or isn't.

No camera and no network: `_send` is substituted so a failure is a decision
rather than a race. That is why `BusClient` lives in its own module —
`realmspace.py` imports cv2 at module scope, so this file could not import it.

The load-bearing test is `test_replay_keeps_the_original_event_id`. Everything
else here could pass while the system silently double-counted every event
recorded during a network drop.
"""

from __future__ import annotations

import json

import pytest

from perception.bus_client import BusClient


@pytest.fixture
def bus(tmp_path):
    """A client whose buffer lives in a tmp dir, so tests can't collide."""
    return BusClient(
        base_url="http://bus.invalid",
        tenant_id="t_test",
        session_id="s_test",
        api_key="rsk_test",
        buffer_file=str(tmp_path / "buffer.jsonl"),
    )


def capture_sends(bus: BusClient, *, succeed: bool = True) -> list[dict]:
    """Replace the transport. Returns the list bodies get appended to."""
    seen: list[dict] = []

    def fake_send(body: dict) -> bool:
        seen.append(body)
        return succeed

    bus._send = fake_send  # type: ignore[method-assign]
    return seen


# ── the happy path ────────────────────────────────────────────────────────────


def test_reachable_bus_posts_and_buffers_nothing(bus: BusClient) -> None:
    sent = capture_sends(bus)
    bus.post("perception.detection", {"anon_id": "P-1"})

    assert len(sent) == 1
    assert sent[0]["type"] == "perception.detection"
    assert sent[0]["tenantId"] == "t_test"
    assert bus.pending() == 0


def test_disabled_client_is_a_no_op(tmp_path) -> None:
    """No --bus-url means the script behaves exactly as it did before, for
    anyone who just wants the stdout stream."""
    quiet = BusClient(base_url="", tenant_id="t", session_id="s",
                      buffer_file=str(tmp_path / "b.jsonl"))
    sent = capture_sends(quiet)
    quiet.post("perception.detection", {})
    assert sent == [] and quiet.pending() == 0


# ── going offline ─────────────────────────────────────────────────────────────


def test_unreachable_bus_buffers_instead_of_raising(bus: BusClient) -> None:
    """A camera loop must not die because a server did."""
    capture_sends(bus, succeed=False)
    bus.post("perception.detection", {"anon_id": "P-1"})
    assert bus.pending() == 1


def test_replay_keeps_the_original_event_id(bus: BusClient) -> None:
    """THE test.

    The id is assigned when the event happens, not when it is finally sent. So
    a buffered event arrives carrying the id it was born with, and the log's
    UNIQUE(event_id) recognises it as something it already has.

    Mint the id at send time instead and every reconnect writes duplicates —
    silently, showing up weeks later as inflated dwell in a report.
    """
    capture_sends(bus, succeed=False)
    bus.post("perception.detection", {"anon_id": "P-1"})

    buffered = json.loads(open(bus.buffer_file).read().strip())
    original_id = buffered["eventId"]

    sent = capture_sends(bus, succeed=True)
    assert bus.replay() == 1
    assert sent[0]["eventId"] == original_id, "replay minted a new id"
    assert bus.pending() == 0


def test_replay_is_oldest_first(bus: BusClient) -> None:
    capture_sends(bus, succeed=False)
    for i in range(3):
        bus.post("perception.detection", {"i": i})

    sent = capture_sends(bus, succeed=True)
    bus.replay()
    assert [b["payload"]["i"] for b in sent] == [0, 1, 2]


def test_a_failure_partway_leaves_the_rest_in_order(bus: BusClient) -> None:
    """A connection that comes back unreliably must not scramble or lose the
    queue. Stop at the failure; keep the remainder, in order."""
    capture_sends(bus, succeed=False)
    for i in range(4):
        bus.post("perception.detection", {"i": i})
    assert bus.pending() == 4

    calls = {"n": 0}

    def flaky(body: dict) -> bool:
        calls["n"] += 1
        return calls["n"] <= 2  # first two land, then the network drops again

    bus._send = flaky  # type: ignore[method-assign]
    assert bus.replay() == 2
    assert bus.pending() == 2

    remaining = [json.loads(line) for line in open(bus.buffer_file) if line.strip()]
    assert [b["payload"]["i"] for b in remaining] == [2, 3]


def test_a_corrupt_line_is_skipped_not_fatal(bus: BusClient) -> None:
    """A half-written line from a hard power cut must not brick the buffer.

    One truncated event out of a session is an acceptable loss; a buffer that
    can never drain again is not.
    """
    capture_sends(bus, succeed=False)
    bus.post("perception.detection", {"i": 0})
    with open(bus.buffer_file, "a") as handle:
        handle.write('{"eventId": "truncated...\n')
    bus.post("perception.detection", {"i": 1})

    sent = capture_sends(bus, succeed=True)
    bus.replay()
    assert [b["payload"]["i"] for b in sent] == [0, 1]
    assert bus.pending() == 0


# ── credentials ───────────────────────────────────────────────────────────────


def test_api_key_is_sent_when_configured(bus: BusClient) -> None:
    assert bus._headers()["X-API-Key"] == "rsk_test"


def test_no_key_means_no_header(tmp_path) -> None:
    """Against a backend that doesn't require auth, sending an empty header
    would be worse than sending none."""
    anon = BusClient(base_url="http://x", tenant_id="t", session_id="s",
                     buffer_file=str(tmp_path / "b.jsonl"))
    assert "X-API-Key" not in anon._headers()


# ── against the real API ──────────────────────────────────────────────────────


async def test_replayed_events_land_once_in_the_real_log(
    client, tmp_path, device_key: str
) -> None:
    """Producer idempotency and log idempotency have to agree.

    The producer resends buffered events; the log must recognise them. This
    drives the actual endpoint rather than a stand-in, because the guarantee
    spans both and testing either half alone would prove nothing.
    """
    from tests.conftest import TENANT

    bus = BusClient(base_url="http://test", tenant_id=TENANT, session_id="s_replay",
                    api_key=device_key, buffer_file=str(tmp_path / "b.jsonl"))
    capture_sends(bus, succeed=False)
    for i in range(3):
        bus.post("perception.detection",
                 {"anon_id": "P-1", "bbox": [1, 1, 2, 2], "i": i,
                  "frame_width": 10, "frame_height": 10})

    buffered = [json.loads(line) for line in open(bus.buffer_file) if line.strip()]

    # post each twice, exactly as a flaky reconnect would
    for body in buffered + buffered:
        r = await client.post("/events", json=body)
        assert r.status_code in (200, 201), r.text

    rows = (await client.get("/events")).json()
    ids = [r["eventId"] for r in rows]
    assert len(ids) == 3, f"expected 3 rows, got {len(ids)}"
    assert len(set(ids)) == 3


# ── the read half (Phase 6, the privacy mask) ─────────────────────────────────


def test_get_json_returns_the_status_alongside_the_body(bus: BusClient) -> None:
    """Status is returned rather than raised, because the caller acts on it.

    `perception/mask.py` treats 404 (no such camera) as a misconfiguration to
    refuse on and a transport failure as a reason to fall back to its cache.
    Collapsing the two into one exception would let a typo in `--camera-id` run
    the booth unmasked.
    """
    status, body = bus.get_json("/v1/sessions/s_test/cameras/cam-1/mask")

    # http://bus.invalid resolves nowhere, so this is the transport-failure case.
    assert status == 0
    assert body is None


def test_a_read_is_never_buffered(bus: BusClient) -> None:
    """The buffer exists so an event that already happened survives an outage.

    There is no equivalent for a read: an answer we could not fetch is not an
    answer we can replay later, and writing one to the queue would put a GET
    into a file the replay loop POSTs.
    """
    bus.get_json("/v1/sessions/s_test/cameras/cam-1/mask")
    assert bus.pending() == 0
