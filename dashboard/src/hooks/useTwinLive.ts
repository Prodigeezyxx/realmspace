"use client";

import { useEffect, useMemo, useState } from "react";

import type { AgentRunResult } from "@/agents/types";
import {
  AGENT_STREAM_CHANNEL,
  TWIN_UPDATE_CHANNEL,
  busSubscribe,
} from "@/lib/event-bus";
import { useLiveSession } from "@/lib/live-session/store";
import { tracksToAvatars } from "@/lib/live-session/twin-emit";
import type { HeatmapOutput } from "@/skills/heatmap";
import type { TwinAvatarDelta } from "@/skills/twin-sync";

export function useTwinLive() {
  const detectorRunning = useLiveSession((s) => s.status === "running");
  const stats = useLiveSession((s) => s.stats);
  const storeHeatmap = useLiveSession((s) => s.heatmap);

  const [busAvatars, setBusAvatars] = useState<TwinAvatarDelta[]>([]);
  const [busHeatmap, setBusHeatmap] = useState<HeatmapOutput | null>(null);
  const [layoutZones, setLayoutZones] = useState<
    { id: string; label: string; polygon: [number, number][] }[] | null
  >(null);

  useEffect(() => {
    return busSubscribe(TWIN_UPDATE_CHANNEL, (msg) => {
      const payload = msg.payload as {
        avatars?: TwinAvatarDelta[];
        zones?: { id: string; label: string; polygon: [number, number][] }[];
      };
      if (payload.avatars) setBusAvatars(payload.avatars);
      if (payload.zones?.length) setLayoutZones(payload.zones);
    });
  }, []);

  useEffect(() => {
    return busSubscribe(AGENT_STREAM_CHANNEL, (msg) => {
      const result = msg.payload as AgentRunResult;
      if (result.agentId === "heatmap") {
        const hm = result.skillChain.heatmap as HeatmapOutput | undefined;
        if (hm) setBusHeatmap(hm);
      }
      if (result.agentId === "layout") {
        const twin = result.skillChain["twin-sync"] as {
          zones?: { id: string; label: string; polygon: [number, number][] }[];
        };
        if (twin?.zones) setLayoutZones(twin.zones);
      }
    });
  }, []);

  const avatars = useMemo(() => {
    if (detectorRunning && stats) {
      return tracksToAvatars(
        stats.activeTracks,
        stats.frameWidth,
        stats.frameHeight
      );
    }
    return busAvatars;
  }, [detectorRunning, stats, busAvatars]);

  const heatmap = detectorRunning
    ? storeHeatmap ?? busHeatmap
    : busHeatmap;

  return { avatars, heatmap, layoutZones };
}
