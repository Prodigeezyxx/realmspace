import type { Prefab } from "./types";
import { BRAND_BASE, BRAND_BLUE, BRAND_DATA } from "@/lib/brand";

export const standardHall: Prefab = {
  id: "standard-hall",
  name: "Standard hall",
  description: "Rectangular venue with central open space and entry/exit",
  thumbnail: "data:image/svg+xml," + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 72"><rect width="120" height="72" fill="${BRAND_BASE}"/><rect x="8" y="8" width="104" height="56" fill="none" stroke="${BRAND_DATA}" stroke-width="2"/><rect x="45" y="20" width="30" height="32" fill="${BRAND_BLUE}33"/></svg>`
  ),
  boothSize: { width: 12, depth: 8 },
  zones: [
    {
      id: "entry",
      label: "Entry",
      polygon: [[0, 0], [0.2, 0], [0.2, 0.25], [0, 0.25]],
      color: "#3e83f7",
    },
    {
      id: "main",
      label: "Main floor",
      polygon: [[0.2, 0.15], [0.8, 0.15], [0.8, 0.85], [0.2, 0.85]],
      color: BRAND_DATA,
    },
    {
      id: "exit",
      label: "Exit",
      polygon: [[0.8, 0], [1, 0], [1, 0.25], [0.8, 0.25]],
      color: "#ffc83d",
    },
  ],
  walls: [
    { from: [0, 0], to: [1, 0] },
    { from: [1, 0], to: [1, 1] },
    { from: [1, 1], to: [0, 1] },
    { from: [0, 1], to: [0, 0] },
  ],
};
