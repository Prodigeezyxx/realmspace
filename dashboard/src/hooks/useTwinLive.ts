"use client";

import { useEffect, useState } from "react";

import type { AgentRunResult } from "@/agents/types";
import {
  AGENT_STREAM_CHANNEL,
  TWIN_UPDATE_CHANNEL,
  busSubscribe,
} from "@/lib/event-bus";
import type { HeatmapOutput } from "@/skills/heatmap";
import type { TwinAvatarDelta } from "@/skills/twin-sync";

export function useTwinLive() {
  const [avatars, setAvatars] = useState<TwinAvatarDelta[]>([]);
  const [heatmap, setHeatmap] = useState<HeatmapOutput | null>(null);
  const [layoutZones, setLayoutZones] = useState<
    { id: string; label: string; polygon: [number, number][] }[] | null
  >(null);

  useEffect(() => {
    return busSubscribe(TWIN_UPDATE_CHANNEL, (msg) => {
      const payload = msg.payload as {
        avatars?: TwinAvatarDelta[];
        zones?: { id: string; label: string; polygon: [number, number][] }[];
      };
      if (payload.avatars?.length) setAvatars(payload.avatars);
      if (payload.zones?.length) setLayoutZones(payload.zones);
    });
  }, []);

  useEffect(() => {
    return busSubscribe(AGENT_STREAM_CHANNEL, (msg) => {
      const result = msg.payload as AgentRunResult;
      if (result.agentId === "heatmap") {
        const hm = result.skillChain.heatmap as HeatmapOutput | undefined;
        if (hm) setHeatmap(hm);
      }
      if (result.agentId === "layout") {
        const twin = result.skillChain["twin-sync"] as {
          zones?: { id: string; label: string; polygon: [number, number][] }[];
        };
        if (twin?.zones) setLayoutZones(twin.zones);
      }
    });
  }, []);

  return { avatars, heatmap, layoutZones };
}
