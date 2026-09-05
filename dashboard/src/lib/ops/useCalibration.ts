"use client";

/**
 * realmspace — the calibration step, client side.
 *
 * `privacy.md` §"Sensitive zones" has promised since Phase 0 that an operator
 * can draw an opt-out polygon "on the calibration step" and that pixels inside
 * it are masked before any model runs. There was no calibration step. This is
 * it, and `perception/mask.py` is the half that keeps the promise.
 *
 * ## What this screen cannot show, and why
 *
 * **There is no camera preview.** The polygon is drawn against normalized frame
 * coordinates on an empty grid, which is worse to use than drawing over a still
 * frame and is the honest option available. Frames live in a 60-second RAM ring
 * buffer on the perception laptop and `privacy.md` says plainly that they never
 * leave it; there is no endpoint that serves one, and adding one is a decision
 * about the privacy posture rather than a convenience for this screen.
 *
 * The consequence is stated on the screen rather than hidden: an operator
 * confirms the mask by looking at the perception preview window, where the
 * masked region is filled black.
 */

import { useCallback, useEffect, useState } from "react";

import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";

/** A point in normalized frame coordinates, 0..1. Same space a zone uses. */
export type Point = [number, number];

export interface CameraConfig {
  id: string;
  label: string;
  privacyMask: Point[] | null;
  maskRevision: number;
}

/**
 * A zone as the backend stores it, carried through this hook untouched.
 *
 * Deliberately open: `POST /v1/sessions` replaces the whole zone set and prunes
 * what is not in it, so re-posting a zone means re-posting *everything* it had
 * — polygon, weight, funnel order, capacity. Narrowing this type to the two
 * fields this screen edits would drop the rest on the first save, and the
 * operator would find their funnel ordering gone with nothing to explain it.
 */
export interface ZoneConfig {
  id: string;
  name?: string;
  /** Which camera's frame the polygon was drawn in. Null means every camera. */
  cameraId?: string | null;
  [key: string]: unknown;
}

export type CalibrationStatus = "loading" | "ready" | "offline" | "error";

export interface CalibrationState {
  status: CalibrationStatus;
  cameras: CameraConfig[];
  zones: ZoneConfig[];
  detail: string | null;
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

function initialState(): CalibrationState {
  return isRemoteBusEnabled()
    ? { status: "loading", cameras: [], zones: [], detail: null }
    : {
        status: "offline",
        cameras: [],
        zones: [],
        detail:
          "No backend configured. A mask has to reach the edge box to do anything, so there is nothing to draw against here.",
      };
}

export function useCalibration(sessionId: string) {
  const [state, setState] = useState<CalibrationState>(initialState);

  const refresh = useCallback(async () => {
    if (!isRemoteBusEnabled()) return;
    try {
      const res = await authed(`/v1/sessions/${encodeURIComponent(sessionId)}`);
      if (!res) {
        setState({
          status: "error",
          cameras: [],
          zones: [],
          detail: "Could not authenticate with the bus.",
        });
        return;
      }
      if (res.status === 404) {
        setState({
          status: "error",
          cameras: [],
          zones: [],
          detail: "This session has no configuration yet. Publish it first.",
        });
        return;
      }
      if (!res.ok) {
        setState({
          status: "error",
          cameras: [],
          zones: [],
          detail: `The bus returned ${res.status}.`,
        });
        return;
      }
      const config = await res.json();
      setState({
        status: "ready",
        cameras: (config.cameras ?? []) as CameraConfig[],
        zones: (config.zones ?? []) as ZoneConfig[],
        detail: null,
      });
    } catch {
      setState({
        status: "error",
        cameras: [],
        zones: [],
        detail: "The bus is unreachable.",
      });
    }
  }, [sessionId]);

  useEffect(() => {
    if (!isRemoteBusEnabled()) return;
    // Same narrow suppression, and same reason, as `useDeadLetters`: every
    // setState in `refresh` runs after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  /**
   * Declare a camera on this session. The precondition for calibrating one.
   *
   * Posts the **whole** camera set, because that is the contract
   * `POST /v1/sessions` has for zones, touchpoints and cameras alike: a list
   * replaces the set and prunes what is not in it. Sending only the new one
   * would delete every other camera and its mask.
   */
  const declare = useCallback(
    async (cameraId: string, label: string): Promise<string | null> => {
      const cameras = [
        ...state.cameras.map((c) => ({ id: c.id, label: c.label })),
        { id: cameraId, label },
      ];
      const res = await authed("/v1/sessions", {
        method: "POST",
        body: JSON.stringify({ sessionId, cameras }),
      });
      if (!res?.ok) {
        const body = await res?.json().catch(() => null);
        return body?.detail
          ? JSON.stringify(body.detail)
          : `Could not add the camera (${res?.status ?? "offline"}).`;
      }
      await refresh();
      return null;
    },
    [refresh, sessionId, state.cameras]
  );

  /**
   * Say which camera's frame a zone was drawn in. `null` means every camera.
   *
   * Why this matters, since it looks like bookkeeping: a zone polygon is
   * normalized 0..1 *within one frame*, so the same numbers name different
   * floor in a different camera. Unowned, a zone drawn over the demo stand on
   * `cam-1` also swallows anybody standing in the corresponding corner of
   * `cam-2` — a visitor credited with a stay in a part of the booth they were
   * never in, which reads in the report exactly like a real one.
   *
   * Posts the **whole** zone set, each zone exactly as it came back from the
   * backend, for the reason `declare` posts the whole camera set: the endpoint
   * replaces the set and prunes what is missing from it.
   */
  const assignZone = useCallback(
    async (zoneId: string, cameraId: string | null): Promise<string | null> => {
      const zones = state.zones.map((z) =>
        z.id === zoneId ? { ...z, cameraId } : z
      );
      const res = await authed("/v1/sessions", {
        method: "POST",
        body: JSON.stringify({ sessionId, zones }),
      });
      if (!res?.ok) {
        const body = await res?.json().catch(() => null);
        return body?.detail
          ? String(body.detail)
          : `Could not save the zone (${res?.status ?? "offline"}).`;
      }
      await refresh();
      return null;
    },
    [refresh, sessionId, state.zones]
  );

  /**
   * Set or clear one camera's mask. `null` clears it.
   *
   * A separate endpoint from the config save, deliberately: this is an audited
   * action with a revision and an author, and the revision is what tells a
   * running edge box that the mask it cached is stale.
   */
  const setMask = useCallback(
    async (cameraId: string, polygon: Point[] | null, note: string): Promise<string | null> => {
      const res = await authed(
        `/v1/sessions/${encodeURIComponent(sessionId)}/cameras/${encodeURIComponent(
          cameraId
        )}/calibration`,
        {
          method: "POST",
          body: JSON.stringify({ kind: "privacy_mask", polygon, note: note || null }),
        }
      );
      if (!res) return "Could not authenticate with the bus.";
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        // The backend's refusals explain themselves — a 409 on `zone_map`
        // points at the endpoint that owns zone geometry, and a 422 names the
        // coordinate space. Passing the reason through is the whole value.
        return typeof body?.detail === "string"
          ? body.detail
          : `The bus returned ${res.status}.`;
      }
      await refresh();
      return null;
    },
    [refresh, sessionId]
  );

  return { ...state, refresh, declare, assignZone, setMask };
}
