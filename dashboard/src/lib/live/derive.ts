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

/**
 * People currently inside a zone: an entry with no matching exit.
 *
 * Counts *people*, not zone occupancies. Somebody who walks from the entrance to
 * the product wall has an unmatched enter for the second zone and a matched pair
 * for the first — they are one person present, not two, and the whole point of
 * the figure is that an operator can compare it with what they see on the floor.
 *
 * A person who left and came back is present again: the last event for them
 * decides, which is why this walks the log in order rather than counting.
 */
export function presentNow(events: RealmEvent[]): number {
  const inZone = new Set<string>();

  for (const e of events) {
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
