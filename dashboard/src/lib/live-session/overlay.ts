import type { Track } from "@/lib/tracker";

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Draw tracking boxes scaled from sensor resolution to canvas display size */
export function drawTrackOverlay(
  canvas: HTMLCanvasElement,
  tracks: Track[],
  sourceWidth: number,
  sourceHeight: number
) {
  const ctx = canvas.getContext("2d");
  if (!ctx || sourceWidth <= 0 || sourceHeight <= 0) return;

  const rect = canvas.getBoundingClientRect();
  const dw = rect.width || canvas.clientWidth;
  const dh = rect.height || canvas.clientHeight;
  if (dw <= 0 || dh <= 0) return;

  canvas.width = dw;
  canvas.height = dh;
  const sx = dw / sourceWidth;
  const sy = dh / sourceHeight;

  ctx.clearRect(0, 0, dw, dh);

  for (const t of tracks) {
    if (t.missCount > 0) continue;
    const x = t.bbox[0] * sx;
    const y = t.bbox[1] * sy;
    const w = t.bbox[2] * sx;
    const h = t.bbox[3] * sy;
    const color = t.color;

    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.shadowColor = color;
    ctx.shadowBlur = 8;
    ctx.strokeRect(x, y, w, h);
    ctx.shadowBlur = 0;

    const bracket = Math.min(18, w * 0.18, h * 0.18);
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(x, y + bracket);
    ctx.lineTo(x, y);
    ctx.lineTo(x + bracket, y);
    ctx.moveTo(x + w - bracket, y);
    ctx.lineTo(x + w, y);
    ctx.lineTo(x + w, y + bracket);
    ctx.moveTo(x + w, y + h - bracket);
    ctx.lineTo(x + w, y + h);
    ctx.lineTo(x + w - bracket, y + h);
    ctx.moveTo(x + bracket, y + h);
    ctx.lineTo(x, y + h);
    ctx.lineTo(x, y + h - bracket);
    ctx.stroke();

    const label = `${t.label} · ${Math.round(t.score * 100)}%`;
    ctx.font = "600 13px ui-monospace, monospace";
    const metrics = ctx.measureText(label);
    const padX = 6;
    const tagW = metrics.width + padX * 2;
    const tagH = 18;
    const tagX = x;
    const tagY = Math.max(0, y - tagH - 2);

    ctx.fillStyle = color;
    roundRect(ctx, tagX, tagY, tagW, tagH, 3);
    ctx.fill();

    ctx.save();
    ctx.translate(tagX + tagW / 2, tagY + tagH / 2);
    ctx.scale(-1, 1);
    ctx.fillStyle = "#ffffff";
    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    ctx.fillText(label, 0, 1);
    ctx.restore();
  }
}
