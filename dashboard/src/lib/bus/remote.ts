/**
 * realmspace — the bridge between this app's durable log and the backend bus.
 *
 * Set `NEXT_PUBLIC_BUS_URL` and the dashboard stops being a self-contained
 * prototype: events this browser produces are posted to the real log, and events
 * the *backend* produces — which is nearly all of them, since perception and the
 * tracker run there — arrive over a WebSocket and are mirrored into the local
 * log that every screen already reads.
 *
 * Unset, nothing here runs and the app behaves exactly as it did before. That is
 * deliberate: the demo has to work on a laptop with no backend.
 *
 * ## Three things worth understanding
 *
 * **The local log is the buffer.** There is no second outbox. `emit()` appends
 * locally first and always succeeds; posting is a separate step that walks
 * forward from a stored cursor. So a browser that emits while the backend is
 * down loses nothing — the events are already durable in localStorage, and
 * `flushOutbound()` sends them in order when it comes back. This is the same
 * property `perception/bus_client.py` gets from its JSONL buffer, and it works
 * for the same reason: the `eventId` is assigned when the event happens, not
 * when it is sent, so a resend after a timeout dedupes instead of duplicating.
 *
 * **`since_seq` is a real cursor.** The socket is told the highest remote seq
 * this browser has already seen, and gets only what came after — no gap, no
 * duplicates, which matters because conference wifi drops constantly. That seq
 * is remote-only and is *not* the local log's seq; see `wire.ts`.
 *
 * **The tenant comes from the token, not from this file.** `tenant/context.ts`
 * always said that when auth was wired, only it would change and callers would
 * keep calling `getTenantId()`. This is that moment: the backend's token carries
 * a verified tenant, and we adopt it rather than asserting one.
 */

import { append, read, getCursor, setCursor } from "./log";
import { eventFromWire, eventToWire, type WireEvent } from "./wire";
import { setTenantId } from "@/lib/tenant/context";
import type { RealmEvent } from "@/lib/contracts";

const OUTBOUND_CURSOR = "remote-bus";
const REMOTE_SEQ_PREFIX = "rs:remoteseq:";

export type BusStatus = "off" | "connecting" | "live" | "retrying" | "error";

interface RemoteState {
  status: BusStatus;
  detail: string | null;
  /** Highest remote seq mirrored in this session, for display. */
  lastSeq: number;
  pendingOutbound: number;
}

let state: RemoteState = {
  status: "off",
  detail: null,
  lastSeq: 0,
  pendingOutbound: 0,
};

const listeners = new Set<() => void>();

function publish(next: Partial<RemoteState>) {
  state = { ...state, ...next };
  listeners.forEach((l) => {
    try {
      l();
    } catch {
      /* a status subscriber must never break the bus */
    }
  });
}

