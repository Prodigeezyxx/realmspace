"use client";

/**
 * The operator's side of a touchpoint tablet: mint, list, withdraw.
 *
 * The tablet's own side is `lib/touch/useTablet.ts`, which holds no credential
 * at all. This one is an ordinary authenticated reader over
 * `/v1/sessions/{id}/tablets`, in the shape `useCalibration` established for
 * every other session-scoped setting screen.
 *
 * ## The token comes back once
 *
 * `POST …/tablet` returns it in that one response and no endpoint returns it
 * again — the row stores a sha256. So the link is held in component state for
 * as long as the operator has the panel open, and after that the remedy for
 * having lost it is to mint another and withdraw this one, which is also the
 * right response to having left the tablet in a taxi.
 */

import { useCallback, useEffect, useState } from "react";

import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";

export interface TabletToken {
  id: string;
  sessionId: string;
  surfaceId: string;
  label: string | null;
  hint: string;
  createdBy: string;
  createdAt: string;
  expiresAt: string;
  revokedAt: string | null;
  lastUsedAt: string | null;
  useCount: number;
  active: boolean;
}

export type TabletsStatus = "loading" | "ready" | "offline" | "error";

async function authed(path: string, init?: RequestInit): Promise<Response | null> {
  const token = await ensureToken(busEmail());
  if (!token) return null;
  return fetch(`${busUrl()}${path}`, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });
}

/** The URL to open on the tablet, from the token the backend just handed back. */
export function tabletUrl(token: string): string {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return `${origin}/touch?t=${encodeURIComponent(token)}`;
}

export function useTablets(sessionId: string) {
  const [status, setStatus] = useState<TabletsStatus>(
    isRemoteBusEnabled() ? "loading" : "offline"
  );
  const [tablets, setTablets] = useState<TabletToken[]>([]);
  const [detail, setDetail] = useState<string | null>(
    isRemoteBusEnabled()
      ? null
      : "No backend configured. A tablet posts its taps to the bus, so there is nothing to hand one here."
  );

  const refresh = useCallback(async () => {
    if (!isRemoteBusEnabled()) return;
    const res = await authed(`/v1/sessions/${encodeURIComponent(sessionId)}/tablets`);
    if (!res) {
      setStatus("offline");
      return;
    }
    if (!res.ok) {
      setStatus("error");
      setDetail(await res.text());
      return;
    }
    setTablets((await res.json()) as TabletToken[]);
    setStatus("ready");
    setDetail(null);
  }, [sessionId]);

  useEffect(() => {
    if (!isRemoteBusEnabled()) return;
    // Same narrow suppression, and same reason, as `useCalibration`: every
    // setState in `refresh` runs after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  /** Mint one. Returns the link, which is the only time it exists. */
  const mint = useCallback(
    async (surfaceId: string, label?: string): Promise<string | null> => {
      const res = await authed(
        `/v1/sessions/${encodeURIComponent(sessionId)}/surfaces/${encodeURIComponent(surfaceId)}/tablet`,
        { method: "POST", body: JSON.stringify({ label: label ?? null }) }
      );
      if (!res?.ok) {
        // The backend's own sentence, not a rewrite of it: the 404 for an
        // unconfigured touchpoint names the surface and says to add it first.
        setDetail(res ? await res.text() : "Not signed in to the backend.");
        return null;
      }
      const created = (await res.json()) as TabletToken & { token: string };
      await refresh();
      return tabletUrl(created.token);
    },
    [sessionId, refresh]
  );

  const revoke = useCallback(
    async (tokenId: string) => {
      const res = await authed(
        `/v1/sessions/${encodeURIComponent(sessionId)}/tablets/${encodeURIComponent(tokenId)}`,
        { method: "DELETE" }
      );
      if (!res?.ok) setDetail(res ? await res.text() : "Not signed in to the backend.");
      await refresh();
    },
    [sessionId, refresh]
  );

  return { status, tablets, detail, refresh, mint, revoke };
}
