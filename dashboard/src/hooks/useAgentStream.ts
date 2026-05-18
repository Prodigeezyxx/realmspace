"use client";

import { useEffect, useState } from "react";

import type { AgentRunResult } from "@/agents/types";
import {
  AGENT_ALERT_CHANNEL,
  AGENT_STREAM_CHANNEL,
  busSubscribe,
  type BusMessage,
} from "@/lib/event-bus";

export function useAgentStream(agentId?: string) {
  const [last, setLast] = useState<AgentRunResult | null>(null);
  const [history, setHistory] = useState<AgentRunResult[]>([]);

  useEffect(() => {
    const handler = (msg: BusMessage) => {
      const result = msg.payload as AgentRunResult;
      if (agentId && result.agentId !== agentId) return;
      setLast(result);
      setHistory((h) => [result, ...h].slice(0, 50));
    };
    return busSubscribe(AGENT_STREAM_CHANNEL, handler);
  }, [agentId]);

  return { last, history };
}

export function useAgentAlerts() {
  const [alerts, setAlerts] = useState<
    { title: string; body: string; ts: number }[]
  >([]);

  useEffect(() => {
    return busSubscribe(AGENT_ALERT_CHANNEL, (msg) => {
      const p = msg.payload as { title?: string; body?: string };
      setAlerts((a) => [
        {
          title: p.title ?? "Alert",
          body: p.body ?? "",
          ts: msg.timestamp,
        },
        ...a,
      ].slice(0, 20));
    });
  }, []);

  return alerts;
}
