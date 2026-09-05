/**
 * EmptyState — reusable "no data yet" indicator.
 *
 * Use the `panel` variant inside an existing Panel (replaces the chart /
 * list area). Use the `page` variant when an entire route doesn't apply
 * yet because the session hasn't recorded data — Twin / Ask / Report on
 * a fresh user-created session.
 *
 * Designed in the Intellias palette: subtle, breathable, never feels like
 * an error. The "fresh session" message is part of the product, not a
 * fallback.
 */

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface EmptyStateProps {
  variant?: "panel" | "page";
  icon?: ReactNode;
  title: string;
  hint?: ReactNode;
  cta?: { href: string; label: string };
  className?: string;
}

export function EmptyState({
  variant = "panel",
  icon,
  title,
  hint,
  cta,
  className,
}: EmptyStateProps) {
  if (variant === "page") {
    return (
      <div
        className={cn(
          "min-h-[60vh] flex flex-col items-center justify-center text-center px-6",
          className
        )}
      >
        {icon && (
          <div className="w-16 h-16 rounded-3xl bg-bg-elevated border border-border-subtle flex items-center justify-center text-accent mb-6 shadow-[var(--shadow-sm)]">
            {icon}
          </div>
        )}
        <h2 className="display-sm text-4xl md:text-5xl max-w-xl">{title}</h2>
        {hint && (
          <div className="mt-5 text-text-secondary text-base leading-relaxed max-w-xl">
            {hint}
          </div>
        )}
        {cta && (
          <Link
            href={cta.href}
            className="mt-8 inline-flex items-center gap-2 bg-accent-action text-text-inverse h-12 pl-5 pr-2.5 rounded-full font-semibold text-sm hover:bg-accent-action-bright transition-colors shadow-[var(--glow-action)]"
          >
            {cta.label}
            <span className="w-9 h-9 rounded-full bg-text-inverse text-accent inline-flex items-center justify-center">
              <ArrowUpRight size={15} />
            </span>
          </Link>
        )}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center gap-3 py-10 px-6",
        className
      )}
    >
      {icon && (
        <div className="w-10 h-10 rounded-2xl bg-bg-elevated border border-border-subtle flex items-center justify-center text-text-muted">
          {icon}
        </div>
      )}
      <div className="text-sm text-text-primary font-medium max-w-xs">
        {title}
      </div>
      {hint && (
        <div className="text-xs text-text-muted max-w-sm leading-relaxed">
          {hint}
        </div>
      )}
      {cta && (
        <Link
          href={cta.href}
          className="mt-1 text-xs text-accent inline-flex items-center gap-1.5 hover:gap-2.5 transition-all font-medium"
        >
          {cta.label}
          <ArrowUpRight size={12} />
        </Link>
      )}
    </div>
  );
}
