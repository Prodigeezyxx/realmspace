"use client";

/**
 * realmspace — what running this session has cost the system.
 *
 * `roadmap.md` Phase 3 asks for a cost tile on `/live` so unit economics come
 * from real data. The figures here are **variable** cost the system meters as it
 * works — LLM tokens for a question asked of Ask, an action unit for a rule that
 * posted to Slack, an enrichment credit spent on a lead. The fixed activation
 * cost is the ROI tile's business (`LiveRoiTile`, `Cost / engaged`), and the two
 * are deliberately not added together.
 *
 * ## Why it says "nothing meters cost yet"
 *
 * Nothing on this track spends money at the moment: Ask is not wired to a
 * provider and the rule dispatchers are not built. An empty tile that read
 * "$0.00" would be a claim — that the session was free — and the honest state is
 * that no meter has reported. The distinction is the same one the report makes
 * about revenue it cannot see.
 */

import { Coins } from "lucide-react";

import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import type { CostSummary } from "@/lib/roi/cost";

function money(amount: number, currency: string): string {
  try {
    return amount.toLocaleString(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    });
  } catch {
    // An unrecognised code is still worth showing — with the code, so nobody
    // reads a bare number as their own currency.
    return `${amount.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${currency}`;
  }
}

function unitAmount(amount: number, unit: string): string {
  return `${amount.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${unit}`;
}

/** `llm_tokens` → `LLM tokens`. The kinds are contract values, not copy. */
function kindLabel(kind: string): string {
  const words = kind.replace(/_/g, " ");
  return words.startsWith("llm") ? `LLM${words.slice(3)}` : words;
}

export function CostTile({ cost }: { cost: CostSummary }) {
  return (
    <Panel
      title="Cost so far · metered"
      subtitle="What the system spent running this session — tokens, action units, credits. Not the activation cost."
      action={
        <Pill variant={cost.metered ? "success" : "neutral"}>
          {cost.metered
            ? `${cost.lines.reduce((n, l) => n + l.events, 0)} readings`
            : "no meter yet"}
        </Pill>
      }
    >
      {cost.metered ? (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-x-10 gap-y-3">
            <Figure
              label="Metered spend"
              value={cost.money ? money(cost.money.amount, cost.money.currency) : "—"}
              note={
                cost.money
                  ? undefined
                  : "no single currency total — see the lines below"
              }
            />
            <Figure
              label="Per engaged visitor"
              value={
                cost.perEngagedVisit != null && cost.money
                  ? money(cost.perEngagedVisit, cost.money.currency)
                  : "—"
              }
              note={
                cost.perEngagedVisit == null
                  ? "needs a currency total and at least one engaged visitor"
                  : undefined
              }
            />
          </div>

          <ul className="flex flex-col gap-1.5 border-t border-border-subtle pt-3">
            {cost.lines.map((line) => (
              <li
                key={`${line.kind}-${line.unit}`}
                className="flex items-baseline justify-between gap-4 text-sm"
              >
                <span className="text-text-secondary capitalize">
                  {kindLabel(line.kind)}
                </span>
                <span className="tabular-nums text-text-primary">
                  {unitAmount(line.amount, line.unit)}
                  <span className="text-text-muted"> · {line.events}×</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="flex items-start gap-3">
          <Coins size={16} className="mt-0.5 shrink-0 text-text-muted" />
          <p className="text-sm text-text-secondary leading-relaxed">
            Nothing has metered a cost for this session. That is not the same as
            a free session — no spender is wired up yet. The first readings will
            come from Ask&apos;s LLM calls and from rule actions, both of which
            meter through <code>cost.metered</code> on the bus.
          </p>
        </div>
      )}
    </Panel>
  );
}

function Figure({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs uppercase tracking-wide text-text-muted">
        {label}
      </span>
      <span className="text-xl tabular-nums text-text-primary">{value}</span>
      {note ? <span className="text-xs text-text-muted">{note}</span> : null}
    </div>
  );
}
