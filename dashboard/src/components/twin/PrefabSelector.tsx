"use client";

import { useState } from "react";

import { Panel } from "@/components/ui/Panel";
import { dispatchTrigger } from "@/lib/agent-engine";
import { patchEventContext } from "@/lib/event-context";
import { prefabs } from "@/lib/prefabs";
import { applyPrefabToDraft } from "@/lib/prefabs/apply";
import { setActivePrefabId } from "@/lib/prefab-store";
import { sessionActions, useActiveSession } from "@/lib/session/store";
import { cn } from "@/lib/utils";

export function PrefabSelector({ onSelect }: { onSelect?: (id: string) => void }) {
  const active = useActiveSession();
  const [selected, setSelected] = useState<string | null>(active.prefabId ?? null);

  async function load(id: string) {
    const applied = applyPrefabToDraft(id, {
      newId: (p) => `${p}_${Math.random().toString(36).slice(2, 7)}`,
      prevZones: active.zones,
      touchpoints: active.touchpoints,
    });
    if (!applied) return;

    setSelected(id);
    setActivePrefabId(id);

    sessionActions.updateSession(active.id, {
      prefabId: applied.prefabId,
      boothSize: applied.boothSize,
      zones: applied.zones,
      touchpoints: applied.touchpoints,
    });

    patchEventContext({
      boothSize: applied.boothSize,
      zones: applied.zones,
    });

    await dispatchTrigger({
      type: "twin_layout_loaded",
      timestamp: Date.now(),
      payload: {
        layout: {
          zones: applied.zones
            .filter((z) => z.polygon?.length)
            .map((z) => ({
              id: z.id,
              label: z.name,
              polygon: z.polygon!,
            })),
        },
      },
    });
    onSelect?.(id);
  }

  return (
    <Panel
      title="Change space template"
      subtitle="Updates the active session layout — set during onboarding for new sessions"
    >
      <div className="grid grid-cols-2 gap-3">
        {prefabs.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => load(p.id)}
            className={cn(
              "text-left rounded-lg border p-3 transition-colors",
              selected === p.id
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
