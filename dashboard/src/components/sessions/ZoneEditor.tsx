"use client";

import { Lock, Plus, Trash2 } from "lucide-react";

import { ZONE_TYPE_OPTIONS } from "@/lib/session/presets";
import type { Zone, ZoneType } from "@/lib/session/types";
import { cn } from "@/lib/utils";

interface Props {
  zones: Zone[];
  onChange: (next: Zone[]) => void;
}

function uid() {
  return `zone_${Math.random().toString(36).slice(2, 9)}`;
}

function colorFor(type: ZoneType): string {
  return ZONE_TYPE_OPTIONS.find((o) => o.value === type)?.color ?? "#a8a8ad";
}

export function ZoneEditor({ zones, onChange }: Props) {
  function update(id: string, patch: Partial<Zone>) {
    onChange(
      zones.map((z) =>
        z.id === id
          ? {
              ...z,
              ...patch,
              color:
                patch.type && patch.type !== z.type
                  ? colorFor(patch.type)
                  : z.color,
            }
          : z
      )
    );
  }
  function remove(id: string) {
    onChange(zones.filter((z) => z.id !== id));
  }
  function add() {
    const z: Zone = {
      id: uid(),
      name: `Zone ${zones.length + 1}`,
      type: "engagement",
      capacity: 15,
      color: colorFor("engagement"),
    };
    onChange([...zones, z]);
  }

  return (
    <div className="space-y-3">
      {/* Header row (column labels) */}
      <div className="hidden md:grid grid-cols-[16px_2fr_1.4fr_120px_120px_44px] gap-3 px-4 text-[10px] uppercase tracking-[0.16em] text-text-muted font-medium">
        <span />
        <span>Zone name</span>
        <span>Type</span>
        <span className="tabular">Capacity</span>
        <span>Privacy</span>
        <span />
      </div>

      <ul className="space-y-2">
        {zones.map((z) => (
          <li
            key={z.id}
            className={cn(
              "panel grid grid-cols-1 md:grid-cols-[16px_2fr_1.4fr_120px_120px_44px] gap-3 items-center p-4",
              z.privacyMasked && "ring-1 ring-border-strong"
            )}
          >
            <span
              className="w-3 h-3 rounded-full shrink-0 hidden md:block"
              style={{ background: z.color ?? "#a8a8ad" }}
              aria-hidden
            />
            <input
              value={z.name}
              onChange={(e) => update(z.id, { name: e.target.value })}
              placeholder="Zone name"
              className="h-10 px-3 rounded-lg bg-bg-canvas border border-border-subtle text-sm focus:border-accent focus:outline-none transition-colors"
            />
            <select
              value={z.type}
              onChange={(e) => update(z.id, { type: e.target.value as ZoneType })}
              className="h-10 px-3 rounded-lg bg-bg-canvas border border-border-subtle text-sm focus:border-accent focus:outline-none transition-colors appearance-none cursor-pointer"
            >
              {ZONE_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <input
              type="number"
              min={1}
              value={z.capacity ?? ""}
              onChange={(e) =>
                update(z.id, {
                  capacity: e.target.value ? parseInt(e.target.value, 10) : undefined,
                })
              }
              placeholder="—"
              className="h-10 px-3 rounded-lg bg-bg-canvas border border-border-subtle text-sm tabular focus:border-accent focus:outline-none transition-colors"
            />
            <button
              type="button"
              onClick={() => update(z.id, { privacyMasked: !z.privacyMasked })}
              className={cn(
                "h-10 px-3 rounded-lg text-xs inline-flex items-center gap-1.5 border transition-colors",
                z.privacyMasked
                  ? "border-accent/40 text-accent bg-accent/8"
                  : "border-border-subtle text-text-muted hover:border-border-strong hover:text-text-primary"
              )}
            >
              <Lock size={12} />
              {z.privacyMasked ? "Masked" : "Open"}
            </button>
            <button
              type="button"
              onClick={() => remove(z.id)}
              className="h-10 w-10 rounded-lg text-text-muted hover:text-accent-red hover:bg-accent-red/10 transition-colors inline-flex items-center justify-center"
              title="Remove zone"
            >
              <Trash2 size={15} />
            </button>
          </li>
        ))}
      </ul>

      <button
        type="button"
        onClick={add}
        className="w-full py-4 rounded-3xl border border-dashed border-border-strong bg-transparent text-sm text-text-secondary inline-flex items-center justify-center gap-2 hover:border-accent/50 hover:text-accent transition-colors"
      >
        <Plus size={15} />
        Add zone
      </button>
    </div>
  );
}
