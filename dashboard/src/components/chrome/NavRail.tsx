"use client";

import {
  Box,
  FileBarChart,
  Gauge,
  MessageSquareText,
  Settings,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const items = [
  { href: "/live", label: "Live", icon: Gauge },
  { href: "/twin", label: "Twin", icon: Box },
  { href: "/ask", label: "Ask", icon: MessageSquareText },
  { href: "/agents", label: "Agents", icon: Zap },
  { href: "/report", label: "Report", icon: FileBarChart },
];

export function NavRail() {
  const pathname = usePathname();
  return (
    <nav className="hidden md:flex flex-col items-center w-16 border-r border-border-hairline bg-bg-panel/60 py-4 gap-1 shrink-0">
      {items.map(({ href, label, icon: Icon }) => {
        const active = pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={cn(
              "group relative flex flex-col items-center justify-center w-12 h-12 rounded-xl transition-colors",
              active
                ? "bg-accent-blue/10 text-accent-blue"
                : "text-text-muted hover:text-text-primary hover:bg-bg-elevated"
            )}
            title={label}
          >
            <Icon size={20} strokeWidth={1.75} />
            <span className="text-[9px] uppercase tracking-[0.15em] mt-1 font-medium">
              {label}
            </span>
            {active && (
              <span className="absolute -left-px top-1/2 -translate-y-1/2 h-6 w-[2px] bg-accent-blue rounded-r-full shadow-[var(--glow-blue)]" />
            )}
          </Link>
        );
      })}
      <div className="flex-1" />
      <Link
        href="/"
        className="w-12 h-12 rounded-xl flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-elevated transition-colors"
        title="Settings"
      >
        <Settings size={20} strokeWidth={1.75} />
      </Link>
    </nav>
  );
}
