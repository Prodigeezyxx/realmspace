import { cn } from "@/lib/utils";
import { ReactNode } from "react";

interface PillProps {
  children: ReactNode;
  variant?:
    | "live"
    | "alert"
    | "info"
    | "neutral"
    | "success"
    | "warn"
    | "violet";
  className?: string;
}

const variants: Record<NonNullable<PillProps["variant"]>, string> = {
  live: "bg-accent-red/8 text-accent-red border-accent-red/25",
  alert: "bg-accent-red/10 text-accent-red border-accent-red/30",
  info: "bg-accent-blue/8 text-accent-blue border-accent-blue/25",
  neutral: "bg-bg-elevated text-text-secondary border-border-subtle",
  success: "bg-accent-green/8 text-accent-green border-accent-green/25",
  warn: "bg-accent-amber/8 text-accent-amber border-accent-amber/25",
  violet: "bg-accent-violet/8 text-accent-violet border-accent-violet/25",
};

export function Pill({ children, variant = "neutral", className }: PillProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[10px] uppercase tracking-[0.16em] font-medium",
        variants[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
