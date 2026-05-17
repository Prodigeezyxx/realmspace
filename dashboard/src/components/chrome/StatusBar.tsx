"use client";

import { Activity, Cpu, Signal, Thermometer, Wifi } from "lucide-react";
import { useEffect, useState } from "react";

import { Pill } from "@/components/ui/Pill";

export function StatusBar({
  sessionName,
  venue,
  cameraCount = 1,
}: {
  sessionName: string;
  venue: string;
  cameraCount?: number;
}) {
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <header className="h-12 border-b border-border-hairline bg-bg-panel/80 backdrop-blur-xl flex items-center justify-between px-4 sticky top-0 z-30">
      <div className="flex items-center gap-3 min-w-0">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-full bg-gradient-to-br from-accent-cyan to-accent-blue flex items-center justify-center shadow-[var(--glow-cyan)]">
            <div className="w-2.5 h-2.5 rounded-full bg-bg-base" />
          </div>
          <span className="text-sm font-semibold tracking-tight">RealmSpace</span>
        </div>
        <span className="h-4 w-px bg-border-subtle" />
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium truncate">{sessionName}</span>
          <span className="text-text-muted text-xs">·</span>
          <span className="text-xs text-text-secondary truncate">{venue}</span>
        </div>
        <Pill variant="live">
          <span className="live-dot" />
          Live
        </Pill>
      </div>

      <div className="flex items-center gap-5 text-xs text-text-secondary tabular">
        <span className="hidden md:flex items-center gap-1.5">
          <Cpu size={13} className="text-text-muted" />
          <span>edge · M2 Pro</span>
        </span>
        <span className="hidden md:flex items-center gap-1.5">
          <Signal size={13} className="text-text-muted" />
          {cameraCount} cam{cameraCount > 1 ? "s" : ""}
        </span>
        <span className="hidden lg:flex items-center gap-1.5">
          <Activity size={13} className="text-accent-green" />
          22 fps
        </span>
        <span className="hidden lg:flex items-center gap-1.5">
          <Thermometer size={13} className="text-text-muted" />
          47°C
        </span>
        <span className="hidden md:flex items-center gap-1.5">
          <Wifi size={13} className="text-accent-green" />
          local
        </span>
        <span className="font-mono text-text-primary tracking-wider">
          {now
            ? now.toLocaleTimeString("en-US", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false,
              })
            : "--:--:--"}
        </span>
      </div>
    </header>
  );
}
