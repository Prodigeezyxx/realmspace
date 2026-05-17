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
  live: "bg-accent-cyan/10 text-accent-cyan border-accent-cyan/30",
  alert: "bg-accent-red/10 text-accent-red border-accent-red/30",
  info: "bg-accent-blue/10 text-accent-blue border-accent-blue/30",
  neutral: "bg-bg-elevated text-text-secondary border-border-subtle",
  success: "bg-accent-green/10 text-accent-green border-accent-green/30",
  warn: "bg-accent-amber/10 text-accent-amber border-accent-amber/30",
  violet: "bg-accent-violet/10 text-accent-violet border-accent-violet/30",
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
