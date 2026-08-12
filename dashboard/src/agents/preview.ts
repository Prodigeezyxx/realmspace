/**
 * realmspace — the dry run an operator sees before saving a rule.
 *
 * ADR-002: "The same document has to run on the edge and in the browser preview.
 * Two implementations of a rule *language* stay in sync for about a week; two
 * runners of one *document* diverge only if the language changes."
 *
 * This is the browser's runner. It takes a `RuleDocument` — the same JSON the
 * backend stores and the edge evaluates — and replays it over events already in
 * this browser's log to answer one question: *how often would this have fired?*
 *
 * ## What this is not allowed to do
 *
 * Dispatch anything. Not a Slack post, not a webhook, not a screen swap. "Nothing
 * in the browser decides whether a rule fires in production" is the sentence
 * ADR-002 ends the split-brain with, and a preview that could act would quietly
 * reinstate it. The return value is a list of moments, for a human to read.
 *
 * ## Where it deliberately mirrors the edge, and where it cannot
 *
 * Mirrored, because these are the parts an operator would be misled by:
 *
 *  - the window is a time range over the log, not a running tally;
 *  - a threshold counts distinct people where the payload carries an `anonId`,
 *    so one restless visitor is not five;
 *  - cooldown is event time, so the preview of a busy minute shows the number of
 *    alerts the room would actually have produced.
 *
 * Not mirrored: `none` conditions. Judging an absence needs a boundary — the
 * next event past the window, or `session.ended` — and this browser's log is a
 * partial mirror of the bus, so an apparent silence here can simply be an event
 * that has not been mirrored yet. Previewing one would show quiet the room never
 * had. It returns `unsupported` and says so, rather than showing zero, which
 * would read as "this rule never fires".
 */

import type { RealmEvent } from "@/lib/contracts";
import type { RuleDocument } from "@/lib/contracts/rules";

export interface PreviewFiring {
  /** When the condition became true, by the edge clock. */
  at: number;
  observed: number;
  countedBy: "people" | "events";
  /** The event that would have triggered it, for "why this moment?". */
  triggerSeq: number;
}

export interface RulePreview {
  firings: PreviewFiring[];
  /** How many events were considered, so an empty result is legible. */
  considered: number;
  /** Set when this rule cannot be previewed here, with the reason. */
  unsupported: string | null;
}

const EMPTY: RulePreview = { firings: [], considered: 0, unsupported: null };

export function previewRule(
  rule: RuleDocument,
  events: RealmEvent[]
): RulePreview {
  if (rule.condition.type === "none") {
    return {
      ...EMPTY,
      unsupported:
        "a “nothing happened” rule is judged at a boundary on the edge — this " +
        "browser's log is a partial mirror, so a gap here may just be an event " +
        "that has not arrived",
    };
  }

  const relevant = events
    .filter((e) => e.type === rule.triggerType)
    .sort((a, b) => a.occurredAt - b.occurredAt);

  const scoped = relevant.filter((e) => inScope(rule, e));
  const firings: PreviewFiring[] = [];
  let lastFiredAt: number | null = null;

  for (const event of scoped) {
    const matched =
      rule.condition.type === "threshold"
        ? threshold(rule, scoped, event)
        : { observed: 1, countedBy: "events" as const };

    if (!matched) continue;

    // Event time, never Date.now() — the same reason the edge does it
    // (ADR-002 §2). A preview run over yesterday's log has to show yesterday's
    // firings, not a burst caused by the clock having moved since.
    if (
      lastFiredAt !== null &&
      event.occurredAt - lastFiredAt < rule.cooldownSec * 1000
    ) {
      continue;
    }
    lastFiredAt = event.occurredAt;
    firings.push({
      at: event.occurredAt,
      observed: matched.observed,
      countedBy: matched.countedBy,
      triggerSeq: event.seq,
    });
  }

  return { firings, considered: scoped.length, unsupported: null };
}

function threshold(
  rule: RuleDocument,
  scoped: RealmEvent[],
  event: RealmEvent
): { observed: number; countedBy: "people" | "events" } | null {
  if (rule.condition.type !== "threshold") return null;
  const since = event.occurredAt - rule.condition.windowSec * 1000;

  const window = scoped.filter(
    (e) => e.occurredAt > since && e.occurredAt <= event.occurredAt
  );
  const people = new Set(
    window
      .map((e) => (e.payload as Record<string, unknown>).anonId)
      .filter((id): id is string => typeof id === "string")
  );

  // Distinct people where the payload names one, events where it does not — a
  // `surface.interaction` from a booth with no tracker attached, say. Which was
  // used is reported, because it changes what the number means.
  const countedBy = people.size > 0 ? "people" : "events";
  const observed = people.size > 0 ? people.size : window.length;

  return observed >= rule.condition.count ? { observed, countedBy } : null;
}

function inScope(rule: RuleDocument, event: RealmEvent): boolean {
  const payload = event.payload as Record<string, unknown>;
  const zone = payload.zoneId;

  if (rule.triggerZoneId && zone !== rule.triggerZoneId) return false;
  const wantZone =
    rule.condition.type === "none" ? null : rule.condition.zoneId ?? null;
  if (wantZone && zone !== wantZone) return false;

  if (rule.condition.type === "threshold" && rule.condition.minDwellSec != null) {
    const duration = payload.durationSec ?? payload.duration;
    if (typeof duration !== "number" || duration < rule.condition.minDwellSec) {
      return false;
    }
  }
  return true;
}
