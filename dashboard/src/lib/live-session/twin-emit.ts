import { getEventContext } from "@/lib/event-context";
import { busEmit, TWIN_UPDATE_CHANNEL } from "@/lib/event-bus";
import type { HeatmapOutput } from "@/skills/heatmap";
import type { TwinAvatarDelta } from "@/skills/twin-sync";
import type { Track } from "@/lib/tracker";

const SENSOR_W = 640;
const SENSOR_H = 480;

export function tracksToAvatars(tracks: Track[]): TwinAvatarDelta[] {
  const ctx = getEventContext();
  const { width, depth } = ctx.boothSize;
  return tracks
    .filter((t) => t.missCount === 0)
    .map((t) => ({
      personId: t.id,
      label: t.label,
      x: (t.cx / SENSOR_W - 0.5) * width,
      z: (t.cy / SENSOR_H - 0.5) * depth,
    }));
}

export function emitTwinFromTracks(tracks: Track[]) {
  busEmit(TWIN_UPDATE_CHANNEL, { avatars: tracksToAvatars(tracks) });
}

export function buildHeatmapFromTracks(tracks: Track[]): HeatmapOutput {
  const w = 24;
  const h = 16;
  const grid = Array.from({ length: h }, () => Array(w).fill(0));
  for (const t of tracks) {
    if (t.missCount > 0) continue;
    const gx = Math.min(w - 1, Math.max(0, Math.floor((t.cx / SENSOR_W) * w)));
    const gy = Math.min(h - 1, Math.max(0, Math.floor((t.cy / SENSOR_H) * h)));
    grid[gy][gx] = Math.min(1, grid[gy][gx] + 0.35);
  }
  let peak = { x: 0, y: 0, value: 0 };
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (grid[y][x] > peak.value) peak = { x, y, value: grid[y][x] };
    }
  }
  return { width: w, height: h, grid, peak };
}

export { SENSOR_W, SENSOR_H };