export function subscribeBusStatus(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getBusStatus(): RemoteState {
  return state;
}

function hasWindow() {
  return typeof window !== "undefined";
}

/** The configured backend, without a trailing slash. Empty string = disabled. */
export function busUrl(): string {
  return (process.env.NEXT_PUBLIC_BUS_URL ?? "").replace(/\/+$/, "");
}

export function isRemoteBusEnabled(): boolean {
  return busUrl() !== "";
}

/**
 * The identity to present to the bus when Firebase has not signed anyone in.
 *
 * Local development runs without Firebase configured, and `python -m
 * app.auth.seed` creates `admin@floats.demo` by default — so this makes the two
 * halves line up out of the box. It is not a security boundary and is not
 * pretending to be one: the backend's token endpoint currently trusts whatever
 * email it is handed, which its own README flags as still open. Once a signed-in
 * user exists, their email is used instead.
 */
export function busEmail(): string {
  return process.env.NEXT_PUBLIC_BUS_EMAIL || "admin@floats.demo";
}

/* ── credentials ─────────────────────────────────────────────────────────── */

let token: string | null = null;
let tokenExpiresAt = 0;
let inFlightToken: Promise<string | null> | null = null;

/**
 * Exchange an email for a backend token.
 *
 * The backend's `POST /v1/auth/token` currently trusts the email it is given
 * rather than verifying a Firebase login — its own README says so. So this is
 * not yet authentication in any meaningful sense; it is how a local dev run gets
 * a signed token whose *tenant claim* is real, which is what the rest of the
 * request path depends on. Verifying the Firebase credential is the backend's
 * job and is still open.
 */
async function fetchToken(email: string): Promise<string | null> {
  const res = await fetch(`${busUrl()}/v1/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    publish({ status: "error", detail: `auth failed (${res.status})` });
    return null;
  }
  const body = (await res.json()) as {
    accessToken: string;
    expiresIn: number;
    tenantId: string;
  };

  token = body.accessToken;
  // Refresh a minute early rather than on the failure. A token that expires
  // mid-session otherwise surfaces as a 401 on a write the user already thinks
  // succeeded.
  tokenExpiresAt = Date.now() + (body.expiresIn - 60) * 1000;

  // The verified tenant wins over whatever this browser had stored.
  setTenantId(body.tenantId);
  return token;
}

/** The current token, refreshed if it is missing or about to expire. */
export async function ensureToken(email: string): Promise<string | null> {
  if (token && Date.now() < tokenExpiresAt) return token;
  // Collapse concurrent callers onto one exchange: the socket and the first
  // post typically ask at the same instant, and two tokens would mean the
  // second silently replacing the first mid-connection.
  if (!inFlightToken) {
    inFlightToken = fetchToken(email).finally(() => {
      inFlightToken = null;
    });
  }
  return inFlightToken;
}

/** Drop the cached token. Exported for sign-out and for tests. */
export function clearToken() {
  token = null;
  tokenExpiresAt = 0;
}

/* ── outbound: local log → POST /events ──────────────────────────────────── */

async function postEvent(event: RealmEvent): Promise<boolean> {
  if (!token) return false;
  const res = await fetch(`${busUrl()}/events`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(eventToWire(event)),
  });
  // 200 and 201 are both success: 201 created the row, 200 means this eventId
  // was already in the log. A producer retrying after a timeout gets 200 and
  // must treat it as done, not as a reason to retry again.
  return res.status === 200 || res.status === 201;
}

/**
 * Send everything this browser has produced since the last successful post.
 *
 * Stops at the first failure and leaves the cursor there, so ordering is
 * preserved across an outage — the backend receives them oldest-first on
 * reconnect, exactly as `perception/bus_client.py` replays its buffer. Advancing
 * past a failure would silently drop the event, which is the failure mode this
 * whole design exists to avoid.
 */
export async function flushOutbound(
  tenantId: string,
  sessionId: string
): Promise<number> {
  if (!isRemoteBusEnabled() || !token) return 0;

  const from = getCursor(OUTBOUND_CURSOR, tenantId, sessionId);
  const pending = read(tenantId, sessionId, { afterSeq: from });
  let sent = 0;

  for (const event of pending) {
    let ok = false;
    try {
      ok = await postEvent(event);
    } catch {
      ok = false; // offline; try again on the next flush
    }
    if (!ok) break;
    setCursor(OUTBOUND_CURSOR, tenantId, sessionId, event.seq);
    sent++;
  }

  publish({ pendingOutbound: pending.length - sent });
  return sent;
}

/* ── inbound: WebSocket → local log ──────────────────────────────────────── */

function remoteSeqKey(tenantId: string, sessionId: string) {
  return `${REMOTE_SEQ_PREFIX}${tenantId}::${sessionId}`;
}

export function getRemoteSeq(tenantId: string, sessionId: string): number {
  if (!hasWindow()) return 0;
  return Number(window.localStorage.getItem(remoteSeqKey(tenantId, sessionId))) || 0;
}

function setRemoteSeq(tenantId: string, sessionId: string, seq: number) {
  if (!hasWindow()) return;
  window.localStorage.setItem(remoteSeqKey(tenantId, sessionId), String(seq));
}

/**
 * Mirror one wire event into the local log and advance the remote cursor.
 *
 * Exported because it is the whole inbound contract in one function, and a test
 * that drives it directly is worth more than one that fakes a WebSocket.
 */
export function mirror(wire: WireEvent): void {
  append(eventFromWire(wire));
  // Only ever forwards. A replay frame after a reconnect can legitimately
  // contain events older than the cursor if the server capped the window;
  // moving the cursor backwards there would re-request them forever.
  if (wire.seq > getRemoteSeq(wire.tenantId, wire.sessionId)) {
    setRemoteSeq(wire.tenantId, wire.sessionId, wire.seq);
    publish({ lastSeq: wire.seq });
  }
}

interface ConnectOptions {
  tenantId: string;
  sessionId: string;
  email: string;
  /** Overridable so tests don't need a real socket. */
  socketFactory?: (url: string) => WebSocket;
  /** Backoff ceiling, ms. */
  maxRetryMs?: number;
}

/**
 * Open the live feed and keep it open. Returns a disconnect function.
 *
 * Reconnection backs off exponentially to a ceiling. It never gives up: a booth
 * runs for days on venue wifi, and a feed that stops retrying after N attempts
 * is a feed that is dead for the rest of the activation with nothing on screen
 * to say so.
 */
export function connectLiveFeed(opts: ConnectOptions): () => void {
  if (!isRemoteBusEnabled()) return () => {};

  const { tenantId, sessionId, email } = opts;
  const factory = opts.socketFactory ?? ((url: string) => new WebSocket(url));
  const maxRetryMs = opts.maxRetryMs ?? 30_000;

  let socket: WebSocket | null = null;
  let retryMs = 1_000;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;

  const scheduleRetry = () => {
    if (closed) return;
    publish({ status: "retrying", detail: `reconnecting in ${retryMs / 1000}s` });
    timer = setTimeout(open, retryMs);
    retryMs = Math.min(retryMs * 2, maxRetryMs);
  };

  const open = async () => {
    if (closed) return;
    publish({ status: "connecting", detail: null });

    const jwt = await ensureToken(email);
    if (!jwt) return scheduleRetry();

    const since = getRemoteSeq(tenantId, sessionId);
    const base = busUrl().replace(/^http/, "ws");
    const url =
      `${base}/v1/ws/${encodeURIComponent(tenantId)}/${encodeURIComponent(sessionId)}` +
      `?since_seq=${since}&token=${encodeURIComponent(jwt)}`;

    try {
      socket = factory(url);
    } catch {
      return scheduleRetry();
    }

    socket.onopen = () => {
      // Reset the backoff only once a connection actually succeeds. Resetting
      // on the attempt would turn a server that accepts and immediately drops
      // into a tight reconnect loop.
      retryMs = 1_000;
      publish({ status: "live", detail: null });
      void flushOutbound(tenantId, sessionId);
    };

    socket.onmessage = (ev: MessageEvent) => {
      let frame: { type: string; replay?: WireEvent[]; event?: WireEvent };
      try {
        frame = JSON.parse(String(ev.data));
      } catch {
        return; // an unparseable frame is not a reason to drop the connection
      }
      if (frame.type === "hello") {
        for (const e of frame.replay ?? []) mirror(e);
      } else if (frame.type === "event" && frame.event) {
        mirror(frame.event);
      }
    };

    socket.onerror = () => {
      publish({ status: "error", detail: "socket error" });
    };

    socket.onclose = () => {
      socket = null;
      scheduleRetry();
    };
  };

  void open();

  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    socket?.close();
    socket = null;
    publish({ status: "off", detail: null });
  };
}
