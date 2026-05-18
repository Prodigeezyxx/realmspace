"use client";

import {
  ArrowUpRight,
  ChevronDown,
  Cpu,
  Plus,
  Power,
  Signal,
  Sparkles,
  Wifi,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { SignOutButton } from "@/components/auth/LoginForm";
import { useAuth } from "@/components/auth/AuthProvider";
import { Pill } from "@/components/ui/Pill";
import {
  getTypeMeta,
  STATUS_META,
} from "@/lib/session/presets";
import {
  sessionActions,
  useActiveSession,
  useSessions,
} from "@/lib/session/store";
import { useLiveSession } from "@/lib/live-session/store";
import { cn } from "@/lib/utils";

export function StatusBar() {
  const router = useRouter();
  const pathname = usePathname();
  const active = useActiveSession();
  const sessions = useSessions();

  const [now, setNow] = useState<Date | null>(null);
  const [open, setOpen] = useState(false);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const switcherRef = useRef<HTMLDivElement>(null);

  // Clock (effect that registers an interval is allowed by lint rule because
  // it's syncing an external system — the wall clock — into React state).
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  // Click-outside for the switcher dropdown
  useEffect(() => {
    if (!open) return;
    const handle = (e: MouseEvent) => {
      if (!switcherRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [open]);

  const status = STATUS_META[active.status];
  const meta = getTypeMeta(active.type);
  const Icon = meta.icon;
  const cameraCount = active.cameras.length;
  const detectorStatus = useLiveSession((s) => s.status);
  const detectorFps = useLiveSession((s) => s.stats?.fps ?? 0);
  const { user, configured: authConfigured } = useAuth();

  function pick(id: string) {
    sessionActions.setActive(id);
    setOpen(false);
    // If user is on /sessions, take them to /live; otherwise stay put.
    if (pathname === "/sessions") router.push("/live");
  }

  return (
    <>
      <header className="h-16 flex items-center justify-between px-5 sticky top-0 z-30 bg-bg-base/85 backdrop-blur-xl border-b border-border-hairline">
        {/* Left — logo + session switcher */}
        <div className="flex items-center gap-3 min-w-0">
          <Link
            href="/"
            className="bg-bg-raised border border-border-subtle h-11 rounded-full px-4 inline-flex items-center gap-2.5 hover:border-border-strong transition-colors shadow-[var(--shadow-sm)]"
          >
            <div className="relative w-5 h-5">
              <div className="absolute inset-0 rounded-full bg-accent shadow-[var(--glow-green)]" />
              <div className="absolute inset-[3px] rounded-full bg-bg-base" />
              <div className="absolute inset-[5px] rounded-full bg-accent" />
            </div>
            <span className="text-sm font-semibold tracking-tight">RealmSpace</span>
          </Link>

          {/* Session switcher — clickable pill that drops a list */}
          <div ref={switcherRef} className="relative min-w-0">
            <button
              onClick={() => setOpen((v) => !v)}
              className="flex items-center gap-2.5 h-11 pl-3 pr-3 rounded-full bg-bg-raised border border-border-subtle hover:border-border-strong transition-colors max-w-[420px]"
              aria-haspopup="listbox"
              aria-expanded={open}
            >
              <span
                className={cn(
                  "w-2 h-2 rounded-full shrink-0",
                  active.status === "live"
                    ? "bg-accent-red animate-pulse"
                    : active.status === "completed"
                      ? "bg-accent"
                      : "bg-text-muted"
                )}
              />
              <span className="flex items-center gap-1.5 min-w-0">
                <Icon
                  size={13}
                  strokeWidth={2}
                  className="text-text-muted shrink-0"
                />
                <span className="text-sm font-medium truncate">
                  {active.name}
                </span>
              </span>
              <span className="hidden md:inline text-xs text-text-muted truncate">
                · {active.venue}
              </span>
              <ChevronDown
                size={14}
                className={cn(
                  "text-text-muted shrink-0 transition-transform",
                  open && "rotate-180"
                )}
              />
            </button>

            {open && (
              <div className="absolute top-full left-0 mt-2 w-[420px] max-w-[calc(100vw-2rem)] panel-elevated p-2 z-40 max-h-[60vh] overflow-y-auto">
                <div className="px-3 py-2 text-[10px] uppercase tracking-[0.18em] text-text-muted font-medium flex items-center justify-between">
                  <span>Switch session</span>
                  <Link
                    href="/sessions"
                    onClick={() => setOpen(false)}
                    className="text-text-secondary hover:text-text-primary transition-colors lowercase tracking-normal text-xs normal-case"
                  >
                    See all →
                  </Link>
                </div>
                <ul className="space-y-1">
                  {sessions.map((s) => {
                    const isActive = s.id === active.id;
                    const sMeta = getTypeMeta(s.type);
                    const SIcon = sMeta.icon;
                    const sStatus = STATUS_META[s.status];
                    return (
                      <li key={s.id}>
                        <button
                          onClick={() => pick(s.id)}
                          className={cn(
                            "w-full flex items-start gap-3 p-3 rounded-xl text-left transition-colors",
                            isActive
                              ? "bg-accent/10 ring-1 ring-accent/30"
                              : "hover:bg-bg-elevated"
                          )}
                        >
                          <span className="w-9 h-9 rounded-lg bg-bg-elevated border border-border-subtle flex items-center justify-center text-accent shrink-0">
                            <SIcon size={15} strokeWidth={1.8} />
                          </span>
                          <span className="flex-1 min-w-0">
                            <span className="flex items-center gap-1.5 flex-wrap">
                              <span className="text-sm font-medium truncate">
                                {s.name}
                              </span>
                              {s.isDemo && (
                                <span className="text-[9px] uppercase tracking-[0.16em] text-text-muted bg-bg-elevated border border-border-subtle px-1.5 py-px rounded-full">
                                  Demo
                                </span>
                              )}
                            </span>
                            <span className="block text-[11px] text-text-muted truncate mt-0.5">
                              {s.venue}
                            </span>
                          </span>
                          <Pill variant={sStatus.variant} className="!text-[9px] !px-2 !py-1 shrink-0">
                            {s.status === "live" && <span className="live-dot" />}
                            {sStatus.label}
                          </Pill>
                        </button>
                      </li>
                    );
                  })}
                </ul>
                <Link
                  href="/sessions/new"
                  onClick={() => setOpen(false)}
                  className="mt-2 flex items-center gap-2 p-3 rounded-xl border border-dashed border-border-strong text-sm text-text-secondary hover:border-accent/50 hover:text-accent transition-colors"
                >
                  <Plus size={14} />
                  New session
                </Link>
              </div>
            )}
          </div>
        </div>

        {/* Right — sensor pill + clock + status / actions */}
        <div className="flex items-center gap-2.5">
          <div className="hidden lg:flex pill-group h-10 px-3 gap-3 text-[11px] tabular text-text-secondary">
            {detectorStatus === "running" && (
              <>
                <span className="inline-flex items-center gap-1.5 text-accent">
                  <span className="live-dot" />
                  Live · {detectorFps.toFixed(0)} fps
                </span>
                <span className="w-px h-3 bg-border-subtle" />
              </>
            )}
            {detectorStatus === "loading-model" && (
              <>
                <span className="inline-flex items-center gap-1.5 text-accent-amber">
                  Loading object detection model…
                </span>
                <span className="w-px h-3 bg-border-subtle" />
              </>
            )}
            <span className="inline-flex items-center gap-1.5">
              <Signal size={12} className="text-text-muted" />
              {cameraCount} cam{cameraCount === 1 ? "" : "s"}
            </span>
            <span className="w-px h-3 bg-border-subtle" />
            <span className="inline-flex items-center gap-1.5">
              <Cpu size={12} className="text-text-muted" />
              local
            </span>
            <span className="w-px h-3 bg-border-subtle" />
            <span className="inline-flex items-center gap-1.5">
              <Wifi size={12} className="text-accent" />
              {active.privacy.mode}
            </span>
          </div>

          {authConfigured && user && (
            <div className="hidden sm:flex flex-col items-end max-w-[140px]">
              <span className="text-[10px] text-text-muted truncate w-full text-right">
                {user.email}
              </span>
              <SignOutButton />
            </div>
          )}

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

          {active.status === "live" && !active.isDemo ? (
            <button
              onClick={() => setConfirmEnd(true)}
              className="inline-flex items-center gap-2 h-10 px-4 rounded-full border border-accent-red/40 text-accent-red text-xs font-semibold hover:bg-accent-red/10 transition-colors"
            >
              <Power size={13} />
              End session
            </button>
          ) : active.isDemo ? (
            <Link
              href="/sessions/new"
              className="inline-flex items-center gap-2 bg-accent text-text-inverse h-10 pl-4 pr-2.5 rounded-full font-semibold text-xs tracking-tight hover:bg-accent-bright transition-colors shadow-[var(--glow-green)]"
            >
              <Sparkles size={12} />
              New session
              <span className="w-7 h-7 rounded-full bg-text-inverse text-accent inline-flex items-center justify-center">
                <ArrowUpRight size={14} />
              </span>
            </Link>
          ) : (
            <Pill variant={status.variant}>{status.label}</Pill>
          )}
        </div>
      </header>

      {confirmEnd && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center px-4"
          onClick={() => setConfirmEnd(false)}
        >
          <div
            className="panel-elevated p-7 max-w-md w-full"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-xl font-semibold tracking-tight">
              End “{active.name}”?
            </h3>
            <p className="mt-3 text-sm text-text-secondary leading-relaxed">
              The session moves to Completed at{" "}
              <span className="text-text-primary tabular">
                {now?.toLocaleTimeString("en-GB", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
              . The report locks at this moment and live updates stop. The
              graph remains for {active.privacy.retentionDays} days.
            </p>
            <div className="mt-6 flex gap-2 justify-end">
              <button
                onClick={() => setConfirmEnd(false)}
                className="h-11 px-5 rounded-full bg-bg-raised border border-border-subtle text-sm font-medium text-text-secondary hover:text-text-primary hover:border-border-strong transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  sessionActions.endSession(active.id);
                  setConfirmEnd(false);
                  router.push("/report");
                }}
                className="h-11 px-5 rounded-full bg-accent-red text-white text-sm font-semibold hover:bg-accent-red/90 transition-colors inline-flex items-center gap-1.5"
              >
                <Power size={14} />
                End session
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
