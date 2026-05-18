"use client";

import { Plus, Trash2 } from "lucide-react";

import { TOUCHPOINT_TYPE_OPTIONS } from "@/lib/session/presets";
import type { Touchpoint, TouchpointType, Zone } from "@/lib/session/types";

interface Props {
  touchpoints: Touchpoint[];
  zones: Zone[];
  onChange: (next: Touchpoint[]) => void;
}

function uid() {
  return `tp_${Math.random().toString(36).slice(2, 9)}`;
}

export function TouchpointEditor({ touchpoints, zones, onChange }: Props) {
  function update(id: string, patch: Partial<Touchpoint>) {
    onChange(
      touchpoints.map((t) => (t.id === id ? { ...t, ...patch } : t))
    );
  }
  function remove(id: string) {
    onChange(touchpoints.filter((t) => t.id !== id));
  }
  function add() {
    const t: Touchpoint = {
      id: uid(),
      name: `Touchpoint ${touchpoints.length + 1}`,
      type: "screen",
      zoneId: zones[0]?.id,
      triggers: ["viewed"],
    };
    onChange([...touchpoints, t]);
  }

  return (
    <div className="space-y-3">
      <div className="hidden md:grid grid-cols-[2fr_1.4fr_1.4fr_1.4fr_44px] gap-3 px-4 text-[10px] uppercase tracking-[0.16em] text-text-muted font-medium">
        <span>Name</span>
        <span>Type</span>
        <span>Zone</span>
        <span>Sponsor</span>
        <span />
      </div>

      <ul className="space-y-2">
        {touchpoints.map((t) => (
          <li
            key={t.id}
            className="panel grid grid-cols-1 md:grid-cols-[2fr_1.4fr_1.4fr_1.4fr_44px] gap-3 items-center p-4"
          >
            <input
              value={t.name}
              onChange={(e) => update(t.id, { name: e.target.value })}
              placeholder="Touchpoint name"
              className="h-10 px-3 rounded-lg bg-bg-canvas border border-border-subtle text-sm focus:border-accent focus:outline-none transition-colors"
            />
            <select
              value={t.type}
              onChange={(e) =>
                update(t.id, { type: e.target.value as TouchpointType })
              }
              className="h-10 px-3 rounded-lg bg-bg-canvas border border-border-subtle text-sm focus:border-accent focus:outline-none transition-colors appearance-none cursor-pointer"
            >
              {TOUCHPOINT_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <select
              value={t.zoneId ?? ""}
              onChange={(e) => update(t.id, { zoneId: e.target.value || undefined })}
              className="h-10 px-3 rounded-lg bg-bg-canvas border border-border-subtle text-sm focus:border-accent focus:outline-none transition-colors appearance-none cursor-pointer"
            >
              <option value="">— Unassigned —</option>
              {zones.map((z) => (
                <option key={z.id} value={z.id}>
                  {z.name}
                </option>
              ))}
            </select>
            <input
              value={t.sponsor ?? ""}
              onChange={(e) => update(t.id, { sponsor: e.target.value || undefined })}
              placeholder="Sponsor (optional)"
              className="h-10 px-3 rounded-lg bg-bg-canvas border border-border-subtle text-sm focus:border-accent focus:outline-none transition-colors"
            />
            <button
              type="button"
              onClick={() => remove(t.id)}
              className="h-10 w-10 rounded-lg text-text-muted hover:text-accent-red hover:bg-accent-red/10 transition-colors inline-flex items-center justify-center"
              title="Remove touchpoint"
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
        Add touchpoint
      </button>
    </div>
  );
}
