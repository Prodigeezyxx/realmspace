import type { Prefab } from "./types";

export const openPlan: Prefab = {
  id: "open-plan",
  name: "Open plan",
  description: "Floor plane with perimeter walls only",
  thumbnail: "data:image/svg+xml," + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 72"><rect width="120" height="72" fill="#0a0c10"/><rect x="15" y="12" width="90" height="48" fill="#ffffff08" stroke="#888"/></svg>`
  ),
  boothSize: { width: 10, depth: 6 },
  zones: [
    {
      id: "floor",
      label: "Open floor",
      polygon: [[0.05, 0.05], [0.95, 0.05], [0.95, 0.95], [0.05, 0.95]],
      color: "#5856d6",
    },
  ],
  walls: [
    { from: [0, 0], to: [1, 0] },
    { from: [1, 0], to: [1, 1] },
    { from: [1, 1], to: [0, 1] },
    { from: [0, 1], to: [0, 0] },
  ],
};
