"use client";

/**
 * realmspace — the human-in-the-loop review queue.
 *
 * Events no consumer could process, and what can be done about each.
 * `event-bus-spec.md` §5 promised this screen from the start; the table has
 * existed since migration 0001 and nothing has ever been able to look at it.
 *
 * The design problem here is not the data, it is whether anybody keeps reading
 * it. A queue that lists the same failure three times, or fills with entries
 * nobody can clear, gets ignored within a week — and an ignored queue is worse
 * than none, because it looks like coverage. So: repeat failures collapse onto
 * one row (backend), every entry can be cleared even when it cannot be retried,
 * and a retry that fails says so instead of quietly doing nothing.
 */

import {
  AlertTriangle,
  Camera,
  Check,
  HelpCircle,
  Info,
  RefreshCw,
  RotateCcw,
  X,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { useDeadLetters, type DeadLetter } from "@/lib/ops/useDeadLetters";
import {
  drift,
  METRIC_LABELS,
  useDriftEvents,
  type DriftFinding,
} from "@/lib/ops/useDriftEvents";
import { describeLimit, usePlan, type PlanLimit } from "@/lib/ops/usePlan";
import {
  useStrandedDispatches,
  type StrandedDispatch,
  type Verdict,
} from "@/lib/ops/useStrandedDispatches";

export default function OpsPage() {
  const { status, items, detail, refresh, retry, dismiss } = useDeadLetters();
  const [busy, setBusy] = useState<number | null>(null);
  const [outcome, setOutcome] = useState<Record<number, string>>({});

  async function run(id: number, action: (id: number) => Promise<string | null>) {
    setBusy(id);
    const error = await action(id);
    setBusy(null);
    setOutcome((prev) => ({ ...prev, [id]: error ?? "" }));
  }

  return (
    <div className="max-w-[1100px] mx-auto p-6 md:p-10 space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <Pill variant={items.length ? "warn" : "success"} className="mb-2">
            <AlertTriangle size={11} />
            {items.length
              ? `${items.length} awaiting review`
              : "Nothing awaiting review"}
          </Pill>
          <h1 className="text-2xl font-semibold tracking-tight">Failed events</h1>
          <p className="text-sm text-text-secondary mt-1 max-w-2xl leading-relaxed">
            Events a consumer could not process after repeated attempts. They were
            set aside rather than retried forever, so one bad event never stops
            the rest of the activation being measured.
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          icon={<RefreshCw size={14} />}
          onClick={() => void refresh()}
        >
          Refresh
        </Button>
      </div>

      {status === "loading" && (
        <EmptyState variant="page" title="Reading the queue…" />
      )}

      {(status === "offline" || status === "error") && (
        <EmptyState
          variant="page"
          icon={<Info size={20} />}
          title={
            status === "offline"
              ? "No backend configured"
              : "Could not read the queue"
          }
          hint={detail ?? undefined}
        />
      )}

      {status === "ready" && !items.length && (
        <EmptyState
          variant="page"
          icon={<Check size={20} />}
          title="Nothing has failed"
          hint="Every event so far has been processed. This is a measured empty, not an absent signal — the queue is being read."
        />
      )}

      {status === "ready" &&
        items.map((item) => (
          <QueueItem
            key={item.id}
            item={item}
            busy={busy === item.id}
            outcome={outcome[item.id]}
            onRetry={() => void run(item.id, retry)}
            onDismiss={() => void run(item.id, dismiss)}
          />
        ))}

      <StrandedSection />
      <DriftSection />
      <PlanSection />
    </div>
  );
}

/**
 * What this organisation's plan allows, and how close they are to it.
 *
 * The fourth section and the only one that is not about something going wrong.
 * It is here because the alternative is worse: a save refused with a 402 on the
 * morning of an activation, from a limit nobody had been shown. The same
 * argument the report's dead export button settled — the operator should not
 * find out in front of the client.
 *
 * There is no upgrade button. Changing a plan needs a payment path and the
 * Stripe half of `roadmap.md`'s billing bullet is unbuilt, so a control here
 * would be wired to nothing.
 */
function PlanSection() {
  const { status, plan, detail, refresh } = usePlan();

  // Quiet when there is no backend. An operator running the demo on a laptop
  // has no organisation and no bill, and a panel saying so would be furniture.
  if (status === "loading" || status === "offline") return null;

  return (
    <>
      <div className="flex items-end justify-between gap-4 flex-wrap border-t border-border-hairline pt-6">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Plan</h2>
          <p className="text-sm text-text-secondary mt-1 max-w-2xl leading-relaxed">
            What this organisation&rsquo;s tier allows. A save that would exceed
            one of these is refused, so it is worth knowing before the doors
            open.
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          icon={<RefreshCw size={14} />}
          onClick={() => void refresh()}
        >
          Refresh
        </Button>
      </div>

      {status === "error" && (
        <EmptyState
          variant="page"
          icon={<Info size={20} />}
          title="Could not read the plan"
          hint={detail ?? undefined}
        />
      )}

      {status === "ready" && plan && (
        <Panel
          title={plan.label}
          subtitle={
            plan.retentionDays === null
              ? "No retention limit on this tier."
              : `${plan.retentionDays} days of your own log are readable back.`
          }
        >
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <LimitTile label="Cameras per activation" limit={plan.cameras} />
            <LimitTile label="Agents" limit={plan.agents} />
            <LimitTile label="Integrations" limit={plan.integrations} />
            <LimitTile label="Ask questions" limit={plan.asks} />
          </div>
          {plan.asks.note && (
            <p className="text-xs text-text-muted mt-4 leading-relaxed">
              {plan.asks.note}
            </p>
          )}
        </Panel>
      )}
    </>
  );
}

/**
 * One limit.
 *
 * "no limit set" rather than "unlimited": for most of these the tier states no
 * number, which is a silence in the pricing sheet and not a generous term. The
 * distinction is `describeLimit`'s, kept out of here so it can be tested.
 */
function LimitTile({ label, limit }: { label: string; limit: PlanLimit }) {
  const { text, atCap } = describeLimit(limit);
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.16em] text-text-muted mb-1">
        {label}
      </div>
      <div
        className={`text-lg font-semibold tabular-nums ${
          atCap ? "text-accent-action" : ""
        }`}
      >
        {text}
      </div>
    </div>
  );
}

