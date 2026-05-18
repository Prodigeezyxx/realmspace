"use client";

import {
  ArrowUpRight,
  Building2,
  CalendarClock,
  Camera,
  CheckCircle2,
  ChevronRight,
  Layers,
  MapPin,
  Plus,
  Power,
  Sparkles,
  Trash2,
  Users,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Pill } from "@/components/ui/Pill";
import {
  getTypeMeta,
  STATUS_META,
} from "@/lib/session/presets";
import {
  sessionActions,
  useActiveSessionId,
  useSessions,
} from "@/lib/session/store";
import type { Session, SessionStatus } from "@/lib/session/types";
import { cn } from "@/lib/utils";

type Filter = "all" | "live" | "scheduled" | "completed" | "demo";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all",       label: "All" },
  { id: "live",      label: "Live" },
  { id: "scheduled", label: "Scheduled" },
  { id: "completed", label: "Completed" },
  { id: "demo",      label: "Demo" },
];

export default function SessionsPage() {
  const router = useRouter();
  const sessions = useSessions();
  const activeId = useActiveSessionId();
  const [filter, setFilter] = useState<Filter>("all");
  const [confirmEndId, setConfirmEndId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const counts = useMemo(() => {
    const c = { all: sessions.length, live: 0, scheduled: 0, completed: 0, demo: 0 };
    for (const s of sessions) {
      if (s.isDemo) c.demo += 1;
      if (s.status === "live") c.live += 1;
      else if (s.status === "scheduled") c.scheduled += 1;
      else if (s.status === "completed") c.completed += 1;
    }
    return c;
  }, [sessions]);

  const filtered = useMemo(() => {
    if (filter === "all") return sessions;
    if (filter === "demo") return sessions.filter((s) => s.isDemo);
    return sessions.filter((s) => s.status === filter);
  }, [sessions, filter]);

  function open(id: string) {
    sessionActions.setActive(id);
    router.push("/live");
  }

  return (
    <div className="p-6 md:p-8 max-w-[1400px] mx-auto">
      {/* ── Page header */}
      <div className="flex flex-wrap items-end justify-between gap-6 mb-10">
        <div>
          <Pill variant="success" className="mb-4">
            <Sparkles size={11} />
            Sessions
          </Pill>
          <h1 className="display text-5xl md:text-6xl">
            Every experience<span className="text-accent">,</span>
            <br />
            <span className="text-text-faint">one console.</span>
          </h1>
          <p className="mt-5 text-text-secondary text-base max-w-xl leading-relaxed">
            Each session is a distinct event — its own zones, touchpoints,
            privacy posture and report. Start a new one, switch between them,
            or end a live session and lock the report.
          </p>
        </div>
        <Link
          href="/sessions/new"
          className="inline-flex items-center gap-2 bg-accent text-text-inverse h-12 pl-5 pr-2.5 rounded-full font-semibold text-sm hover:bg-accent-bright transition-colors shadow-[var(--glow-green)]"
        >
          <Plus size={16} />
          New session
          <span className="w-9 h-9 rounded-full bg-text-inverse text-accent inline-flex items-center justify-center">
            <ArrowUpRight size={15} />
          </span>
        </Link>
      </div>

      {/* ── Filter pills */}
      <div className="flex flex-wrap items-center gap-2 mb-6">
        {FILTERS.map((f) => {
          const active = filter === f.id;
          const count =
            f.id === "all"
              ? counts.all
              : f.id === "live"
                ? counts.live
                : f.id === "scheduled"
                  ? counts.scheduled
                  : f.id === "completed"
                    ? counts.completed
                    : counts.demo;
          return (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              className={cn(
                "h-10 px-4 rounded-full inline-flex items-center gap-2 border text-sm font-medium transition-colors",
                active
                  ? "bg-accent text-text-inverse border-accent shadow-[var(--glow-green)]"
                  : "bg-bg-raised border-border-subtle text-text-secondary hover:border-border-strong hover:text-text-primary"
              )}
            >
              {f.label}
              <span
                className={cn(
                  "text-[10px] tabular px-1.5 py-px rounded-full",
                  active ? "bg-text-inverse/15" : "bg-bg-elevated text-text-muted"
                )}
              >
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {/* ── Grid */}
      {filtered.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid lg:grid-cols-2 gap-4">
          {filtered.map((s) => (
            <SessionCard
              key={s.id}
              session={s}
              isActive={s.id === activeId}
              onOpen={() => open(s.id)}
              onEndRequest={() => setConfirmEndId(s.id)}
              onDeleteRequest={() => setConfirmDeleteId(s.id)}
            />
          ))}
        </div>
      )}

      {/* ── Confirm: end session */}
      {confirmEndId && (
        <ConfirmDialog
          title="End this session?"
          message="The session moves to Completed, the report locks at this moment, and live updates stop. The structured graph remains for the configured retention window."
          confirmLabel="End session"
          onConfirm={() => {
            sessionActions.endSession(confirmEndId);
            setConfirmEndId(null);
          }}
          onCancel={() => setConfirmEndId(null)}
        />
      )}

      {/* ── Confirm: delete */}
      {confirmDeleteId && (
        <ConfirmDialog
          title="Delete this session?"
          message="The session is removed from the console and its graph is purged immediately. This cannot be undone."
          confirmLabel="Delete"
          danger
          onConfirm={() => {
            sessionActions.deleteSession(confirmDeleteId);
            setConfirmDeleteId(null);
          }}
          onCancel={() => setConfirmDeleteId(null)}
        />
      )}
    </div>
  );
}

// ── Card ─────────────────────────────────────────────────────────────────

function SessionCard({
  session,
  isActive,
  onOpen,
  onEndRequest,
  onDeleteRequest,
}: {
  session: Session;
  isActive: boolean;
  onOpen: () => void;
  onEndRequest: () => void;
  onDeleteRequest: () => void;
}) {
  const meta = getTypeMeta(session.type);
  const Icon = meta.icon;
  const status = STATUS_META[session.status];

  return (
    <article
      className={cn(
        "panel-elevated p-6 flex flex-col gap-5 transition-all",
        isActive && "ring-1 ring-accent/50 shadow-[var(--glow-green)]"
      )}
    >
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-2xl bg-bg-elevated border border-border-subtle flex items-center justify-center text-accent shrink-0">
          <Icon size={20} strokeWidth={1.8} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <Pill variant={status.variant}>
              {session.status === "live" && <span className="live-dot" />}
              {status.label}
            </Pill>
            <Pill variant="outline">{meta.label}</Pill>
            {session.isDemo && (
              <Pill variant="violet">
                <Sparkles size={10} />
                Demo
              </Pill>
            )}
            {isActive && (
              <Pill variant="success">
                <CheckCircle2 size={10} />
                Active
              </Pill>
            )}
          </div>
          <h2 className="mt-3 text-2xl font-semibold tracking-tight leading-tight">
            {session.name}
          </h2>
          {session.brand && (
            <p className="mt-1 text-sm text-text-secondary">
              {session.brand}
              {session.client && session.client !== session.brand && (
                <> · {session.client}</>
              )}
            </p>
          )}
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
        <DlRow icon={<MapPin size={12} />} label="Venue" value={session.venue} />
        <DlRow
          icon={<CalendarClock size={12} />}
          label="When"
          value={formatRange(session.startAt, session.endAt)}
        />
        <DlRow
          icon={<Layers size={12} />}
          label="Zones"
          value={`${session.zones.length}`}
        />
        <DlRow
          icon={<Zap size={12} />}
          label="Touchpoints"
          value={`${session.touchpoints.length}`}
        />
        <DlRow
          icon={<Camera size={12} />}
          label="Cameras"
          value={`${session.cameras.length}`}
        />
        <DlRow
          icon={<Users size={12} />}
          label="Footfall target"
          value={
            session.expectedDailyFootfall
              ? `${session.expectedDailyFootfall.toLocaleString()} / day`
              : "—"
          }
        />
      </dl>

      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={onOpen}
          className="flex-1 inline-flex items-center justify-center gap-2 bg-accent text-text-inverse h-11 px-5 rounded-full font-semibold text-sm hover:bg-accent-bright transition-colors shadow-[var(--glow-green)]"
        >
          {isActive ? "Open live" : "Switch & open"}
          <ChevronRight size={15} />
        </button>
        {session.status === "live" && !session.isDemo && (
          <button
            onClick={onEndRequest}
            className="h-11 px-4 rounded-full inline-flex items-center gap-1.5 border border-accent-red/40 text-accent-red hover:bg-accent-red/10 text-sm transition-colors"
          >
            <Power size={14} />
            End
          </button>
        )}
        {!session.isDemo && (
          <button
            onClick={onDeleteRequest}
            className="h-11 w-11 rounded-full inline-flex items-center justify-center text-text-muted hover:text-accent-red hover:bg-accent-red/10 transition-colors"
            title="Delete session"
          >
            <Trash2 size={15} />
          </button>
        )}
      </div>

      {session.isDemo && (
        <div className="text-[11px] text-text-muted leading-relaxed border-t border-border-hairline pt-3">
          <Building2 size={11} className="inline mr-1.5 align-text-bottom" />
          Seeded demo experience. Lives in code, can&apos;t be edited or deleted —
          use it to explore the dashboard without setting up a real session.
        </div>
      )}
    </article>
  );
}

function DlRow({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-[0.16em] text-text-muted font-medium flex items-center gap-1.5">
        {icon}
        {label}
      </dt>
      <dd className="text-sm mt-0.5 truncate">{value}</dd>
    </div>
  );
}

function formatRange(startIso: string, endIso?: string): string {
  const start = new Date(startIso);
  const end = endIso ? new Date(endIso) : null;
  const fmt: Intl.DateTimeFormatOptions = {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  };
  const s = start.toLocaleString("en-GB", fmt);
  if (!end) return `${s} (open-ended)`;
  return `${s} → ${end.toLocaleString("en-GB", fmt)}`;
}

// ── Empty state ──────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="panel-elevated p-12 text-center max-w-2xl mx-auto">
      <div className="w-14 h-14 rounded-2xl bg-bg-elevated border border-border-subtle inline-flex items-center justify-center text-accent mb-5">
        <Sparkles size={22} />
      </div>
      <h3 className="text-2xl font-semibold tracking-tight">
        No sessions match this filter.
      </h3>
      <p className="mt-2 text-text-secondary text-sm">
        Try a different filter, or start a new session to begin mapping a new
        experience.
      </p>
      <Link
        href="/sessions/new"
        className="mt-6 inline-flex items-center gap-2 bg-accent text-text-inverse h-11 pl-5 pr-2.5 rounded-full font-semibold text-sm hover:bg-accent-bright transition-colors shadow-[var(--glow-green)]"
      >
        <Plus size={15} />
        New session
        <span className="w-9 h-9 rounded-full bg-text-inverse text-accent inline-flex items-center justify-center">
          <ArrowUpRight size={14} />
        </span>
      </Link>
    </div>
  );
}

// ── Confirm dialog ───────────────────────────────────────────────────────

function ConfirmDialog({
  title,
  message,
  confirmLabel,
  onConfirm,
  onCancel,
  danger,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  danger?: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center px-4"
      onClick={onCancel}
    >
      <div
        className="panel-elevated p-7 max-w-md w-full"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-xl font-semibold tracking-tight">{title}</h3>
        <p className="mt-3 text-sm text-text-secondary leading-relaxed">
          {message}
        </p>
        <div className="mt-6 flex gap-2 justify-end">
          <button
            onClick={onCancel}
            className="h-11 px-5 rounded-full bg-bg-raised border border-border-subtle text-sm font-medium text-text-secondary hover:text-text-primary hover:border-border-strong transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={cn(
              "h-11 px-5 rounded-full text-sm font-semibold transition-colors",
              danger
                ? "bg-accent-red text-white hover:bg-accent-red/90"
                : "bg-accent text-text-inverse hover:bg-accent-bright shadow-[var(--glow-green)]"
            )}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
