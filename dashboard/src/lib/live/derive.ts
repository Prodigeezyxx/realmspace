/**
 * realmspace — the live view's derived figures.
 *
 * Almost everything the live screen shows is already `computeScorecard`'s job,
 * and it stays that way: one definition of engagement rate, dwell and reach for
 * the whole product, so the tile above the fold and the report a client reads
 * the next morning cannot disagree.
 *
 * What the scorecard cannot answer is "how many people are in the room *right
 * now*", because that is not a property of a finished session — it is a property
 * of an unfinished one. That question lives here.
 */

import type { RealmEvent, ZoneMovePayload } from "@/lib/contracts";
import type { StaffPromptPayload } from "@/lib/contracts/rules";

/**
 * Zone transitions, oldest first by **event time**.
 *
 * Everything below decides who is where by replaying enters and exits, and that
 * only gives the right answer in the order things happened. A producer that
 * appends one visitor's whole journey at a time, or a batch replayed after an
 * outage, arrives in a different order from the one it happened in — which is
 * the 2026-08-25 walk's defect 7, where the scorecard read 1 where three
 * visitors overlapped. `consumers/occupancy.py` walks the same events the same
 * way, for the same reason.
 */
function transitionsInOrder(events: RealmEvent[]): RealmEvent[] {
  return events
    .filter(
      (e) => e.type === "spatial.zone_enter" || e.type === "spatial.zone_exit"
    )
    .sort((a, b) => a.occurredAt - b.occurredAt);
}

/**
 * People currently inside a zone: an entry with no matching exit.
 *
 * Counts *people*, not zone occupancies. Somebody who walks from the entrance to
 * the product wall has an unmatched enter for the second zone and a matched pair
 * for the first — they are one person present, not two, and the whole point of
 * the figure is that an operator can compare it with what they see on the floor.
 *
 * A person who left and came back is present again: the last event for them
 * decides, which is why this replays the transitions in event-time order rather
 * than counting.
 */
export function presentNow(events: RealmEvent[]): number {
  const inZone = new Set<string>();

  for (const e of transitionsInOrder(events)) {
    if (e.type === "spatial.zone_enter") {
      const p = e.payload as ZoneMovePayload;
      if (p.anonId) inZone.add(p.anonId);
    } else if (e.type === "spatial.zone_exit") {
      const p = e.payload as ZoneMovePayload;
      // An exit removes them outright rather than decrementing a per-zone
      // count. Moving between zones is exit-then-enter, so the enter that
      // follows immediately puts them back — and a dropout or session-end exit,
      // which has no following enter, correctly leaves them gone.
      if (p.anonId) inZone.delete(p.anonId);
    }
  }

  return inZone.size;
}

/**
 * How many people are in each zone right now, keyed by zone id.
 *
 * The per-zone twin of `presentNow`, and it has to count differently: a person
 * is in exactly one place there and is *removed* by any exit, while here an exit
 * only empties the zone it names. Somebody moving from the entrance to the
 * product wall is one person present and shows in one zone, because the tracker
 * emits the exit before the enter.
 *
 * Zones with nobody in them are **absent from the map rather than zero**, which
 * is the distinction `ZoneList` needs: a zone nobody has walked into yet and a
 * zone the log knows nothing about are the same on screen, and neither should be
 * confused with a zone this browser has no events for at all.
 */
export function zoneOccupancy(events: RealmEvent[]): Map<string, number> {
  const inside = new Map<string, Set<string>>();

  for (const e of transitionsInOrder(events)) {
    const p = e.payload as unknown as ZoneMovePayload;
    if (!p.anonId || !p.zoneId) continue;
    const occupants = inside.get(p.zoneId) ?? new Set<string>();
    if (e.type === "spatial.zone_enter") occupants.add(p.anonId);
    else occupants.delete(p.anonId);
    inside.set(p.zoneId, occupants);
  }

  const counts = new Map<string, number>();
  for (const [zoneId, occupants] of inside) {
    if (occupants.size > 0) counts.set(zoneId, occupants.size);
  }
  return counts;
}

