"use client";

import { cn } from "@/lib/utils";

interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  stroke?: string;
  fill?: string;
  className?: string;
  showLast?: boolean;
}

export function Sparkline({
  data,
  width = 120,
  height = 32,
  stroke = "var(--accent-blue)",
  fill,
  className,
  showLast,
}: SparklineProps) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const stepX = width / (data.length - 1);

  const points = data.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return [x, y] as const;
  });

  const path = points
    .map(([x, y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`))
    .join(" ");

  const area = `${path} L${width},${height} L0,${height} Z`;

  const [lx, ly] = points[points.length - 1];

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("block", className)}
    >
      {fill && <path d={area} fill={fill} />}
      <path
        d={path}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {showLast && (
        <circle cx={lx} cy={ly} r={2.5} fill={stroke}>
          <animate
            attributeName="r"
            values="2.5;4;2.5"
            dur="1.6s"
            repeatCount="indefinite"
          />
        </circle>
      )}
    </svg>
  );
}
