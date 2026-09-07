"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const items = [
  { href: "/sessions", label: "Sessions", icon: "space_dashboard" },
  { href: "/live", label: "Live", icon: "monitoring" },
  { href: "/twin", label: "Twin", icon: "view_in_ar" },
  { href: "/ask", label: "Ask", icon: "forum" },
  { href: "/agents", label: "Agents", icon: "automation" },
  { href: "/report", label: "Report", icon: "analytics" },
];

export function NavRail() {
  const pathname = usePathname();
  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  return (
    <>
      <aside className="realm-nav-rail hidden md:flex w-[84px] xl:w-[224px] shrink-0 flex-col sticky top-[72px] h-[calc(100dvh-72px)] z-20">
        <div className="px-3 xl:px-4 pt-5 pb-3">
          <Link
            href="/sessions/new"
            className="min-h-12 rounded-[18px] bg-accent text-[var(--md-sys-color-on-primary)] flex items-center justify-center xl:justify-start gap-3 px-3.5 font-semibold shadow-[var(--glow-green)] hover:bg-accent-bright transition-colors"
            title="Create session"
          >
            <span className="material-symbol material-symbol-filled">add</span>
            <span className="hidden xl:inline">Create session</span>
          </Link>
        </div>

        <nav aria-label="Product" className="flex-1 px-2.5 xl:px-3 py-2 space-y-1">
          {items.map(({ href, label, icon }) => {
            const active = isActive(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "group relative min-h-[54px] rounded-[18px] flex items-center justify-center xl:justify-start gap-3 px-3 transition-all duration-200",
                  active
                    ? "bg-primary-container text-on-primary-container"
                    : "text-text-secondary hover:bg-bg-elevated hover:text-text-primary"
                )}
                title={label}
              >
                {active && <span className="absolute left-0 top-3 bottom-3 w-1 rounded-r-full bg-accent" />}
                <span className={cn("material-symbol", active && "material-symbol-filled")}>{icon}</span>
                <span className="hidden xl:inline text-sm font-semibold">{label}</span>
                {href === "/live" && (
                  <span className="hidden xl:block ml-auto w-1.5 h-1.5 rounded-full bg-accent-red" />
                )}
              </Link>
            );
          })}
        </nav>

        <div className="p-3 border-t border-border-hairline">
          <Link
            href="/"
            className="min-h-[52px] rounded-[18px] flex items-center justify-center xl:justify-start gap-3 px-3 text-text-muted hover:text-text-primary hover:bg-bg-elevated transition-colors"
            title="Public site"
          >
            <span className="material-symbol">arrow_outward</span>
            <span className="hidden xl:inline text-sm font-semibold">Public site</span>
          </Link>
          <div className="hidden xl:block px-3 pt-3 pb-1">
            <div className="text-[10px] uppercase tracking-[.14em] text-text-faint">realmspace</div>
            <div className="text-[11px] text-text-muted mt-1">Spatial intelligence OS</div>
          </div>
        </div>
      </aside>

      <nav className="mobile-nav md:hidden grid grid-cols-6" aria-label="Product navigation">
        {items.map(({ href, label, icon }) => {
          const active = isActive(href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "min-w-0 min-h-[52px] flex flex-col items-center justify-center gap-0.5 rounded-[16px] transition-colors",
                active ? "bg-primary-container text-on-primary-container" : "text-text-muted"
              )}
            >
              <span className={cn("material-symbol material-symbol-sm", active && "material-symbol-filled")}>{icon}</span>
              <span className="text-[9px] font-semibold truncate max-w-full px-0.5">{label}</span>
            </Link>
          );
        })}
      </nav>
    </>
  );
}
