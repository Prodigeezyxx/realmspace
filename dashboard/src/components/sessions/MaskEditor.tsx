"use client";

/**
 * The opt-out polygon of `privacy.md`, drawn.
 *
 * Click to place a point, click the first point to close, drag a point to move
 * it. Coordinates are normalized 0..1 — the same space a zone polygon lives in,
 * and the space `perception/mask.py` multiplies back up by the real frame size.
 *
 * ## Drawn on an empty grid, and that is a limitation not a style
 *
 * There is no camera preview behind this. Frames sit in a 60-second RAM ring
 * buffer on the perception laptop and never leave it (`privacy.md` → "Where
 * data lives"); no endpoint serves one, and adding one is a decision about the
 * privacy posture rather than a convenience for this editor. So an operator is
 * placing a shape against remembered geometry, which is harder than tracing
 * over a still, and the screen says so instead of implying otherwise.
 */

import { useCallback, useRef, useState } from "react";

import type { Point } from "@/lib/ops/useCalibration";

interface Props {
  polygon: Point[];
  onChange: (next: Polygon) => void;
  /** 16:9 by default — what most fixed booth cameras produce. */
  aspect?: number;
}

type Polygon = Point[];

/** Clicking within this normalized distance of the first point closes the shape. */
const CLOSE_RADIUS = 0.04;

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

export function MaskEditor({ polygon, onChange, aspect = 9 / 16 }: Props) {
  const svg = useRef<SVGSVGElement>(null);
  const [dragging, setDragging] = useState<number | null>(null);

  const at = useCallback((event: { clientX: number; clientY: number }): Point => {
    const box = svg.current?.getBoundingClientRect();
    if (!box) return [0, 0];
    return [
      clamp01((event.clientX - box.left) / box.width),
      clamp01((event.clientY - box.top) / box.height),
    ];
  }, []);

  function place(event: React.MouseEvent) {
    if (dragging !== null) return;
    const point = at(event);

    // Clicking the first point closes the shape rather than adding a duplicate
    // vertex on top of it — which is what a naive editor does, and it produces
    // a polygon whose first and last points are the same, which every
    // ray-casting implementation counts as a zero-length edge.
    if (polygon.length >= 3) {
      const [fx, fy] = polygon[0];
      if (Math.hypot(point[0] - fx, point[1] - fy) < CLOSE_RADIUS) return;
    }
    onChange([...polygon, point]);
  }

  function move(index: number, event: React.MouseEvent) {
    if (dragging !== index) return;
    const next = [...polygon];
    next[index] = at(event);
    onChange(next);
  }

  const points = polygon.map(([x, y]) => `${x * 100},${y * 100}`).join(" ");
  const closed = polygon.length >= 3;

  return (
    <div className="space-y-3">
      <svg
        ref={svg}
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        style={{ aspectRatio: String(1 / aspect) }}
        className="w-full rounded-lg border border-border-subtle bg-bg-canvas cursor-crosshair select-none"
        onClick={place}
        onMouseMove={(e) => dragging !== null && move(dragging, e)}
        onMouseUp={() => setDragging(null)}
        onMouseLeave={() => setDragging(null)}
      >
        {/* A grid, because there is nothing else to place the shape against.
            Thirds rather than a fine mesh: it is a landmark, not a ruler. */}
        {[100 / 3, 200 / 3].map((offset) => (
          <g key={offset} stroke="currentColor" className="text-border-hairline" strokeWidth={0.2}>
            <line x1={offset} y1={0} x2={offset} y2={100} />
            <line x1={0} y1={offset} x2={100} y2={offset} />
          </g>
        ))}

        {closed && (
          <polygon
            points={points}
            className="fill-accent-red/25 stroke-accent-red"
            strokeWidth={0.4}
          />
        )}
        {!closed && polygon.length > 1 && (
          <polyline
            points={points}
            fill="none"
            className="stroke-accent-red"
            strokeWidth={0.4}
          />
        )}

        {polygon.map(([x, y], index) => (
          <circle
            key={index}
            cx={x * 100}
            cy={y * 100}
            r={1.4}
            className="fill-accent-red stroke-bg-canvas cursor-grab"
            strokeWidth={0.4}
            onMouseDown={(e) => {
              e.stopPropagation();
              setDragging(index);
            }}
          />
        ))}
      </svg>

      <div className="flex items-center justify-between gap-3 flex-wrap text-xs text-text-muted">
        <span>
          {polygon.length === 0
            ? "Click to place the first corner."
            : polygon.length < 3
              ? `${polygon.length} of at least 3 corners. A shape with fewer contains nothing.`
              : `${polygon.length} corners. Drag one to adjust.`}
        </span>
        {polygon.length > 0 && (
          <button
            type="button"
            className="underline hover:text-text-secondary"
            onClick={() => onChange(polygon.slice(0, -1))}
          >
            Undo last corner
          </button>
        )}
      </div>
    </div>
  );
}
