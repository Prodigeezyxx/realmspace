import type { Prefab } from "./types";

export const theatre: Prefab = {
  id: "theatre",
  name: "Theatre",
  description: "Rows of seats facing a stage",
  thumbnail: "data:image/svg+xml," + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 72"><rect width="120" height="72" fill="#0a0c10"/><rect x="30" y="8" width="60" height="14" fill="#ff2d9233" stroke="#ff2d92"/><g fill="#34c75933">${[0,1,2,3].map(r=>`<rect x="20" y="${28+r*10}" width="80" height="6"/>`).join("")}</g></svg>`
  ),
  boothSize: { width: 12, depth: 10 },
  zones: [
    {
      id: "stage",
      label: "Stage",
      polygon: [[0.25, 0.05], [0.75, 0.05], [0.75, 0.22], [0.25, 0.22]],
      color: "#ff2d92",
    },
    {
      id: "seating",
      label: "Seating",
      polygon: [[0.15, 0.28], [0.85, 0.28], [0.85, 0.92], [0.15, 0.92]],
      color: "#34c759",
    },
  ],
  walls: [
    { from: [0, 0], to: [1, 0] },
    { from: [1, 0], to: [1, 1] },
    { from: [1, 1], to: [0, 1] },
    { from: [0, 1], to: [0, 0] },
  ],
};
