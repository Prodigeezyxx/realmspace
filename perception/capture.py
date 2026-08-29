"""
Reading frames, and telling a dropped one apart from the end of the footage.

## The failure this exists for

`realmspace.py` used to open a source and then loop on:

    ok, frame = cap.read()
    if not ok:
        break

which conflates two events that are not the same thing. For a **video file**,
`not ok` is end-of-file and stopping is exactly right. For a **live camera** it
is a dropped frame, and stopping ends the activation.

Found on 2026-08-28, on the first read rather than a later one: the camera
opened, `cap.read()` returned nothing once, and the process printed
`{"type": "session_end", "frames": 0}` and exited about a second after it
started. Nothing in the output said the camera had never delivered anything. On
a booth that is a camera which woke a moment too slowly and a run that ended
before it began, and the operator's only clue is a report with no events in it.

## Its own module, with no cv2

`realmspace.py` imports cv2 at the top, so nothing in it can be reached by the
backend venv — the same reason `bus_client.py` and `heading.py` are separate,
and the reasoning `pytest.ini` records: the backend venv is the one that always
gets run. Everything here takes any object with a `.read()`, so
`tests/test_capture.py` drives it with a fake that fails on demand rather than
with a webcam nobody can rely on being plugged in.
"""

from __future__ import annotations

import time
from typing import Any, Callable

#: How long to keep trying before deciding a camera is not going to deliver.
#: Long enough for a device that needs a moment to wake — which is the common
#: case and was being treated as a fatal error — and short enough that a booth
#: with a genuinely dead camera finds out at startup rather than after an hour
#: of recording nothing.
FIRST_FRAME_TIMEOUT = 3.0

#: The same patience mid-run, for a camera that stutters. A run of failures this
#: long is a camera that has actually gone away; anything shorter is a dropped
#: frame, which is a thing cameras do and not a reason to end an activation.
DROPOUT_TIMEOUT = 3.0

#: How long to wait between attempts. Short enough not to add meaningful latency
#: to a recovery, long enough not to spin a core while a device wakes up.
RETRY_INTERVAL = 0.05


def open_first_frame(
    cap: Any,
    *,
    timeout: float = FIRST_FRAME_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> tuple[bool, Any, int]:
    """Wait for the source to produce its first frame.

    Returns `(ok, frame, attempts)`. `ok` false means the source opened and then
    never gave us anything, which is a different diagnosis from never opening at
    all and gets its own exit code in `realmspace.py`.

    The attempt count is returned rather than logged here, so the caller decides
    what to say — a camera that took four tries to wake is worth a line in the
    startup output and is not worth an error.
    """
    deadline = now() + timeout
    attempts = 0

    while True:
        attempts += 1
        ok, frame = cap.read()
        if ok:
            return True, frame, attempts
        if now() >= deadline:
            return False, None, attempts
        sleep(RETRY_INTERVAL)


def read_frame(
    cap: Any,
    *,
    live: bool,
    timeout: float = DROPOUT_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> tuple[bool, Any, int]:
    """One frame. Returns `(ok, frame, dropped)`.

    `live` is the whole distinction. `realmspace.py` already knows which it has
    — a numeric `--source` is a camera index and anything else is a path — and
    that fact was simply not reaching this decision.

    **On a file**, the first failure is end-of-file and ends the loop, exactly as
    before. Retrying there would spin forever at the end of every clip.

    **On a camera**, failures are tolerated for `timeout` before giving up, and
    the number dropped is returned so the caller can say the run limped rather
    than leaving it to be inferred from a frame count nobody has a baseline for.
    """
    ok, frame = cap.read()
    if ok or not live:
        return ok, frame, 0

    deadline = now() + timeout
    dropped = 1

    while now() < deadline:
        sleep(RETRY_INTERVAL)
        ok, frame = cap.read()
        if ok:
            return True, frame, dropped
        dropped += 1

    return False, None, dropped
