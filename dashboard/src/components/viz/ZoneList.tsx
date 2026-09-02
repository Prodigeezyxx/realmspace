"use client";

import { zoneStats } from "@/lib/mock/session";
import { useActiveSession } from "@/lib/session/store";
import { formatDuration } from "@/lib/utils";

interface ZoneListProps {
  /**
   * People in each zone right now, from `useLiveStats`. A zone with nobody in
   * it is absent from the map — see `lib/live/derive.ts::zoneOccupancy`.
   */
  occupancy?: Map<string, number>;
  /**
   * Has the durable log anything for this session at all? The difference
   * between "nobody is in this zone" and "nothing is measuring" — the same
   * distinction `/report` draws between a quiet day and a broken pipeline, and
   * the reason a `0` here has to be earned rather than defaulted.
   */
  hasData?: boolean;
}

/**
 * Which zones have people in them, right now.
 *
 * Until 2026-09-02 this rendered a hardcoded `0` beside every zone of every real
 * activation, with "capacity 15 · awaiting data" underneath a session that was
 * running — the curated demo numbers on one side of a ternary and a literal zero
 * on the other. That is the invented-figure class Phase 2 struck off `/report`
 * and `/live`, surviving one component over, exactly as the twin's mock panels
 * did.
 *
 * `capacity` is the operator's own number (`data-model.md`: "optional, for
 * crowding alerts"). Where they set one it is shown as a ratio and the zone goes
 * to the action accent at or over it — the same threshold
 * `consumers/occupancy.py` emits `spatial.occupancy` on, so the panel and the
 * staff prompt beside it cannot disagree about whether a zone is full. Where
 * they did not, no ratio is invented.
 */
export function ZoneList({ occupancy, hasData = false }: ZoneListProps) {
  const active = useActiveSession();
  const isDemo = active.isDemo;

  return (
    <ul className="divide-y divide-border-hairline">
      {active.zones.map((z) => {
        const demoStat = isDemo
          ? zoneStats.find((zs) => zs.zoneId === z.id)
          : undefined;
        // A real zero is a measurement and is shown as one; no log at all is
        // not, and reads as an absence.
        const now = demoStat
          ? demoStat.nowCount
          : hasData
            ? (occupancy?.get(z.id) ?? 0)
            : null;
        const avg = demoStat?.avgDwell ?? 0;
        const capture = demoStat?.capture;
        const atCapacity =
          z.capacity != null && now != null && now >= z.capacity;

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
                    ? atCapacity
                      ? `at capacity · ${now} of ${z.capacity}`
                      : `capacity ${z.capacity}`
                    : hasData
                      ? "no capacity set"
                      : "awaiting data"}
              </div>
            </div>
            <div
              className={`text-2xl font-semibold tabular tracking-tight ${
                atCapacity ? "text-accent-action" : ""
              }`}
            >
              {now ?? "—"}
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
