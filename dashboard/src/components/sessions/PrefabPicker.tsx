"use client";

import { LayoutGrid } from "lucide-react";

import { prefabs } from "@/lib/prefabs";
import { cn } from "@/lib/utils";

export function PrefabPicker({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (prefabId: string) => void;
}) {
  return (
    <section className="mb-10">
      <h2 className="text-lg font-semibold tracking-tight inline-flex items-center gap-2 mb-1">
        <LayoutGrid size={18} className="text-accent" />
        Space template
      </h2>
      <p className="text-sm text-text-secondary mb-4 max-w-2xl">
        Pick the shape of the venue you&apos;re mapping. We&apos;ll seed zones and
        booth dimensions for the live sensor and digital twin — you can still edit
        zones below.
      </p>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {prefabs.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => onChange(p.id)}
            className={cn(
              "text-left rounded-xl border p-3 transition-colors",
              value === p.id
                ? "border-accent/50 bg-accent/10 ring-1 ring-accent/30"
                : "border-border-subtle hover:border-border-default bg-bg-elevated/40"
            )}
          >
            <img
              src={p.thumbnail}
              alt=""
              className="w-full h-16 object-contain rounded-lg mb-2 bg-bg-canvas"
            />
            <div className="text-sm font-medium">{p.name}</div>
            <p className="text-[11px] text-text-muted mt-0.5 leading-snug">
              {p.description}
            </p>
            <p className="text-[10px] tabular text-text-faint mt-1.5">
              {p.boothSize.width}m × {p.boothSize.depth}m · {p.zones.length} zones
            </p>
          </button>
        ))}
      </div>
    </section>
  );
}
