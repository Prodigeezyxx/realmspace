/**
 * realmspace — remote edge bus client.
 *
 * Bridges the dashboard to the FastAPI edge API (backend/).
 * When NEXT_PUBLIC_BUS_URL is unset, all helpers no-op so the prototype
 * keeps working fully offline on the localStorage bus.
 *
 * Same event shapes as @/lib/contracts — storage swap, callers unchanged.
 */

import type { RealmEvent, RealmEventInput, RealmEventType } from "@/lib/contracts";

const DEFAULT_URL =
  typeof process !== "undefined"
    ? process.env.NEXT_PUBLIC_BUS_URL?.replace(/\/$/, "") ?? ""
    : "";

export type BusConnectionState = "off" | "connecting" | "live" | "error";

export function getBusBaseUrl(): string {
  return DEFAULT_URL;
}

export function isRemoteBusConfigured(): boolean {
  return Boolean(DEFAULT_URL);
}

export async function remoteAppend(
  input: RealmEventInput & {
    consentProof?: { tier: "T1" | "T2" | "T3"; contactId: string };
  },
  baseUrl = DEFAULT_URL
): Promise<RealmEvent | null> {
  if (!baseUrl) return null;
  const res = await fetch(`${baseUrl}/v1/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    throw new Error(`remoteAppend failed: ${res.status} ${await res.text()}`);
  }
  return (await res.json()) as RealmEvent;
}

export async function remoteRead(opts: {
  tenantId: string;
  sessionId: string;
  afterSeq?: number;
  limit?: number;
  types?: RealmEventType[];
  baseUrl?: string;
}): Promise<RealmEvent[]> {
  const base = opts.baseUrl ?? DEFAULT_URL;
  if (!base) return [];
  const q = new URLSearchParams({
    tenantId: opts.tenantId,
    sessionId: opts.sessionId,
    afterSeq: String(opts.afterSeq ?? 0),
    limit: String(opts.limit ?? 500),
  });
  if (opts.types) {
    for (const t of opts.types) q.append("type", t);
  }
  const res = await fetch(`${base}/v1/events?${q}`);
  if (!res.ok) throw new Error(`remoteRead failed: ${res.status}`);
  return (await res.json()) as RealmEvent[];
}

export async function remoteGraphSnapshot(
  tenantId: string,
  sessionId: string,
  baseUrl = DEFAULT_URL
) {
  if (!baseUrl) return null;
  const res = await fetch(`${baseUrl}/v1/graph/${encodeURIComponent(tenantId)}/${encodeURIComponent(sessionId)}`);
  if (!res.ok) throw new Error(`remoteGraphSnapshot failed: ${res.status}`);
  return res.json();
}

export async function remoteAuthResolve(email: string, baseUrl = DEFAULT_URL) {
  if (!baseUrl) return null;
  const res = await fetch(
    `${baseUrl}/v1/auth/resolve?email=${encodeURIComponent(email)}`
  );
  if (!res.ok) throw new Error(`remoteAuthResolve failed: ${res.status}`);
  return res.json() as Promise<{
    userId: string;
    email: string;
    displayName: string | null;
    orgId: string;
    role: "admin" | "operator" | "analyst" | "viewer";
  }>;
}

export type RemoteBusHandlers = {
  onEvent?: (e: RealmEvent) => void;
  onHello?: (replay: RealmEvent[]) => void;
  onState?: (s: BusConnectionState) => void;
};

/**
 * Open a WebSocket to the edge bus for a tenant/session.
 * Returns a dispose function.
 */
export function subscribeRemoteBus(
  tenantId: string,
  sessionId: string,
  handlers: RemoteBusHandlers,
  baseUrl = DEFAULT_URL
): () => void {
  if (!baseUrl || typeof window === "undefined") {
    handlers.onState?.("off");
    return () => undefined;
  }

  const wsUrl =
    baseUrl.replace(/^http/, "ws") +
    `/v1/ws/${encodeURIComponent(tenantId)}/${encodeURIComponent(sessionId)}`;

  let closed = false;
  let ws: WebSocket | null = null;
  let pingTimer: ReturnType<typeof setInterval> | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;

  const connect = () => {
    if (closed) return;
    handlers.onState?.("connecting");
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      attempt = 0;
      handlers.onState?.("live");
      pingTimer = setInterval(() => {
        try {
          ws?.send("ping");
        } catch {
          /* ignore */
        }
      }, 15000);
    };

    ws.onmessage = (msg) => {
      try {
        const data = JSON.parse(String(msg.data)) as {
          type: string;
          event?: RealmEvent;
          replay?: RealmEvent[];
        };
        if (data.type === "hello" && data.replay) {
          handlers.onHello?.(data.replay);
        } else if (data.type === "event" && data.event) {
          handlers.onEvent?.(data.event);
        }
      } catch {
        /* ignore malformed */
      }
    };

    ws.onerror = () => {
      handlers.onState?.("error");
    };

    ws.onclose = () => {
      if (pingTimer) clearInterval(pingTimer);
      pingTimer = null;
      if (closed) return;
      handlers.onState?.("error");
      const delay = Math.min(10000, 500 * 2 ** attempt);
      attempt += 1;
      retryTimer = setTimeout(connect, delay);
    };
  };

  connect();

  return () => {
    closed = true;
    if (pingTimer) clearInterval(pingTimer);
    if (retryTimer) clearTimeout(retryTimer);
    try {
      ws?.close();
    } catch {
      /* ignore */
    }
    handlers.onState?.("off");
  };
}
