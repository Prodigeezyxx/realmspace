import type { Prefab } from "./types";

const booths: Prefab["zones"] = [];
for (let row = 0; row < 3; row++) {
  for (let col = 0; col < 4; col++) {
    const x0 = 0.1 + col * 0.2;
    const y0 = 0.15 + row * 0.22;
    booths.push({
      id: `booth_${row}_${col}`,
      label: `Booth ${row * 4 + col + 1}`,
      polygon: [
        [x0, y0],
        [x0 + 0.16, y0],
        [x0 + 0.16, y0 + 0.18],
        [x0, y0 + 0.18],
      ],
      color: "#4a9eff",
    });
  }
}

export const exhibitionGrid: Prefab = {
  id: "exhibition-grid",
  name: "Exhibition grid",
  description: "3×4 booth grid with aisles",
  thumbnail: "data:image/svg+xml," + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 72"><rect width="120" height="72" fill="#0a0c10"/><g fill="#4a9eff33" stroke="#4a9eff">${Array.from({length:12}).map((_,i)=>{const c=i%4,r=Math.floor(i/4);return `<rect x="${12+c*24}" y="${10+r*18}" width="18" height="14"/>`}).join("")}</g></svg>`
  ),
  boothSize: { width: 14, depth: 10 },
  zones: booths,
  walls: [
    { from: [0, 0], to: [1, 0] },
    { from: [1, 0], to: [1, 1] },
    { from: [1, 1], to: [0, 1] },
    { from: [0, 1], to: [0, 0] },
  ],
};
