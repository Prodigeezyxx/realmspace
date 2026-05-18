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
    | "violet"
    | "outline";
  className?: string;
}

const variants: Record<NonNullable<PillProps["variant"]>, string> = {
  live: "bg-accent-red/10 text-accent-red border-accent-red/30",
  alert: "bg-accent-red/10 text-accent-red border-accent-red/30",
  info: "bg-accent-blue/10 text-accent-blue border-accent-blue/25",
  neutral: "bg-bg-raised text-text-secondary border-border-subtle",
  success: "bg-accent/8 text-accent border-accent/30",
  warn: "bg-accent-amber/10 text-accent-amber border-accent-amber/25",
  violet: "bg-accent-violet/10 text-accent-violet border-accent-violet/25",
  // Intellias-style: thin white outline on dark, white text — used for category tags
  outline: "bg-transparent text-text-primary border-border-strong",
};

export function Pill({ children, variant = "neutral", className }: PillProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border text-[11px] uppercase tracking-[0.14em] font-medium",
        variants[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
