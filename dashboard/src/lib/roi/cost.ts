/**
 * realmspace — what this session has spent, from the log.
 *
 * `roadmap.md` Phase 3: "cost telemetry — `cost.metered` emitters on anything
 * that costs money + cost tile on /live — unit economics from real data."
 *
 * ## This is not the activation cost
 *
 * `Session.measurement.activationCost` is the fixed price of the stand, and the
 * scorecard already divides by it for CPEV and ROI (`roi-framework.md` §2). What
 * is counted here is the *variable* cost the system itself incurs while running —
 * LLM tokens for a question asked of Ask, an action unit for a rule that posted
 * to Slack, an enrichment credit spent on a lead. Two different numbers with two
 * different owners, and adding them would misstate both.
 *
 * ## Why units are kept apart
 *
 * A `cost.metered` amount is in whatever `unit` says: tokens, credits, or a
 * currency. Summing across units gives a figure that reads like money and is
 * not, so lines are grouped by (kind, unit) and only entries whose unit is a
 * currency code contribute to a money total. If two currencies appear, there is
 * no honest single total and the summary says so rather than picking one.
 */

import type { CostMeteredPayload, RealmEvent } from "@/lib/contracts";

/** One (kind, unit) pair and what it has run to. */
export interface CostLine {
  kind: string;
  unit: string;
  amount: number;
  /** How many metered events make up this line. */
  events: number;
}

export interface CostSummary {
  /** True once anything at all has been metered for this session. */
  metered: boolean;
  /** Every (kind, unit) pair, largest first. */
  lines: CostLine[];
  /**
   * The money total, when there is an honest one: some spend arrived already
   * denominated in a currency, and only one currency appeared.
   */
  money: { currency: string; amount: number } | null;
  /**
   * Money spent per engaged visitor — the unit-economics figure the phase asks
   * for. Null unless there is a money total *and* somebody engaged; dividing by
   * zero visitors would print Infinity next to a currency symbol.
   */
  perEngagedVisit: number | null;
}

/** ISO 4217 codes are three letters. Tokens and credits are not. */
function isCurrency(unit: string): boolean {
  return /^[A-Z]{3}$/.test(unit);
}

export function summarizeCost(
  events: RealmEvent[],
  opts: { engagedVisitors?: number } = {}
): CostSummary {
  const byLine = new Map<string, CostLine>();
  const byCurrency = new Map<string, number>();

  for (const e of events) {
    if (e.type !== "cost.metered") continue;
    const p = e.payload as CostMeteredPayload;

    // A malformed meter reading is skipped rather than coerced. NaN propagates
    // silently through every sum it touches, and the resulting "—" on the tile
    // is indistinguishable from having spent nothing.
    const amount = typeof p?.amount === "number" ? p.amount : NaN;
    if (!Number.isFinite(amount)) continue;

    const kind = p.kind ?? "other";
    const unit = p.unit ?? "";
    if (!unit) continue;

    const key = `${kind}${unit}`;
    const line = byLine.get(key) ?? { kind, unit, amount: 0, events: 0 };
    line.amount += amount;
    line.events += 1;
    byLine.set(key, line);

    if (isCurrency(unit)) {
      byCurrency.set(unit, (byCurrency.get(unit) ?? 0) + amount);
    }
  }

  const lines = [...byLine.values()].sort((a, b) => b.amount - a.amount);

  const currencies = [...byCurrency.entries()];
  const money =
    currencies.length === 1
      ? { currency: currencies[0][0], amount: currencies[0][1] }
      : null;

  const engaged = opts.engagedVisitors ?? 0;
  const perEngagedVisit =
    money && engaged > 0 ? money.amount / engaged : null;

  return { metered: lines.length > 0, lines, money, perEngagedVisit };
}
