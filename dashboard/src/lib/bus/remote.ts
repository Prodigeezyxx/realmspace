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

import { getFirebaseAuth } from "@/lib/firebase/client";
import { append, read, getCursor, setCursor, headSeq } from "./log";
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
/**
 * The signed-in user's Firebase ID token, or null if nobody is signed in.
 *
 * `getIdToken()` returns a cached token and refreshes it only when it is close
 * to expiring, so this is not a network round trip per call.
 */
async function currentIdToken(): Promise<string | null> {
  try {
    const auth = getFirebaseAuth();
    const user = auth?.currentUser;
    return user ? await user.getIdToken() : null;
  } catch {
    // Firebase not configured, or the refresh failed. Either way the backend
    // decides what to do about a request with no ID token — it is the only
    // place that knows whether one is required here.
    return null;
  }
}

export function busEmail(): string {
  return process.env.NEXT_PUBLIC_BUS_EMAIL || "admin@floats.demo";
}

/* ── credentials ─────────────────────────────────────────────────────────── */

let token: string | null = null;
let tokenExpiresAt = 0;
let inFlightToken: Promise<string | null> | null = null;

/**
 * Exchange a verified Firebase sign-in for a backend token.
 *
 * The backend used to trust whatever email it was given, which meant anyone who
 * knew an address could get that user's token. It now verifies a Firebase ID
 * token against Google's published keys, so this sends the real credential the
 * user already signed in with — the identity existed all along and was simply
 * never carried across.
 *
 * The email fallback survives for **local development only**, and the backend
 * refuses it in every other environment (`app/routers/auth.py`). A dev stack
 * with no Firebase project configured would otherwise be unrunnable.
 */
async function fetchToken(email: string): Promise<string | null> {
  const idToken = await currentIdToken();
  const res = await fetch(`${busUrl()}/v1/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // Both are sent; the backend picks. Which one is *required* is a property of
    // the deployment, not of this browser, and asking here would mean shipping a
    // second copy of that rule to disagree with the first.
    body: JSON.stringify(idToken ? { idToken, email } : { email }),
  });
  if (!res.ok) {
    // 401 here is specifically "verified, but no account" — the backend's
    // `unknown user`. Worth its own message: `auth failed (401)` in the
    // connection pill told somebody who had signed in perfectly correctly
    // nothing at all about what to do, and until Phase 6 there was nothing they
    // *could* do. See `signUpOrganisation` below.
    publish({
      status: "error",
      detail:
        res.status === 401
          ? "signed in, but this address has no organisation yet"
          : `auth failed (${res.status})`,
    });
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

/**
 * Create an organisation for the signed-in identity, and adopt the token.
 *
 * `multi-tenant.md` §4 step 1 — the only one of the six onboarding steps that
 * had no implementation. Until 2026-08-21 a person could complete the "Create
 * your account" tab on the login form, get a real Firebase identity, and then
 * receive `401 unknown user` from this backend forever: a Firebase account is
 * only half a login here, because the address still has to map to a tenant and
 * a role.
 *
 * Deliberately **not** folded into `fetchToken`. Auto-creating an organisation
 * whenever an unknown address signed in would mean a colleague who was supposed
 * to be invited to an existing org silently getting an empty one of their own
 * instead — and the first they would know is that the floor they were told to
 * watch is not there. Naming the organisation is the confirmation that this is
 * a new company and not a missing invitation.
 */
export async function signUpOrganisation(
  email: string,
  orgName: string
): Promise<{ tenantId: string } | { error: string }> {
  const idToken = await currentIdToken();
  const res = await fetch(`${busUrl()}/v1/auth/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // Same shape as the token exchange above, and for the same reason: which
    // credential is required is a property of the deployment, not of this
    // browser.
    body: JSON.stringify(
      idToken ? { idToken, email, orgName } : { email, orgName }
    ),
  });

  const body = await res.json().catch(() => null);
  if (!res.ok) {
    return {
      error:
        typeof body?.detail === "string"
          ? body.detail
          : `Could not create the organisation (${res.status}).`,
    };
  }

  // Adopt it exactly as `fetchToken` does — signup returns a token so this is
  // one round trip rather than two, and a second exchange here would be another
  // chance to fail on a path where the account now exists.
  token = body.accessToken;
  tokenExpiresAt = Date.now() + (body.expiresIn - 60) * 1000;
  setTenantId(body.tenantId);
  publish({ status: "live", detail: null });

  return { tenantId: body.tenantId };
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

