"use client";

import { zoneStats } from "@/lib/mock/session";
import { useActiveSession } from "@/lib/session/store";
import { formatDuration } from "@/lib/utils";

export function ZoneList() {
  const active = useActiveSession();
  const isDemo = active.isDemo;

  // Demo: show the curated stats. Otherwise: show this session's configured
  // zones with placeholder values (will become live once detection wiring
  // attributes tracks to zones).
  return (
    <ul className="divide-y divide-border-hairline">
      {active.zones.map((z) => {
        const demoStat = isDemo
          ? zoneStats.find((zs) => zs.zoneId === z.id)
          : undefined;
        const now = demoStat?.nowCount ?? 0;
        const avg = demoStat?.avgDwell ?? 0;
        const capture = demoStat?.capture;

        return (
          <li
            key={z.id}
            className="grid grid-cols-[14px_1fr_auto_auto] items-center gap-3 py-3"
          >
            <span
              className="w-2.5 h-2.5 rounded-full"
              style={{
                background: z.color ?? "#a8a8ad",
                boxShadow: `0 0 12px ${z.color ?? "#a8a8ad"}`,
              }}
            />
            <div className="min-w-0">
              <div className="text-sm font-medium truncate">{z.name}</div>
              <div className="text-[11px] text-text-muted tabular">
                {demoStat
                  ? `avg ${formatDuration(avg)} · ${Math.round((capture ?? 0) * 100)}% capture`
                  : z.capacity
                    ? `capacity ${z.capacity} · awaiting data`
                    : "awaiting data"}
              </div>
            </div>
            <div className="text-2xl font-semibold tabular tracking-tight">
              {now}
            </div>
            <div className="text-[10px] uppercase text-text-muted tabular tracking-[0.12em]">
              now
            </div>
          </li>
        );
      })}

      {active.zones.length === 0 && (
        <li className="py-6 text-center text-sm text-text-muted">
          No zones configured for this session.
        </li>
      )}
    </ul>
  );
}