/**
 * When the session's log last saw anything, or null for an empty log.
 *
 * The live screen uses it to say how stale it is. A feed that quietly stopped
 * ten minutes ago looks exactly like a quiet room, and the operator watching it
 * is the person least able to tell the difference.
 */
export function lastEventAt(events: RealmEvent[]): number | null {
  let latest: number | null = null;
  for (const e of events) {
    if (latest === null || e.occurredAt > latest) latest = e.occurredAt;
  }
  return latest;
}

export interface StaffPrompt extends StaffPromptPayload {
  at: number;
  eventId: string;
}

/**
 * How long a prompt is worth showing, in ms of event time.
 *
 * Phase 3 gives the whole path a `< 3s` budget from event to action, which is
 * about a prompt being *useful* — "greet the group at the entrance" is worth
 * nothing after they have moved on. The same logic decides when to stop showing
 * it: a prompt still on screen twenty minutes later is not information, it is
 * furniture, and it teaches the floor staff to ignore the panel.
 *
 * Five minutes is a judgement, not a derivation, and it is the one number here
 * that an operator might reasonably want to change.
 */
export const PROMPT_TTL_MS = 5 * 60_000;

/**
 * Staff prompts a rule raised recently, newest first.
 *
 * Read off `rule.staff_prompt`, which the edge dispatcher appends and the
 * broadcast consumer carries here. Deliberately *not* derived from `rule.fired`
 * by checking whether its action happens to be a staff prompt: that would put a
 * fourth implementation of the rule spec in the browser, which is the
 * split-brain ADR-002 exists to end. This function knows nothing about rules
 * beyond the shape of one event.
 *
 * `now` is a parameter rather than `Date.now()` so the same log renders the same
 * panel in a replay as it did live.
 */
export function staffPrompts(events: RealmEvent[], now: number): StaffPrompt[] {
  const prompts: StaffPrompt[] = [];

  for (const e of events) {
    if (e.type !== "rule.staff_prompt") continue;
    if (now - e.occurredAt > PROMPT_TTL_MS) continue;
    const p = e.payload as unknown as StaffPromptPayload;
    prompts.push({ ...p, at: e.occurredAt, eventId: e.eventId });
  }

  return prompts.sort((a, b) => b.at - a.at);
}

/**
 * An insight the backend generated, as `/live` shows it.
 *
 * Read from the durable log like everything else on that page. What it replaces
 * was three hardcoded strings ("Scent Quiz dwell 2.4× longer", "Bottle Wall
 * captures 86% of gazes") shown only in demo mode, beneath a subtitle promising
 * "the AI surfaces a fresh round every 10 minutes" about a thing that did not
 * exist.
 */
export interface LiveInsight {
  /** The log position, and what a click-through is fetched by. */
  seq: number;
  text: string;
  /** The events the claim rests on — `insight.generated`'s whole contract. */
  refs: { seq: number; event_id: string }[];
  /** End of the window it describes, in ms epoch. */
  at: number;
  windowMinutes: number;
  /** `deterministic` when no AI provider is configured. Always rendered. */
  basis: string;
  /** True when the window outran the digest's bound — a partial view, said so. */
  truncated: boolean;
}

/**
 * Insights for this session, newest first.
 *
 * No TTL, unlike `staffPrompts`. A staff prompt is an instruction that goes
 * stale — "a prompt still shown twenty minutes later is furniture" — while an
 * insight is an observation about a window that already closed, and stays true.
 */
export function liveInsights(events: RealmEvent[]): LiveInsight[] {
  const insights: LiveInsight[] = [];

  for (const e of events) {
    if (e.type !== "insight.generated") continue;
    const p = e.payload as unknown as {
      text?: string;
      refs?: { seq: number; event_id: string }[];
      basis?: string;
      truncated?: boolean;
      window?: { minutes?: number };
    };
    insights.push({
      seq: (e as unknown as { seq: number }).seq ?? 0,
      text: String(p.text ?? ""),
      refs: p.refs ?? [],
      at: e.occurredAt,
      windowMinutes: p.window?.minutes ?? 0,
      basis: String(p.basis ?? "deterministic"),
      truncated: Boolean(p.truncated),
    });
  }

  return insights.sort((a, b) => b.at - a.at);
}