/**
 * Cameras that are not seeing what they were seeing.
 *
 * The third section on this screen and the odd one out: the other two are
 * queues, with a thing to do per row. This one has no button, because there is
 * no action a browser can take on a lens that has fogged. What it owes the
 * operator is the numbers — go and look at that camera, and here is why.
 *
 * Which is also why every row shows `observed` against `baseline` rather than
 * the severity alone. `event-bus-spec.md` §3 pins both onto the event so that
 * "the event states the comparison it is making instead of asserting a verdict
 * someone later cannot check", and rendering only the verdict would spend that
 * on nothing. An operator who can see 0.55 against 0.90 can disagree with the
 * threshold; one who can only see "critical" can only believe it or not.
 */
function DriftSection() {
  const { status, items, detail, refresh } = useDriftEvents();

  // Same reasoning as the stranded section: no drift is the common case and it
  // needs no reassurance of its own. A healthy screen should not look like
  // three things to check.
  if (status === "loading" || (status === "ready" && !items.length)) return null;

  const cameras = new Set(items.map((item) => item.cameraId)).size;

  return (
    <section className="space-y-6 pt-4">
      <div className="flex items-end justify-between gap-4 flex-wrap border-t border-border-hairline pt-6">
        <div>
          <Pill variant="warn" className="mb-2">
            <Camera size={11} />
            {`${cameras} ${cameras === 1 ? "camera" : "cameras"} drifting`}
          </Pill>
          <h2 className="text-xl font-semibold tracking-tight">
            Cameras seeing less than they were
          </h2>
          <p className="text-sm text-text-secondary mt-1 max-w-2xl leading-relaxed">
            Measured against each camera&rsquo;s own first ten minutes of this
            activation, not against a fixed number — cameras differ, and a floor
            an operator has to keep re-tuning is a floor nobody tunes. A quiet
            room reads as nothing here rather than as a fault: these are
            per-detection statistics, so an empty booth contributes no samples.
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          icon={<RefreshCw size={14} />}
          onClick={() => void refresh()}
        >
          Refresh
        </Button>
      </div>

      {(status === "offline" || status === "error") && (
        <EmptyState
          variant="page"
          icon={<Info size={20} />}
          title={
            status === "offline"
              ? "No backend configured"
              : "Could not read the telemetry"
          }
          hint={detail ?? undefined}
        />
      )}

      {status === "ready" &&
        items.map((item) => (
          <DriftItem key={`${item.seq}`} item={item} />
        ))}
    </section>
  );
}

