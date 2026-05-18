"use client";

/**
 * Heatmap: aggregated positions across the session, rendered as a soft glow
 * overlay on the booth floor plan.
 *
 * Only shown when the active session is the demo (which has fabricated
 * waypoints). User-created sessions get an empty state until cameras
 * actually record positions.
 */

import { Flame } from "lucide-react";
import { useMemo } from "react";

import { EmptyState } from "@/components/ui/EmptyState";
import { peopleTracks } from "@/lib/mock/people";
import { useActiveSession } from "@/lib/session/store";

export function Heatmap({ height = 200 }: { height?: number }) {
  const active = useActiveSession();

  // Aggregate every waypoint into a coarse 24x14 grid
  const grid = useMemo(() => {
    const W = 32;
    const H = 18;
    const cells = Array.from({ length: H }, () => new Array(W).fill(0));
    peopleTracks.forEach((p) => {
      p.waypoints.forEach(([x, y, t], i) => {
        if (i === 0) return;
        const dwell = t - p.waypoints[i - 1][2];
        const cx = Math.floor(x * (W - 1));
        const cy = Math.floor(y * (H - 1));
        cells[cy][cx] += Math.min(60, dwell);
      });
    });
    return { cells, W, H };
  }, []);

  const max = Math.max(1, ...grid.cells.flat());

  if (!active.isDemo) {
    return (
      <div
        className="relative w-full rounded-lg overflow-hidden bg-bg-canvas map-grid border border-dashed border-border-subtle flex items-center justify-center"
        style={{ height }}
      >
        <EmptyState
          icon={<Flame size={18} />}
          title="Heatmap fills as visitors dwell."
          hint="Once cameras attribute positions to zones, the densest spots glow brightest here."
        />
      </div>
    );
  }

  return (
    <div
      className="relative w-full rounded-lg overflow-hidden bg-bg-canvas map-grid border border-border-hairline"
      style={{ height }}
    >
      <svg
        viewBox={`0 0 ${grid.W * 10} ${grid.H * 10}`}
        className="absolute inset-0 w-full h-full"
        preserveAspectRatio="none"
      >
        {grid.cells.map((row, y) =>
          row.map((v, x) => {
            if (v <= 0) return null;
            const intensity = v / max;
            return (
              <circle
                key={`${x}_${y}`}
                cx={x * 10 + 5}
                cy={y * 10 + 5}
                r={3 + intensity * 14}
                fill="url(#heat)"
                opacity={0.25 + intensity * 0.6}
              />
            );
          })
        )}
        <defs>
          <radialGradient id="heat">
            <stop offset="0%" stopColor="#ff4d4d" stopOpacity="0.9" />
            <stop offset="35%" stopColor="#ffc83d" stopOpacity="0.65" />
            <stop offset="70%" stopColor="#42faa1" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#42faa1" stopOpacity="0" />
          </radialGradient>
        </defs>
      </svg>

      <div className="absolute top-2 left-2 text-[10px] tabular uppercase tracking-[0.18em] text-text-muted">
        attention density · session-to-date
      </div>
      <div className="absolute bottom-2 right-2 flex items-center gap-2 text-[10px] tabular text-text-muted">
        <div className="h-1.5 w-24 rounded-full bg-gradient-to-r from-accent via-accent-amber to-accent-red" />
        <span>cold → hot</span>
      </div>
    </div>
  );
}
