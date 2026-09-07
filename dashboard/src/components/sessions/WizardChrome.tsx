"use client";

import { Check } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

// ── Top header — pill nav + step indicator dots ──────────────────────────

export function WizardHeader({
  total,
  current,
  cancelHref = "/sessions",
}: {
  total: number;
  current: number;
  cancelHref?: string;
}) {
  return (
    <header className="sticky top-0 z-30 px-4 md:px-5 py-3 bg-bg-base/92 backdrop-blur-xl border-b border-border-hairline">
      <div className="max-w-[1400px] mx-auto flex items-center justify-between gap-4">
        <Link
          href="/"
          className="h-12 rounded-[18px] px-2 md:px-3 inline-flex items-center gap-2 hover:bg-bg-elevated transition-colors"
        >
          <span className="brand-mark" aria-hidden="true" />
          <span className="text-sm font-bold tracking-[-.02em]">realmspace</span>
        </Link>

        <div className="hidden md:flex items-center gap-2">
          {Array.from({ length: total }).map((_, i) => {
            const idx = i + 1;
            const done = idx < current;
            const active = idx === current;
            return (
              <div key={i} className="flex items-center gap-2">
                <div
                  className={cn(
                    "h-9 rounded-full inline-flex items-center justify-center font-semibold text-[12px] transition-all border",
                    active
                      ? "w-9 bg-accent text-text-inverse border-accent shadow-[var(--glow-green)]"
                      : done
                        ? "w-9 bg-bg-raised text-accent border-accent/40"
                        : "w-9 bg-bg-raised text-text-muted border-border-subtle"
                  )}
                >
                  {done ? <Check size={13} strokeWidth={3} /> : idx}
                </div>
                {i < total - 1 && (
                  <div
                    className={cn(
                      "w-6 h-px transition-colors",
                      done ? "bg-accent/40" : "bg-border-subtle"
                    )}
                  />
                )}
              </div>
            );
          })}
        </div>

        <Link
          href={cancelHref}
          className="text-sm text-text-muted hover:text-text-primary transition-colors h-11 px-4 inline-flex items-center"
        >
          Cancel
        </Link>
      </div>
    </header>
  );
}

// ── Step layout wrapper ──────────────────────────────────────────────────

export function WizardStep({
  step,
  total,
  eyebrow,
  title,
  ghost,
  subtitle,
  children,
}: {
  step: number;
  total: number;
  eyebrow: string;
  title: ReactNode;
  ghost?: string;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="max-w-[1400px] mx-auto px-4 md:px-8 pt-10 pb-32">
      <div className="flex items-center gap-3 text-text-muted text-xs uppercase tracking-[0.18em] font-medium">
        <span>
          Step {String(step).padStart(2, "0")} / {String(total).padStart(2, "0")}
        </span>
        <span className="w-1 h-1 rounded-full bg-border-strong" />
        <span>{eyebrow}</span>
      </div>

      <h1 className="display-small mt-5 max-w-4xl">
        {ghost ? (
          <span className="block">
            <span className="ghost-text" data-text={ghost}>
              {title}
            </span>
          </span>
        ) : (
          <span className="block">{title}</span>
        )}
      </h1>

      {subtitle && (
        <p className="mt-6 text-text-secondary text-lg leading-relaxed max-w-3xl">
          {subtitle}
        </p>
      )}

      <div className="mt-10">{children}</div>
    </section>
  );
}

// ── Bottom action bar ────────────────────────────────────────────────────

export function WizardFooter({
  back,
  next,
  finish,
  nextDisabled,
  nextLabel = "Continue",
  finishLabel = "Launch session",
}: {
  back?: () => void;
  next?: () => void;
  finish?: () => void;
  nextDisabled?: boolean;
  nextLabel?: string;
  finishLabel?: string;
}) {
  return (
    <div className="fixed bottom-0 left-0 right-0 z-30 bg-gradient-to-t from-bg-base via-bg-base to-transparent pt-6 pb-6 px-5">
      <div className="max-w-[1400px] mx-auto flex items-center justify-between">
        <div>
          {back && (
            <button
              onClick={back}
              className="bg-bg-raised border border-border-subtle h-12 rounded-full px-5 inline-flex items-center gap-2 text-sm font-medium text-text-secondary hover:text-text-primary hover:border-border-strong transition-colors"
            >
              ← Back
            </button>
          )}
        </div>
        <div>
          {finish ? (
            <button
              onClick={finish}
              disabled={nextDisabled}
              className="inline-flex items-center gap-2 bg-accent text-text-inverse h-12 pl-6 pr-2.5 rounded-full font-semibold text-sm hover:bg-accent-bright transition-colors shadow-[var(--glow-green)] disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {finishLabel}
              <span className="material-symbol material-symbol-sm">arrow_outward</span>
            </button>
          ) : (
            <button
              onClick={next}
              disabled={nextDisabled}
              className="inline-flex items-center gap-2 bg-accent text-text-inverse h-12 pl-6 pr-2.5 rounded-full font-semibold text-sm hover:bg-accent-bright transition-colors shadow-[var(--glow-green)] disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {nextLabel}
              <span className="material-symbol material-symbol-sm">arrow_forward</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Form primitives ──────────────────────────────────────────────────────

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("flex flex-col gap-2", className)}>
      <span className="text-[11px] uppercase tracking-[0.16em] text-text-secondary font-medium">
        {label}
      </span>
      {children}
      {hint && <span className="text-xs text-text-muted">{hint}</span>}
    </label>
  );
}

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cn(
        "h-12 px-4 rounded-xl bg-bg-panel border border-border-subtle text-text-primary placeholder:text-text-muted text-sm focus:border-accent focus:outline-none transition-colors",
        props.className
      )}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cn(
        "h-12 px-4 rounded-xl bg-bg-panel border border-border-subtle text-text-primary text-sm focus:border-accent focus:outline-none transition-colors appearance-none cursor-pointer",
        props.className
      )}
    />
  );
}

export function NumberInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      type="number"
      inputMode="numeric"
      className={cn(
        "h-12 px-4 rounded-xl bg-bg-panel border border-border-subtle text-text-primary placeholder:text-text-muted text-sm tabular focus:border-accent focus:outline-none transition-colors",
        props.className
      )}
    />
  );
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cn(
        "min-h-[110px] p-4 rounded-xl bg-bg-panel border border-border-subtle text-text-primary placeholder:text-text-muted text-sm focus:border-accent focus:outline-none transition-colors resize-y",
        props.className
      )}
    />
  );
}