function DriftItem({ item }: { item: DriftFinding }) {
  const change = drift(item);
  const label = METRIC_LABELS[item.metric] ?? item.metric;
  const minutes = item.windowSeconds ? Math.round(item.windowSeconds / 60) : null;

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <Pill variant={item.severity === "critical" ? "alert" : "warn"}>
            {item.severity === "critical" ? "Critical" : "Warning"}
          </Pill>
          {item.cameraId} · {label}
        </span>
      }
      subtitle={`${item.sessionId} · ${new Date(item.occurredAt).toLocaleString()}${
        minutes ? ` · ${minutes}-minute window` : ""
      }`}
    >
      <div className="space-y-3">
        {/* The comparison, not the conclusion. Both numbers at the same size,
            because the whole claim is the relationship between them. */}
        <div className="flex items-baseline gap-6 flex-wrap">
          <div>
            <div className="text-xs text-text-muted">Now</div>
            <div className="text-xl font-semibold tabular-nums">{item.observed}</div>
          </div>
          <div>
            <div className="text-xs text-text-muted">Baseline</div>
            <div className="text-xl font-semibold tabular-nums text-text-secondary">
              {item.baseline}
            </div>
          </div>
          <div>
            <div className="text-xs text-text-muted">Change</div>
            <div className="text-xl font-semibold tabular-nums">
              {change > 0 ? "+" : ""}
              {Math.round(change * 100)}%
            </div>
          </div>
        </div>

        <p className="text-sm text-text-secondary leading-relaxed">
          {item.metric === "confidence_mean"
            ? "The model is less sure about the people it is finding than it was at the start of this activation. Usually the scene changed rather than the model: a light moved, a lens fogged, or something got hung in front of the camera."
            : item.metric === "track_length"
              ? "More visits are ending because the tracker lost the person than were at the start. Dwell measured through this camera is a lower bound while that holds, and the report will under-count."
              : "This measurement has moved away from where it started for this camera."}
        </p>

        <p className="text-xs text-text-muted leading-relaxed">
          Nothing to click. Go and look at {item.cameraId}; if the scene has
          genuinely changed, recalibrating resets the baseline so this stops
          reporting the new normal as a fault.
        </p>
      </div>
    </Panel>
  );
}

/**
 * Dispatches whose outcome nobody knows.
 *
 * Kept as its own section rather than mixed into the queue above, because the
 * two ask different things of the reader. A failed event asks "can this be
 * retried?"; these ask "go and look in the channel, then tell us." Same screen,
 * because it is the same job — but an operator should never be one careless
 * click away from treating an unknown as a failure.
 */
function StrandedSection() {
  const { status, items, detail, refresh, resolve } = useStrandedDispatches();
  const [busy, setBusy] = useState<number | null>(null);
  const [outcome, setOutcome] = useState<Record<number, string>>({});

  // Nothing stranded is the overwhelmingly common case, and it needs no
  // reassurance of its own — the "nothing has failed" empty above already says
  // the queue is being read. Rendering a second empty panel next to it would
  // make a healthy screen look like two things to check.
  if (status === "loading" || (status === "ready" && !items.length)) return null;

  async function decide(id: number, verdict: Verdict, note: string) {
    setBusy(id);
    const result = await resolve(id, verdict, note);
    setBusy(null);
    setOutcome((prev) => ({
      ...prev,
      [id]: "error" in result ? result.error : result.effect,
    }));
  }

  return (
    <section className="space-y-6 pt-4">
      <div className="flex items-end justify-between gap-4 flex-wrap border-t border-border-hairline pt-6">
        <div>
          <Pill variant="warn" className="mb-2">
            <HelpCircle size={11} />
            {items.length
              ? `${items.length} unknown ${items.length === 1 ? "outcome" : "outcomes"}`
              : "Unknown outcomes"}
          </Pill>
          <h2 className="text-xl font-semibold tracking-tight">
            Actions nobody can confirm
          </h2>
          <p className="text-sm text-text-secondary mt-1 max-w-2xl leading-relaxed">
            The rule fired and the action was claimed, but the process stopped
            before the call came back. Nothing here failed — we simply cannot tell
            from this side whether the message arrived. Until somebody says, the
            same firing can never be acted on again.
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          icon={<RefreshCw size={14} />}
          onClick={() => void refresh()}
        >
          Refresh
        </Button>
      </div>

      {(status === "offline" || status === "error") && (
        <EmptyState
          variant="page"
          icon={<Info size={20} />}
          title={
            status === "offline"
              ? "No backend configured"
              : "Could not read the dispatches"
          }
          hint={detail ?? undefined}
        />
      )}

      {status === "ready" &&
        items.map((item) => (
          <StrandedItem
            key={item.id}
            item={item}
            busy={busy === item.id}
            outcome={outcome[item.id]}
            onDecide={(verdict, note) => void decide(item.id, verdict, note)}
          />
        ))}
    </section>
  );
}