/**
 * Declare everything currently in a partition local-only: advance the outbound
 * cursor to the head without sending anything.
 *
 * There is exactly one caller and one reason. The demo session's events are
 * synthetic (`lib/mock/seed-demo.ts`), and they live in the same durable log as
 * real ones — which is the point, because it means the report computes them with
 * the same code. But `flushOutbound` walks that log from a cursor, so the next
 * genuine `emit()` would post 140 invented visitors into the real event log,
 * where they would be indistinguishable from a real activation and permanent
 * (the log is append-only). Marking them keeps the demo entirely on this
 * machine.
 */
export function markLocalOnly(tenantId: string, sessionId: string): void {
  setCursor(OUTBOUND_CURSOR, tenantId, sessionId, headSeq(tenantId, sessionId));
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

/**
 * Pull a session's whole history into the local log, oldest first.
 *
 * The live socket only carries what happens while a browser is watching. Open
 * `/report` on a session this machine never had open — the normal case, a week
 * later, on someone else's laptop — and the local log is empty. Without this the
 * report would show an empty state for a session with thousands of events in it.
 *
 * Reuses `mirror()`, so it is idempotent against whatever the socket already
 * delivered and the two can run in any order. Returns how many events were read.
 *
 * Pages on `seq` rather than an offset: `seq` has permanent gaps (a deduped
 * insert burns a value), so paging by count would skip events.
 */
export async function backfillSession(
  tenantId: string,
  sessionId: string,
  email: string
): Promise<number> {
  if (!isRemoteBusEnabled()) return 0;
  const jwt = await ensureToken(email);
  if (!jwt) return 0;

  const PAGE = 500;
  let since = 0;
  let total = 0;

  // Bounded so a pathological log cannot spin forever in a render path.
  for (let page = 0; page < 200; page++) {
    const res = await fetch(
      `${busUrl()}/events?since_seq=${since}&limit=${PAGE}` +
        `&session_id=${encodeURIComponent(sessionId)}`,
      { headers: { Authorization: `Bearer ${jwt}` } }
    );
    if (!res.ok) break;

    const batch = (await res.json()) as WireEvent[];
    if (!batch.length) break;

    for (const wire of batch) {
      // The server filters by session, but the log is partitioned by
      // (tenant, session) and a mismatch here would write into the wrong
      // partition — worth one comparison rather than trusting the query.
      if (wire.tenantId === tenantId && wire.sessionId === sessionId) mirror(wire);
    }
    total += batch.length;
    since = batch[batch.length - 1].seq;
    if (batch.length < PAGE) break;
  }

  return total;
}

/**
 * Read a session's events into memory **without mirroring them into the log**.
 *
 * The opposite choice from `backfillSession` above, and both are right for what
 * they do. The local log is a 5,000-event ring buffer that silently drops its
 * oldest entries; anything reading a whole activation — the twin's replay, the
 * report's benchmark — would lose the earliest dwells to trimming and nothing
 * would say so. So these events are held for as long as the caller holds them
 * and never enter the buffer.
 *
 * `types` narrows the read server-side. The benchmark computes a scorecard over
 * several past activations, and a scorecard reads seven event types, none of
 * them `perception.detection` — which is almost every row in a day's log. Left
 * unfiltered it would page through hundreds of thousands of detections to
 * compute nothing.
 */
export async function fetchSessionEvents(
  tenantId: string,
  sessionId: string,
  options: { types?: string[]; maxPages?: number } = {}
): Promise<RealmEvent[]> {
  if (!isRemoteBusEnabled()) return [];
  const token = await ensureToken(busEmail());
  if (!token) return [];

  const PAGE = 500;
  const maxPages = options.maxPages ?? 400;
  const typeQuery = (options.types ?? [])
    .map((t) => `&type=${encodeURIComponent(t)}`)
    .join("");

  const out: RealmEvent[] = [];
  let since = 0;

  for (let page = 0; page < maxPages; page++) {
    const res = await fetch(
      `${busUrl()}/events?since_seq=${since}&limit=${PAGE}` +
        `&session_id=${encodeURIComponent(sessionId)}${typeQuery}`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    if (!res.ok) break;

    const batch = (await res.json()) as WireEvent[];
    if (!batch.length) break;

    for (const wire of batch) {
      if (wire.tenantId !== tenantId || wire.sessionId !== sessionId) continue;
      // Translated but not mirrored: the payload has to be in this app's shape
      // to be read, but it must not enter the ring buffer.
      out.push({
        ...eventFromWire(wire),
        seq: wire.seq,
        eventId: wire.eventId,
        occurredAt: wire.occurredAt,
        recordedAt: wire.recordedAt,
      } as RealmEvent);
    }

    // Page on `seq`, not on an offset: seq has permanent gaps where a deduped
    // insert burned a value, so counting would skip events.
    since = batch[batch.length - 1].seq;
    if (batch.length < PAGE) break;
  }

  return out;
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
