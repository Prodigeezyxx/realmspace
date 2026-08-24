/**
 * Stack: message payload → { sent, channel }
 * Channels: in_app (event bus), webhook (fetch), log (console)
 */
import {
  AGENT_ALERT_CHANNEL,
  busEmit,
} from "@/lib/event-bus";
import type { SkillModule, SkillRunInput } from "./types";

export interface NotifyPayload {
  title: string;
  body: string;
  zoneId?: string;
  metric?: string;
  value?: number;
  thumbnailUrl?: string;
}

export interface NotifyOutput {
  sent: boolean;
  channel: string;
}

export const notifySkill: SkillModule<NotifyOutput> = {
  id: "notify",
  stack: "payload message → in_app | webhook | log",
  async run(_input: SkillRunInput, config) {
    const channel = (config.channel as string) ?? "in_app";
    const payload: NotifyPayload = (config.message as NotifyPayload) ?? {
      title: String(_input.trigger.payload.title ?? "realmspace alert"),
      body: String(_input.trigger.payload.body ?? "Threshold crossed"),
      zoneId: _input.trigger.payload.zoneId as string | undefined,
      metric: _input.trigger.payload.metric as string | undefined,
      value: _input.trigger.payload.count as number | undefined,
    };

    if (channel === "in_app") {
      busEmit(AGENT_ALERT_CHANNEL, { ...payload, channel });
      return { sent: true, channel };
    }

    if (channel === "webhook") {
      const url = config.webhookUrl as string | undefined;
      if (url && typeof fetch !== "undefined") {
        try {
          await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          return { sent: true, channel };
        } catch {
          return { sent: false, channel };
        }
      }
      console.info("[notify:webhook stub]", payload);
      return { sent: true, channel: "webhook-stub" };
    }

    console.info("[notify:log]", payload);
    return { sent: true, channel: "log" };
  },
};
