"use client";

/**
 * Mocked "live video" feed for the dashboard.
 *
 * In production this is an MJPEG / WebRTC stream from the Perception Engine
 * with YOLOv8 detections drawn server-side.
 *
 * Here we render a stylised representation of the booth from above with
 * animated avatars + bounding boxes — convincing enough for the demo
 * without recording video that would compromise privacy.
 */

import { useEffect, useState } from "react";

import { positionsAt, peopleTracks } from "@/lib/mock/people";
import { zones } from "@/lib/mock/session";

export function LiveVideo() {
  const [t, setT] = useState(280);

  useEffect(() => {
    const id = setInterval(() => {
      setT((cur) => (cur > 600 ? 30 : cur + 1));
    }, 100);
    return () => clearInterval(id);
  }, []);

  const positions = positionsAt(t);

  return (
    <div className="relative w-full h-full overflow-hidden scanline rounded-xl bg-gradient-to-b from-[#0d1416] to-[#080a0c]">
      {/* Faux camera perspective grid */}
      <svg
        viewBox="0 0 1000 600"
        className="absolute inset-0 w-full h-full"
        preserveAspectRatio="xMidYMid slice"
      >
        <defs>
          <linearGradient id="floor" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor="#1a2026" />
            <stop offset="1" stopColor="#070a0c" />
          </linearGradient>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1a2530" strokeWidth="0.5" />
          </pattern>
        </defs>
        {/* Floor perspective */}
        <polygon points="0,600 1000,600 800,180 200,180" fill="url(#floor)" />
        <polygon points="0,600 1000,600 800,180 200,180" fill="url(#grid)" opacity="0.5" />

        {/* Zone polygons projected on the floor */}
        {zones.map((z) => {
          // Convert normalized booth coords to a rough perspective trapezoid mapping
          const project = ([nx, ny]: [number, number]) => {
            // top-y at 180, bottom-y at 600; top-width 600, bottom-width 1000
            const y = 180 + ny * 420;
            const widthAt = 600 + ny * 400;
            const xCenter = 500;
            const x = xCenter + (nx - 0.5) * widthAt;
            return `${x},${y}`;
          };
          const pts = z.polygon.map(project).join(" ");
          return (
            <g key={z.id}>
              <polygon
                points={pts}
                fill={z.color}
                fillOpacity="0.06"
                stroke={z.color}
                strokeOpacity="0.5"
                strokeWidth="1.5"
                strokeDasharray="4 4"
              />
              {(() => {
                // Label at centroid
                const cx =
                  z.polygon.reduce((s, [x]) => s + x, 0) / z.polygon.length;
                const cy =
                  z.polygon.reduce((s, [, y]) => s + y, 0) / z.polygon.length;
                const [lx, ly] = project([cx, cy]).split(",");
                return (
                  <text
                    x={lx}
                    y={ly}
                    fill={z.color}
                    fontSize="11"
                    fontFamily="ui-monospace, monospace"
                    textAnchor="middle"
                    opacity="0.85"
                  >
                    {z.name.toUpperCase()}
                  </text>
                );
              })()}
            </g>
          );
        })}

        {/* Tracked persons with bounding boxes */}
        {positions.map((p) => {
          const project = (nx: number, ny: number) => {
            const y = 180 + ny * 420;
            const widthAt = 600 + ny * 400;
            const xCenter = 500;
            const x = xCenter + (nx - 0.5) * widthAt;
            return [x, y];
          };
          const [x, y] = project(p.x, p.y);
          const scale = 0.5 + p.y * 0.9;
          const w = 32 * scale;
          const h = 70 * scale;
          return (
            <g key={p.id}>
              {/* Shadow */}
              <ellipse
                cx={x}
                cy={y + h / 2}
                rx={w * 0.6}
                ry={4 * scale}
                fill="#000"
                opacity="0.5"
              />
              {/* Avatar (simple humanoid silhouette) */}
              <rect
                x={x - w / 4}
                y={y - h / 2}
                width={w / 2}
                height={h * 0.35}
                rx={w / 4}
                fill={p.color}
                opacity="0.85"
              />
              <circle
                cx={x}
                cy={y - h / 2 - w / 4}
                r={w / 4}
                fill={p.color}
                opacity="0.95"
              />
              <rect
                x={x - w / 5}
                y={y - h / 12}
                width={w / 2.5}
                height={h * 0.4}
                rx={w / 8}
                fill={p.color}
                opacity="0.65"
              />
              {/* Bounding box */}
              <rect
                x={x - w / 2}
                y={y - h / 2 - w / 2}
                width={w}
                height={h + w / 2}
                fill="none"
                stroke={p.color}
                strokeWidth="1"
                opacity="0.9"
              />
              {/* ID tag */}
              <rect
                x={x - w / 2}
                y={y - h / 2 - w / 2 - 14}
                width={Math.max(36, p.id.length * 6)}
                height={12}
                fill={p.color}
                rx="2"
              />
              <text
                x={x - w / 2 + 4}
                y={y - h / 2 - w / 2 - 4}
                fill="#000"
                fontSize="9"
                fontWeight="700"
                fontFamily="ui-monospace, monospace"
              >
                {p.id}
              </text>
            </g>
          );
        })}
      </svg>

      {/* HUD overlay */}
      <div className="absolute top-3 left-3 flex items-center gap-2">
        <span className="alert-dot" />
        <span className="text-[10px] tabular tracking-[0.18em] uppercase text-text-secondary">
          REC · CAM_01
        </span>
      </div>
      <div className="absolute top-3 right-3 text-[10px] tabular text-text-secondary">
        1920×1080 · 22 fps · YOLOv8 + ByteTrack
      </div>
      <div className="absolute bottom-3 left-3 text-[10px] tabular text-text-secondary">
        T+{Math.floor(t / 60)}:{String(t % 60).padStart(2, "0")} · tracking {positions.length} subject{positions.length === 1 ? "" : "s"}
      </div>
      <div className="absolute bottom-3 right-3 text-[10px] tabular text-text-secondary">
        anonymised · no faces stored
      </div>

      {/* Corner crosshairs */}
      <Crosshair className="top-2 left-2" />
      <Crosshair className="top-2 right-2 scale-x-[-1]" />
      <Crosshair className="bottom-2 left-2 scale-y-[-1]" />
      <Crosshair className="bottom-2 right-2 scale-x-[-1] scale-y-[-1]" />

      <div className="sr-only">
        Total tracked across session: {peopleTracks.length}
      </div>
    </div>
  );
}

function Crosshair({ className }: { className?: string }) {
  return (
    <svg
      className={`absolute w-4 h-4 text-accent-cyan/60 ${className ?? ""}`}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
    >
      <path d="M0 1 H6" />
      <path d="M1 0 V6" />
    </svg>
  );
}
