"use client";

import { useState } from "react";

import { Panel } from "@/components/ui/Panel";
import { dispatchTrigger } from "@/lib/agent-engine";
import { patchEventContext } from "@/lib/event-context";
import { prefabs } from "@/lib/prefabs";
import { setActivePrefabId } from "@/lib/prefab-store";
import { cn } from "@/lib/utils";

export function PrefabSelector({ onSelect }: { onSelect?: (id: string) => void }) {
  const [active, setActive] = useState<string | null>(null);

  async function load(id: string) {
    const prefab = prefabs.find((p) => p.id === id);
    if (!prefab) return;
    setActive(id);
    setActivePrefabId(id);
    patchEventContext({
      boothSize: prefab.boothSize,
      zones: prefab.zones.map((z) => ({
        id: z.id,
        name: z.label,
        type: "other" as const,
        polygon: z.polygon,
        color: z.color,
      })),
    });
    await dispatchTrigger({
      type: "twin_layout_loaded",
      timestamp: Date.now(),
      payload: {
        layout: {
          zones: prefab.zones.map((z) => ({
            id: z.id,
            label: z.label,
            polygon: z.polygon,
          })),
        },
      },
    });
    onSelect?.(id);
  }

  return (
    <Panel title="Venue prefabs" subtitle="Load a spatial template without uploading">
      <div className="grid grid-cols-2 gap-3">
        {prefabs.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => load(p.id)}
            className={cn(
              "text-left rounded-lg border p-3 transition-colors",
              active === p.id
                ? "border-accent/50 bg-accent/10"
                : "border-border-subtle hover:border-border-default bg-bg-elevated/40"
            )}
          >
            <img
              src={p.thumbnail}
              alt=""
              className="w-full h-16 object-contain rounded mb-2 bg-bg-canvas"
            />
            <div className="text-sm font-medium">{p.name}</div>
            <p className="text-[11px] text-text-muted mt-0.5">{p.description}</p>
          </button>
        ))}
      </div>
    </Panel>
  );
}
