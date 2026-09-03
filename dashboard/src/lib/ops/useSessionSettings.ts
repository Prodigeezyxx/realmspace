"use client";

/**
 * An activation's settings, after the wizard.
 *
 * Every parameter the ROI report divides by was set once, in a five-step wizard,
 * and could never be changed: `publishSessionConfig` had exactly one caller and
 * `sessionActions.updateSession` was called from two places, neither a setting.
 * So an operator could not correct a mistyped cost, and could not enter the
 * client's influenced-revenue figure at all — which `roi-framework.md` §3 says
 * arrives *after* the activation, and which is the numerator of the ratio the
 * whole product is sold on. The only way in was `curl`.
 *
 * ## A partial save, never `sessionToWire`
 *
 * `publishSessionConfig(session)` posts the **whole** configuration, and
 * `zones` / `touchpoints` / `cameras` are replace-and-prune. Reusing it here
 * would post this browser's idea of the zones and delete the ones the operator
 * drew on the calibration screen — the Phase 6 acceptance run's defect 4 in the
 * other direction, and just as quiet.
 *
 * So this posts `{sessionId, …only the fields the form changed}`, which the
 * backend applies with `SET s += $props` over `model_fields_set`. An omitted
 * field is left alone; an explicit `null` clears one, which is how a cost typed
 * by mistake is removed.
 *
 * ## And it mirrors into the local store
 *
 * The backend is the authority — `useSessionReport` has always read it back
 * rather than trusting this laptop. But a deployment with **no** backend is the
 * laptop demo, where the browser store is the only copy there is. So a save
 * writes both, and everything that reads a figure resolves them through
 * `lib/roi/measurement.ts` rather than picking one.
 */

import { useCallback, useEffect, useState } from "react";

import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";
import type { RemoteSessionConfig } from "@/lib/session/publish";

export type SettingsStatus = "loading" | "ready" | "offline" | "error";

/**
 * What a save may carry. Deliberately not `Partial<RemoteSessionConfig>`: the
 * zone, touchpoint and camera lists are on that type and must never be sent
 * from this screen — see the module docstring.
 */
export interface SettingsPatch {
  client?: string | null;
  campaign?: string | null;
  venue?: string | null;
  city?: string | null;
  startedAt?: string | null;
  endsAt?: string | null;
  activationCost?: number | null;
  currency?: string;
  revenueInfluenced?: number | null;
  qualifiedLeads?: number | null;
  engagedThresholdSeconds?: number;
  attributionModel?: string;
  attributionWindowDays?: number;
  anonymousHandoffs?: boolean;
  insightIntervalMinutes?: number;
  consentCopy?: string | null;
  consentCopyVersion?: string | null;
  consentTier?: "T1" | "T2" | "T3";
}

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

export function useSessionSettings(sessionId: string) {
  const [status, setStatus] = useState<SettingsStatus>(
    isRemoteBusEnabled() ? "loading" : "offline"
  );
  const [config, setConfig] = useState<RemoteSessionConfig | null>(null);
  const [detail, setDetail] = useState<string | null>(
    isRemoteBusEnabled()
      ? null
      : "No backend configured, so these settings live in this browser only."
  );

  const refresh = useCallback(async () => {
    if (!isRemoteBusEnabled()) return;
    try {
      const res = await authed(`/v1/sessions/${encodeURIComponent(sessionId)}`);
      if (!res) {
        setStatus("error");
        setDetail("Could not authenticate with the bus.");
        return;
      }
      if (res.status === 404) {
        // Not an error to fix here: the activation exists in this browser and
        // has never been published. The wizard is what publishes one, and
        // saying so is more useful than a status code.
        setStatus("error");
        setDetail(
          "This activation has never been published to the bus, so there is nothing stored to edit."
        );
        return;
      }
      if (!res.ok) {
        setStatus("error");
        setDetail(`The bus returned ${res.status}.`);
        return;
      }
      setConfig((await res.json()) as RemoteSessionConfig);
      setStatus("ready");
      setDetail(null);
    } catch {
      setStatus("error");
      setDetail("The bus is unreachable.");
    }
  }, [sessionId]);

  useEffect(() => {
    if (!isRemoteBusEnabled()) return;
    // The same narrow suppression as `useCalibration`, for its reason: every
    // setState in `refresh` runs after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  /** Save a patch. Returns the reason it failed, or null. */
  const save = useCallback(
    async (patch: SettingsPatch): Promise<string | null> => {
      if (!isRemoteBusEnabled()) return null; // local-only; the caller mirrors
      const res = await authed("/v1/sessions", {
        method: "POST",
        body: JSON.stringify({ sessionId, ...patch }),
      });
      if (!res) return "Could not authenticate with the bus.";
      if (res.status === 403) {
        // The browser does not know the caller's role — the token is opaque
        // here — so the refusal is the server's, phrased as `publish.ts`
        // phrases it rather than guessed at before the request.
        return "Your role cannot configure an activation (needs admin or operator).";
      }
      if (!res.ok) {
        // 422 names the field, 402 names the plan and the tier that would
        // allow this. Both are sentences written for the person reading them.
        const body = await res.json().catch(() => null);
        const reason = (body as { detail?: unknown } | null)?.detail;
        if (typeof reason === "string") return reason;
        if (reason) return JSON.stringify(reason);
        return `The bus rejected the change (${res.status}).`;
      }
      setConfig((await res.json()) as RemoteSessionConfig);
      return null;
    },
    [sessionId]
  );

  return { status, config, detail, refresh, save };
}
