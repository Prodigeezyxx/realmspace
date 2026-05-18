import { getEventContext } from "@/lib/event-context";
import { busEmit, TWIN_UPDATE_CHANNEL } from "@/lib/event-bus";
import type { HeatmapOutput } from "@/skills/heatmap";
import type { TwinAvatarDelta } from "@/skills/twin-sync";
import type { Track } from "@/lib/tracker";

/** Mirror sensor X so twin matches the mirrored live feed. */
function sensorXToBooth(
  cx: number,
  frameW: number,
  boothW: number
): number {
  return (0.5 - cx / frameW) * boothW;
}

function sensorYToBooth(
  cy: number,
  frameH: number,
  boothD: number
): number {
  return (cy / frameH - 0.5) * boothD;
}

export function tracksToAvatars(
  tracks: Track[],
  frameW: number,
  frameH: number
): TwinAvatarDelta[] {
  const ctx = getEventContext();
  const { width, depth } = ctx.boothSize;
  const w = frameW > 0 ? frameW : 640;
  const h = frameH > 0 ? frameH : 480;

  return tracks
    .filter((t) => t.missCount === 0)
    .map((t) => ({
      personId: t.id,
      label: t.label,
      x: sensorXToBooth(t.cx, w, width),
      z: sensorYToBooth(t.cy, h, depth),
    }));
}

export function emitTwinFromTracks(
  tracks: Track[],
  frameW: number,
  frameH: number
) {
  busEmit(TWIN_UPDATE_CHANNEL, {
    avatars: tracksToAvatars(tracks, frameW, frameH),
  });
}

export function buildHeatmapFromTracks(
  tracks: Track[],
  frameW: number,
  frameH: number
): HeatmapOutput {
  const w = 24;
  const h = 16;
  const fw = frameW > 0 ? frameW : 640;
  const fh = frameH > 0 ? frameH : 480;
  const grid = Array.from({ length: h }, () => Array(w).fill(0));

  for (const t of tracks) {
    if (t.missCount > 0) continue;
    const nx = 1 - t.cx / fw;
    const ny = t.cy / fh;
    const gx = Math.min(w - 1, Math.max(0, Math.floor(nx * w)));
    const gy = Math.min(h - 1, Math.max(0, Math.floor(ny * h)));
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
