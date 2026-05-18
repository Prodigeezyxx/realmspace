"use client";

import {
  DoorClosed,
  DoorOpen,
  Eye,
  ListChecks,
  type LucideIcon,
  Sparkles,
  Timer,
  Users,
  Zap,
} from "lucide-react";

import { EmptyState } from "@/components/ui/EmptyState";
import { events, type SessionEvent } from "@/lib/mock/events";
import { useActiveSession } from "@/lib/session/store";
import { formatRelative } from "@/lib/utils";

const ICON: Record<SessionEvent["type"], { icon: LucideIcon; color: string }> = {
  enter: { icon: DoorOpen, color: "text-accent" },
  exit: { icon: DoorClosed, color: "text-text-secondary" },
  dwell: { icon: Timer, color: "text-accent-blue" },
  gaze: { icon: Eye, color: "text-accent-cyan" },
  trigger: { icon: Zap, color: "text-accent-amber" },
  group: { icon: Users, color: "text-accent-violet" },
  insight: { icon: Sparkles, color: "text-accent-violet" },
};

const NOW = Date.UTC(2026, 4, 18, 21, 14, 0);

export function EventTimeline() {
  const active = useActiveSession();
  if (!active.isDemo) {
    return (
      <EmptyState
        icon={<ListChecks size={18} />}
        title="No events yet."
        hint="Events stream in as visitors enter zones, dwell on touchpoints, or fire agent rules."
      />
    );
  }
  return (
    <ul className="font-mono text-xs space-y-px">
      {events.map((e) => {
        const { icon: Icon, color } = ICON[e.type];
        return (
          <li
            key={e.id}
            className="group grid grid-cols-[56px_18px_1fr_auto] items-center gap-3 px-3 py-2 hover:bg-bg-elevated rounded-md transition-colors"
          >
            <span className="text-text-faint tabular">
              {new Date(e.timestamp).toLocaleTimeString("en-US", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false,
                timeZone: "UTC",
              })}
            </span>
            <Icon size={14} className={color} />
            <span className="text-text-primary truncate">
              {e.type === "insight" && (
                <span className="text-accent-violet mr-1.5 uppercase tracking-[0.16em] text-[10px]">
                  insight
                </span>
              )}
              {e.text}
            </span>
            <span className="text-text-muted text-[10px] tabular">
              {formatRelative(NOW - e.timestamp)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
