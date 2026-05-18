"use client";

import { Check } from "lucide-react";

import { type SessionTypeMeta } from "@/lib/session/presets";
import { cn } from "@/lib/utils";

interface Props {
  meta: SessionTypeMeta;
  selected: boolean;
  onSelect: () => void;
}

export function TypeCard({ meta, selected, onSelect }: Props) {
  const Icon = meta.icon;
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        "panel p-6 text-left flex flex-col gap-4 relative transition-all group min-h-[200px]",
        selected
          ? "ring-1 ring-accent/60 shadow-[var(--glow-green)] !border-accent/40"
          : "hover:border-border-strong"
      )}
    >
      {selected && (
        <span className="absolute top-4 right-4 w-6 h-6 rounded-full bg-accent text-text-inverse inline-flex items-center justify-center shadow-[var(--glow-green)]">
          <Check size={13} strokeWidth={3} />
        </span>
      )}

      <div
        className={cn(
          "w-12 h-12 rounded-2xl flex items-center justify-center transition-all",
          selected
            ? "bg-accent text-text-inverse"
            : "bg-bg-elevated border border-border-subtle text-accent group-hover:border-border-strong"
        )}
      >
        <Icon size={22} strokeWidth={1.8} />
      </div>

      <div>
        <h3 className="text-lg font-semibold tracking-tight">{meta.label}</h3>
        <p className="mt-1 text-sm text-text-secondary leading-relaxed">
          {meta.short}
        </p>
      </div>

      {meta.examples.length > 0 && (
        <div className="mt-auto flex flex-wrap gap-1.5 pt-2">
          {meta.examples.slice(0, 3).map((ex) => (
            <span
              key={ex}
              className="text-[10px] uppercase tracking-[0.12em] px-2 py-1 rounded-full bg-bg-elevated text-text-muted border border-border-hairline"
            >
              {ex}
            </span>
          ))}
        </div>
      )}
    </button>
  );
}
