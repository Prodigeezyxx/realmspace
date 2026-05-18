"use client";

import { ArrowUpRight, Cpu, Signal, Wifi } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

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
    <header className="h-16 flex items-center justify-between px-5 sticky top-0 z-30 bg-bg-base/85 backdrop-blur-xl border-b border-border-hairline">
      {/* Left — logo pill */}
      <div className="flex items-center gap-3 min-w-0">
        <Link
          href="/"
          className="pill-group h-11 pl-3 pr-4 gap-2 group hover:border-border-strong transition-colors"
          style={{ padding: 0 }}
        >
          <div className="h-11 pl-3 pr-4 inline-flex items-center gap-2">
            <div className="relative w-6 h-6">
              <div className="absolute inset-0 rounded-full bg-accent shadow-[var(--glow-green)]" />
              <div className="absolute inset-[3px] rounded-full bg-bg-base" />
              <div className="absolute inset-[6px] rounded-full bg-accent" />
            </div>
            <span className="text-[15px] font-semibold tracking-tight">
              RealmSpace
            </span>
          </div>
        </Link>

        <div className="hidden md:flex items-center gap-2 pl-2 min-w-0">
          <div className="alert-dot" />
          <span className="text-sm font-medium truncate">{sessionName}</span>
          <span className="text-text-muted text-xs">·</span>
          <span className="text-xs text-text-secondary truncate">{venue}</span>
        </div>
      </div>

      {/* Right — sensor pill + clock + action */}
      <div className="flex items-center gap-2.5">
        <div className="hidden lg:flex pill-group h-10 px-3 gap-3 text-[11px] tabular text-text-secondary">
          <span className="inline-flex items-center gap-1.5">
            <Signal size={12} className="text-text-muted" />
            {cameraCount} cam{cameraCount > 1 ? "s" : ""}
          </span>
          <span className="w-px h-3 bg-border-subtle" />
          <span className="inline-flex items-center gap-1.5">
            <Cpu size={12} className="text-text-muted" />
            M2 Pro
          </span>
          <span className="w-px h-3 bg-border-subtle" />
          <span className="inline-flex items-center gap-1.5">
            <Wifi size={12} className="text-accent" />
            local
          </span>
        </div>

        <div className="hidden md:inline-flex pill-group h-10 px-4 font-mono text-xs tabular text-text-primary tracking-wider">
          {now
            ? now.toLocaleTimeString("en-US", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false,
              })
            : "--:--:--"}
        </div>

        <Link
          href="/"
          className="inline-flex items-center gap-2 bg-accent text-text-inverse h-10 pl-4 pr-2.5 rounded-full font-semibold text-xs tracking-tight hover:bg-accent-bright transition-colors shadow-[var(--glow-green)]"
        >
          Book a pilot
          <span className="w-7 h-7 rounded-full bg-text-inverse text-accent inline-flex items-center justify-center">
            <ArrowUpRight size={14} />
          </span>
        </Link>
      </div>
    </header>
  );
}