function StrandedItem({
  item,
  busy,
  outcome,
  onDecide,
}: {
  item: StrandedDispatch;
  busy: boolean;
  outcome?: string;
  onDecide: (verdict: Verdict, note: string) => void;
}) {
  const [note, setNote] = useState("");
  const stuckFor = formatDuration(item.strandedForSeconds);
  const isHandoff = item.kind === "handoff";
  const isRetract = item.kind === "retract";
  // Only a rule row has a rule behind it. The other two carry a session id in
  // `ruleId`, so treating them as rules would show "this rule was deleted"
  // about something that never was one.
  const isRule = !isHandoff && !isRetract;

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          {/* Which kind, first and unmissable: a lead nobody can account for, a
              withdrawal nobody can account for, and a Slack post nobody can
              account for want different people and different urgencies. */}
          <Pill variant={isRetract ? "warn" : isHandoff ? "violet" : "info"}>
            {isRetract ? "Withdrawal" : isHandoff ? "Lead" : "Rule"}
          </Pill>
          {isRule
            ? item.ruleName ?? item.ruleId
            : `${isRetract ? "CRM retraction" : "Lead handoff"} · ${item.ruleId}`}
        </span>
      }
      subtitle={`${item.actionType} · claimed ${stuckFor} ago · ${new Date(
        item.createdAt
      ).toLocaleString()}`}
    >
      <div className="space-y-3">
        {isRule && !item.ruleName && (
          <p className="text-xs text-text-muted leading-relaxed">
            The rule <code>{item.ruleId}</code> has since been deleted. The
            dispatch still has to be answered for — a deleted rule does not
            un-send a message.
          </p>
        )}

        <p className="text-sm text-text-secondary leading-relaxed">
          {isRetract
            ? "Check the CRM for this contact, then say what you found. This is somebody who withdrew their consent and whose record may still be in a client's system — it is the most urgent thing on this screen, and the only one with a deadline outside our control."
            : isHandoff
              ? "Check whether this lead reached its destination, then say what you found. Answering “it arrived” keeps the claim, so a replay cannot send the same lead a second time."
              : "Check the destination for this action, then say what you found. There is no retry here on purpose: re-sending something that may already have arrived is the duplicate this whole mechanism exists to prevent."}
        </p>

        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="How you know — e.g. “found it in #ops at 14:32”"
          className="w-full text-sm bg-bg-canvas border border-border-hairline rounded-lg px-3 py-2 placeholder:text-text-muted"
        />

        <div className="flex items-center gap-2 flex-wrap">
          <Button
            variant="secondary"
            size="sm"
            icon={<Check size={14} />}
            disabled={busy}
            onClick={() => onDecide("delivered", note)}
          >
            {busy ? "Working…" : "It arrived"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            icon={<X size={14} />}
            disabled={busy}
            onClick={() => onDecide("failed", note)}
          >
            It never arrived
          </Button>
        </div>

        {outcome !== undefined && (
          <p className="text-xs text-text-secondary leading-relaxed">{outcome}</p>
        )}
      </div>
    </Panel>
  );
}

/** Rounded, and never more precise than it is useful — "4 minutes", not "247s". */
function formatDuration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes} min`;
  return `${Math.round(minutes / 60)} h`;
}

function QueueItem({
  item,
  busy,
  outcome,
  onRetry,
  onDismiss,
}: {
  item: DeadLetter;
  busy: boolean;
  outcome?: string;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  return (
    <Panel
      title={item.eventType ?? `event #${item.eventSeq}`}
      subtitle={`${item.consumer} · ${item.attempts} attempts · ${new Date(
        item.createdAt
      ).toLocaleString()}`}
      action={
        <div className="flex items-center gap-2">
          {/* Disabled with the reason attached rather than hidden: an operator
              who cannot see the button wonders whether the screen is broken. */}
          <Button
            variant="secondary"
            size="sm"
            icon={<RotateCcw size={14} />}
            disabled={!item.retryable || busy}
            title={item.retryBlockedReason ?? undefined}
            onClick={onRetry}
          >
            {busy ? "Working…" : "Retry"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            icon={<X size={14} />}
            disabled={busy}
            onClick={onDismiss}
          >
            Dismiss
          </Button>
        </div>
      }
    >
      <div className="space-y-3">
        {item.eventPayload && (
          <pre className="text-xs text-text-secondary bg-bg-canvas border border-border-hairline rounded-lg p-3 overflow-x-auto">
            {JSON.stringify(item.eventPayload, null, 2)}
          </pre>
        )}

        <div>
          <div className="text-[10px] uppercase tracking-[0.16em] text-text-muted mb-1">
            Why it failed
          </div>
          <pre className="text-xs text-accent-red/90 whitespace-pre-wrap font-mono leading-relaxed max-h-[160px] overflow-y-auto">
            {item.error}
          </pre>
        </div>

        {!item.retryable && item.retryBlockedReason && (
          <p className="text-xs text-text-muted flex items-start gap-2 leading-relaxed">
            <Info size={13} className="mt-0.5 shrink-0" />
            <span>{item.retryBlockedReason}</span>
          </p>
        )}

        {outcome !== undefined && (
          <p
            className={`text-xs leading-relaxed ${
              outcome ? "text-accent-red" : "text-accent"
            }`}
          >
            {outcome ? `Retried and still failing — ${outcome}` : "Resolved."}
          </p>
        )}
      </div>
    </Panel>
  );
}
