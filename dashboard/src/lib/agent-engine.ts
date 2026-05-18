/**
 * Dispatches trigger events to matching agents and handles dwell / threshold logic.
 */
import { agentsForTrigger } from "@/agents/registry";
import { runAgent } from "@/agents/runtime";
import type { TriggerEvent } from "@/agents/types";
import { busEmit, TWIN_UPDATE_CHANNEL } from "@/lib/event-bus";
import type { Track } from "@/lib/tracker";
import type { TwinSyncOutput } from "@/skills/twin-sync";
import type { ZoneDetectOutput } from "@/skills/zone-detect";

const dwellStart = new Map<string, number>();
const lastAlertAt = new Map<string, number>();
let previousZoneByPerson: Record<number, string> = {};

export async function dispatchTrigger(event: TriggerEvent) {
  const matches = agentsForTrigger(event.type);
  const results = await Promise.all(
    matches.map((agent) => runAgent(agent, event))
  );

  for (const r of results) {
    const twin = r.skillChain["twin-sync"] as TwinSyncOutput | undefined;
    if (twin?.avatars?.length) {
      busEmit(TWIN_UPDATE_CHANNEL, twin);
    }
  }

  return results;
}

export function resetAgentEngineState() {
  dwellStart.clear();
  lastAlertAt.clear();
  previousZoneByPerson = {};
}

/** Call each frame from Live detector with active tracks */
export async function processFrameTracks(
  tracks: Track[],
  frameTs: number,
  frameWidth = 1280,
  frameHeight = 720
) {
  const base = {
    tracks,
    frameWidth,
    frameHeight,
    previousZoneByPerson,
  };

  const results = await dispatchTrigger({
    type: "frame_tracks",
    timestamp: frameTs,
    payload: base,
  });

  for (const r of results) {
    const zd = r.skillChain["zone-detect"] as ZoneDetectOutput | undefined;
    if (!zd?.occupancy) continue;
    for (const [zoneId, count] of Object.entries(zd.occupancy)) {
      checkZoneThreshold(zoneId, count, frameTs);
    }
    const trackOut = r.skillChain.track as {
      persons?: { id: number; label: string }[];
    };
    if (trackOut?.persons) {
      const next: Record<number, string> = {};
      for (const p of trackOut.persons) {
        const enter = zd.crossings.find(
          (c) => c.personId === p.id && c.kind === "enter"
        );
        if (enter) next[p.id] = enter.zoneId;
        else if (previousZoneByPerson[p.id]) next[p.id] = previousZoneByPerson[p.id];
      }
      previousZoneByPerson = next;
    }
  }

  // Dwell detection
  const thresholdMs = 30_000;
  for (const t of tracks) {
    if (t.missCount > 0) {
      dwellStart.delete(String(t.id));
      continue;
    }
    const key = String(t.id);
    if (!dwellStart.has(key)) dwellStart.set(key, t.firstSeen);
    const dwell = frameTs - (dwellStart.get(key) ?? t.firstSeen);
    if (dwell >= thresholdMs) {
      await dispatchTrigger({
        type: "person_dwell_exceeded",
        timestamp: frameTs,
        payload: {
          ...base,
          personId: t.id,
          personLabel: t.label,
          dwellMs: dwell,
          thresholdMs,
        },
      });
      dwellStart.set(key, frameTs);
    }
  }
}

function checkZoneThreshold(zoneId: string, count: number, ts: number) {
  const alertAgents = agentsForTrigger("zone_threshold_exceeded");
  for (const agent of alertAgents) {
    const cfg = agent.trigger.config ?? {};
    if (cfg.zoneId !== zoneId) continue;
    const threshold = (cfg.threshold as number) ?? 5;
    const cooldown = (cfg.cooldownMs as number) ?? 60_000;
    if (count < threshold) continue;
    const key = `${agent.id}:${zoneId}`;
    if (ts - (lastAlertAt.get(key) ?? 0) < cooldown) continue;
    lastAlertAt.set(key, ts);
    void dispatchTrigger({
      type: "zone_threshold_exceeded",
      timestamp: ts,
      payload: {
        zoneId,
        count,
        metric: cfg.metric ?? "count",
        threshold,
        title: "Zone threshold exceeded",
        body: `${count} in zone (limit ${threshold})`,
      },
    });
  }
}

export async function runNlqQuery(query: string) {
  return dispatchTrigger({
    type: "nlq_query",
    timestamp: Date.now(),
    payload: { query },
  });
}

export async function runSessionReport(summary: Record<string, unknown>) {
  return dispatchTrigger({
    type: "session_ended",
    timestamp: Date.now(),
    payload: { summary },
  });
}
