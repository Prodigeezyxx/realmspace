import { cn } from "@/lib/utils";
import { ReactNode } from "react";

interface StatProps {
  label: string;
  value: ReactNode;
  unit?: string;
  delta?: { value: string; direction: "up" | "down" | "flat" };
  size?: "sm" | "md" | "lg" | "xl";
  accent?:
    | "brand"
    | "blue"
    | "cyan"
    | "green"
    | "red"
    | "amber"
    | "violet"
    | "white";
  className?: string;
  hint?: string;
}

const sizeMap = {
  sm: "text-2xl",
  md: "text-3xl",
  lg: "text-5xl",
  xl: "text-7xl",
};

const accentMap = {
  brand: "text-accent",
  blue: "text-accent-blue",
  cyan: "text-accent-cyan",
  green: "text-accent",
  red: "text-accent-red",
  amber: "text-accent-amber",
  violet: "text-accent-violet",
  white: "text-text-primary",
};

export function Stat({
  label,
  value,
  unit,
  delta,
  size = "lg",
  accent = "white",
  hint,
  className,
}: StatProps) {
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div className="text-[11px] uppercase tracking-[0.18em] text-text-secondary">
        {label}
      </div>
      <div className="flex items-baseline gap-2 tabular">
        <span
          className={cn(
            "font-semibold leading-none tracking-tight",
            sizeMap[size],
            accentMap[accent]
          )}
        >
          {value}
        </span>
        {unit && (
          <span className="text-sm text-text-muted font-medium">{unit}</span>
        )}
      </div>
      {(delta || hint) && (
        <div className="flex items-center gap-2 text-xs">
          {delta && (
            <span
              className={cn(
                "inline-flex items-center gap-1 font-medium tabular",
                delta.direction === "up" && "text-accent",
                delta.direction === "down" && "text-accent-red",
                delta.direction === "flat" && "text-text-muted"
              )}
            >
              {delta.direction === "up" && "▲"}
              {delta.direction === "down" && "▼"}
              {delta.direction === "flat" && "•"}
              {delta.value}
            </span>
          )}
          {hint && <span className="text-text-muted">{hint}</span>}
        </div>
      )}
    </div>
  );
}
