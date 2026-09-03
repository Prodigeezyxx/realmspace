"use client";

/**
 * The operator's side of a consent kiosk: mint, list, withdraw.
 *
 * The kiosk's own side is `lib/consent/useKiosk.ts`, which holds no credential
 * at all. This one is an ordinary authenticated reader over
 * `/v1/sessions/{id}/kiosks`, in `useTablets`' shape — the two panels are the
 * same screen's two producers, and a second idea of what a minted credential
 * looks like would be one more thing to keep in step.
 *
 * ## The token comes back once
 *
 * `POST …/kiosk` returns it in that one response and no endpoint returns it
 * again — the row stores a sha256. The operator's copy is the QR code they
 * print from it; having lost it, the remedy is to mint another and withdraw
 * this one, which is also the right response to a plinth that has walked off.
 */

import { useCallback, useEffect, useState } from "react";

import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";

export interface KioskToken {
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

export type KiosksStatus = "loading" | "ready" | "offline" | "error";

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

/** The URL to print on the QR code, from the token the backend just handed back. */
export function kioskUrl(token: string): string {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return `${origin}/consent?t=${encodeURIComponent(token)}`;
}

export function useKiosks(sessionId: string) {
  const [status, setStatus] = useState<KiosksStatus>(
    isRemoteBusEnabled() ? "loading" : "offline"
  );
  const [kiosks, setKiosks] = useState<KioskToken[]>([]);
  const [detail, setDetail] = useState<string | null>(
    isRemoteBusEnabled()
      ? null
      : "No backend configured. A kiosk records consents on the bus, so there is nothing to hand one here."
  );

  const refresh = useCallback(async () => {
    if (!isRemoteBusEnabled()) return;
    const res = await authed(`/v1/sessions/${encodeURIComponent(sessionId)}/kiosks`);
    if (!res) {
      setStatus("offline");
      return;
    }
    if (!res.ok) {
      setStatus("error");
      setDetail(await res.text());
      return;
    }
    setKiosks((await res.json()) as KioskToken[]);
    setStatus("ready");
    setDetail(null);
  }, [sessionId]);

  useEffect(() => {
    if (!isRemoteBusEnabled()) return;
    // Same narrow suppression, and same reason, as `useTablets`: every setState
    // in `refresh` runs after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  /** Mint one. Returns the link, which is the only time it exists. */
  const mint = useCallback(
    async (surfaceId: string, label?: string): Promise<string | null> => {
      const res = await authed(
        `/v1/sessions/${encodeURIComponent(sessionId)}/surfaces/${encodeURIComponent(surfaceId)}/kiosk`,
        { method: "POST", body: JSON.stringify({ label: label ?? null }) }
      );
      if (!res?.ok) {
        // The backend's own sentence, not a rewrite of it: the 409 for an
        // activation with no consent wording says which two fields are missing
        // and why a surface without them has not really captured consent.
        setDetail(res ? await res.text() : "Not signed in to the backend.");
        return null;
      }
      const created = (await res.json()) as KioskToken & { token: string };
      await refresh();
      return kioskUrl(created.token);
    },
    [sessionId, refresh]
  );

  const revoke = useCallback(
    async (tokenId: string) => {
      const res = await authed(
        `/v1/sessions/${encodeURIComponent(sessionId)}/kiosks/${encodeURIComponent(tokenId)}`,
        { method: "DELETE" }
      );
      if (!res?.ok) setDetail(res ? await res.text() : "Not signed in to the backend.");
      await refresh();
    },
    [sessionId, refresh]
  );

  return { status, kiosks, detail, refresh, mint, revoke };
}
