"use client";

import { useEffect } from "react";

import { setEventContextFromSession } from "@/lib/event-context";
import { session as demoMeta, zones as demoZones } from "@/lib/mock/session";
import { useActiveSession } from "@/lib/session/store";

/** Keeps agent event context in sync with the active session */
export function useEventSession() {
  const active = useActiveSession();

  useEffect(() => {
    const zones =
      active.isDemo
        ? active.zones.map((z) => {
            const poly = demoZones.find((d) => d.id === z.id)?.polygon;
            return poly ? { ...z, polygon: poly } : z;
          })
        : active.zones;

    setEventContextFromSession(
      { ...active, zones },
      active.boothSize ?? demoMeta.boothSize
    );
  }, [active]);
}
