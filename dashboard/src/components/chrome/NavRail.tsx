"use client";

import {
  Box,
  FileBarChart,
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
];

export function NavRail() {
  const pathname = usePathname();
  return (
    <nav className="hidden md:flex flex-col w-20 shrink-0 surface-container border-r border-outline-variant">
      {/* Navigation items — M3 nav rail style: icon above label, no pill container */}
      <div className="flex flex-col items-center gap-2 py-4 flex-1">
        {items.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || (href !== "/" && pathname.startsWith(href));
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex flex-col items-center justify-center gap-1 w-16 h-14 rounded-2xl transition-all duration-200",
                active
                  ? "bg-secondary-container text-on-secondary-container"
                  : "text-on-surface-variant hover:bg-surface-container-high"
              )}
              title={label}
            >
              <Icon size={20} strokeWidth={active ? 2.5 : 1.8} />
              <span className="label-small">{label}</span>
            </Link>
          );
        })}
      </div>

      {/* Bottom action */}
      <div className="flex flex-col items-center pb-4">
        <Link
          href="/"
          className="flex flex-col items-center justify-center gap-1 w-16 h-14 rounded-2xl text-on-surface-variant hover:bg-surface-container-high transition-colors"
          title="Home"
        >
          <Settings size={20} strokeWidth={1.8} />
          <span className="label-small">Home</span>
        </Link>
      </div>
    </nav>
  );
}
