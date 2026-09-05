"use client";

/**
 * The tablet at a touchpoint: what it is, and the taps it owes the backend.
 *
 * ## Why the queue is the whole of this file
 *
 * The one thing a stand's wifi is reliably bad at is the moment the stand is
 * busy, which is the moment the taps matter. `perception/bus_client.py` was
 * built around the same fact — it buffers to a local JSONL and replays
 * oldest-first — and the property that makes its reconnect idempotent is that
 * **the event id is assigned when the event happens, not when it is sent**.
 *
 * So a tap mints its `touchId` under the finger, goes into `localStorage`, and
 * is retried until the backend takes it. A retry of a tap the backend already
 * has is a no-op there: the `event_id` derives from this id, and the log's
 * UNIQUE constraint turns the second POST into the same row.
 *
 * ## What it deliberately does not do
 *
 * No optimistic count shown to the room, no "thanks, that's 41 today". Whoever
 * is holding this tablet is a member of the public, and the activation's
 * numbers are the client's. `routers/touch.py` returns nothing else for the
 * same reason.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { busUrl } from "@/lib/bus";

/** One unsent tap. Small on purpose: this is all a tablet can attest to. */
export interface QueuedTap {
  touchId: string;
  at: string;
}

export interface Tablet {
  surfaceId: string;
  label: string;
  kind: string;
}

export type TabletStatus = "loading" | "ready" | "invalid" | "unconfigured";

/** Per token, so two tablets in one browser profile cannot eat each other's. */
const queueKey = (token: string) => `realmspace.touch.queue.${token}`;

function readQueue(token: string): QueuedTap[] {
  try {
    const raw = window.localStorage.getItem(queueKey(token));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as QueuedTap[]) : [];
  } catch {
    // A private window, cleared site data, or a browser refusing storage. The
    // button still works; it just cannot survive being closed mid-outage.
    return [];
  }
}

function writeQueue(token: string, queue: QueuedTap[]): void {
  try {
    window.localStorage.setItem(queueKey(token), JSON.stringify(queue));
  } catch {
    /* see readQueue */
  }
}

export function useTablet(token: string | null) {
  // `invalid` and `unconfigured` are **derived, not stored**: both are decided
  // by the arguments this hook already has, and storing them would mean setting
  // state during an effect to say something that was true before it ran. The
  // repo made the same change to `AuthProvider.loading` when CI started gating
  // on lint, and for the same reason rather than to satisfy the rule.
  const [fetched, setFetched] = useState<TabletStatus>("loading");
  const [tablet, setTablet] = useState<Tablet | null>(null);
  const [pending, setPending] = useState(0);
  // The flush is not re-entrant. Two overlapping runs would send the head of
  // the queue twice — harmless at the backend, which dedupes, and confusing
  // here, where the count would drop twice for one tap.
  const flushing = useRef(false);

  // No token is a bad link; no backend is a tablet with nowhere to send a tap.
  // Both are said plainly rather than shown as a working button — a tablet that
  // looks like it is recording and is not is the worst version of this page.
  const status: TabletStatus = !token
    ? "invalid"
    : !busUrl()
      ? "unconfigured"
      : fetched;

  useEffect(() => {
    if (!token || !busUrl()) return;

    let live = true;
    void (async () => {
      try {
        const res = await fetch(`${busUrl()}/v1/touch/${encodeURIComponent(token)}`);
        if (!live) return;
        if (!res.ok) {
          setFetched("invalid");
          return;
        }
        setTablet((await res.json()) as Tablet);
        setFetched("ready");
      } catch {
        // Offline at open. The token may be perfectly good, so this is not
        // `invalid` — but there is no label to put on the button yet.
        if (live) setFetched("loading");
      }
    })();
    return () => {
      live = false;
    };
  }, [token]);

  const flush = useCallback(async () => {
    if (!token || flushing.current || !busUrl()) return;
    flushing.current = true;
    try {
      let queue = readQueue(token);
      setPending(queue.length);
      while (queue.length) {
        const [head, ...rest] = queue;
        const res = await fetch(`${busUrl()}/v1/touch/${encodeURIComponent(token)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ touchId: head.touchId, at: head.at }),
        });
        if (res.status === 404) {
          // Revoked, expired, or never real. Retrying forever would fill the
          // queue with taps that can never land; the remedy is a new link.
          setFetched("invalid");
          break;
        }
        if (!res.ok) break; // still offline, or the backend is down. Keep it.
        queue = rest;
        writeQueue(token, queue);
        setPending(queue.length);
      }
    } catch {
      /* offline. The queue is on disk and the next tap or reconnect retries. */
    } finally {
      flushing.current = false;
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    // Same narrow suppression, and the same reason, as `useCalibration`: the
    // queue lives in `localStorage`, which does not exist while this page is
    // being prerendered, so the first read of it cannot happen during render.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void flush();
    const onOnline = () => void flush();
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [token, flush]);

  const tap = useCallback(() => {
    if (!token) return;
    const queued: QueuedTap = {
      // Minted here, under the finger. See the module docstring.
      touchId:
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      at: new Date().toISOString(),
    };
    const queue = [...readQueue(token), queued];
    writeQueue(token, queue);
    setPending(queue.length);
    void flush();
  }, [token, flush]);

  return { status, tablet, pending, tap };
}
