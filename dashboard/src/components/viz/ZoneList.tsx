"use client";

import { zoneStats, zones } from "@/lib/mock/session";
import { formatDuration } from "@/lib/utils";

export function ZoneList() {
  return (
    <ul className="divide-y divide-border-hairline">
      {zoneStats.map((zs) => {
        const z = zones.find((zz) => zz.id === zs.zoneId);
        return (
          <li
            key={zs.zoneId}
            className="grid grid-cols-[14px_1fr_auto_auto] items-center gap-3 py-3"
          >
            <span
              className="w-2.5 h-2.5 rounded-full"
              style={{
                background: z?.color,
                boxShadow: `0 0 12px ${z?.color}`,
              }}
            />
            <div className="min-w-0">
              <div className="text-sm font-medium truncate">{zs.name}</div>
              <div className="text-[11px] text-text-muted tabular">
                avg {formatDuration(zs.avgDwell)} · {Math.round(zs.capture * 100)}% capture
              </div>
            </div>
            <div className="text-2xl font-semibold tabular tracking-tight">
              {zs.nowCount}
            </div>
            <div className="text-[10px] uppercase text-text-muted tabular tracking-[0.12em]">
              now
            </div>
          </li>
        );
      })}
    </ul>
  );
}
