"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const items = [
  { href: "/sessions", label: "Sessions", icon: "layers" },
  { href: "/live",     label: "Live",     icon: "monitoring" },
  { href: "/twin",     label: "Twin",     icon: "view_in_ar" },
  { href: "/ask",      label: "Ask",      icon: "chat" },
  { href: "/agents",   label: "Agents",   icon: "bolt" },
  { href: "/report",   label: "Report",   icon: "assessment" },
];

export function NavRail() {
  const pathname = usePathname();
  return (
    <nav className="hidden md:flex flex-col w-20 shrink-0 surface-container border-r border-outline-variant">
      {/* Navigation items — M3 nav rail: Material Symbols icons + label-small text */}
      <div className="flex flex-col items-center gap-2 py-4 flex-1">
        {items.map(({ href, label, icon }) => {
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
              <span className={cn("material-symbol", active && "material-symbol-filled material-symbol-w600")}>
                {icon}
              </span>
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
          <span className="material-symbol">home</span>
          <span className="label-small">Home</span>
        </Link>
      </div>
    </nav>
  );
}
