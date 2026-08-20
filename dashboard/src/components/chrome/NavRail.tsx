"use client";

import {
  AlertTriangle,
  Box,
  Camera,
  FileBarChart,
  FileSpreadsheet,
  Gauge,
  Layers,
  MessageSquareText,
  Settings,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const items = [
  { href: "/sessions", label: "Sessions", icon: Layers },
  { href: "/live",     label: "Live",     icon: Gauge },
  { href: "/twin",     label: "Twin",     icon: Box },
  { href: "/ask",      label: "Ask",      icon: MessageSquareText },
  { href: "/agents",   label: "Agents",   icon: Zap },
  { href: "/report",   label: "Report",   icon: FileBarChart },
  { href: "/ledger",   label: "Ledger",   icon: FileSpreadsheet },
  // `/ops` has existed since the HITL queue shipped and was never reachable
  // from here — a review queue nobody can navigate to is a review queue nobody
  // reads, which is the whole failure that screen was built to avoid.
  { href: "/ops",      label: "Ops",      icon: AlertTriangle },
  // The calibration step `privacy.md` has described since Phase 0 and that
  // nothing implemented until Phase 6. Nested under /sessions because it
  // calibrates one, which is why `active` below can no longer be a plain
  // prefix test.
  { href: "/sessions/calibration", label: "Cameras", icon: Camera },
];

export function NavRail() {
  const pathname = usePathname();
  return (
    <nav className="hidden md:flex flex-col items-center w-20 py-4 px-3 shrink-0">
      <div
        className="bg-bg-raised border border-border-subtle rounded-full flex flex-col items-center gap-1 p-1.5 shadow-[var(--shadow-sm)]"
      >
        {items.map(({ href, label, icon: Icon }) => {
          // Longest match wins. A plain `startsWith` lit Sessions *and* Cameras
          // on /sessions/calibration, and two highlighted tabs tell a reader
          // nothing about where they are.
          const active =
            pathname.startsWith(href) &&
            !items.some((o) => o.href.length > href.length && pathname.startsWith(o.href));
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "group relative flex flex-col items-center justify-center w-12 h-12 rounded-full transition-colors",
                active
                  ? "bg-accent text-text-inverse shadow-[var(--glow-green)]"
                  : "text-text-muted hover:text-text-primary hover:bg-bg-elevated"
              )}
              title={label}
            >
              <Icon size={18} strokeWidth={2} />
              <span className="text-[8px] uppercase tracking-[0.16em] mt-0.5 font-semibold">
                {label}
              </span>
            </Link>
          );
        })}
      </div>
      <div className="flex-1" />
      <Link
        href="/"
        className="w-12 h-12 rounded-full flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-elevated transition-colors"
        title="Settings"
      >
        <Settings size={18} strokeWidth={2} />
      </Link>
    </nav>
  );
}
