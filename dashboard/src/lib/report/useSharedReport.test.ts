/**
 * The shared report's event boundary.
 *
 * Written after the bug it describes. The first version of `useSharedReport`
 * fetched the share endpoint and handed the payloads straight to
 * `computeScorecard` — and the shared report rendered **1 visitor where there
 * were 6, with `NaN` for average dwell**. The bus pins snake_case for the
 * Python producers, this app's contract is camelCase, and skipping the
 * translation is exactly the failure `lib/bus/wire.ts` exists to prevent.
 *
 * It was caught by opening the page, which is the argument for opening the
 * page. This pins it so the next reader is not a person.
 */

import { describe, expect, it } from "vitest";

import { validatePayload } from "@/lib/contracts";
import { computeScorecard } from "@/lib/roi/scorecard";
import { eventFromWire, type WireEvent } from "@/lib/bus";
import type { RealmEvent } from "@/lib/contracts";

const ZONES = [
  { id: "z_entry", name: "Entry", kind: "entry" as const, weight: 1 },
  { id: "z_pod", name: "Product Pod", kind: "experience" as const, weight: 3 },
];

/** Exactly what `GET /v1/share/{token}/events` returns: spec-shaped payloads. */
function wire(over: Partial<WireEvent>): WireEvent {
  return {
    seq: 1,
    eventId: "e-1",
    tenantId: "t_test",
    sessionId: "s_link",
    type: "spatial.dwell",
    payload: {},
    occurredAt: 1_787_990_400_000,
    recordedAt: 1_787_990_400_000,
    ...over,
  } as WireEvent;
}

/** The seeded floor from the live check: 6 in, 4 of them to the pod. */
function sharedFeed(): WireEvent[] {
  const out: WireEvent[] = [];
  let seq = 0;
  for (let i = 0; i < 6; i++) {
    const anon = `P-${String(i).padStart(3, "0")}`;
    out.push(
      wire({
        seq: ++seq,
        eventId: `enter-entry-${i}`,
        type: "spatial.zone_enter",
        payload: { anon_id: anon, zone_id: "z_entry" },
      }),
      wire({
        seq: ++seq,
        eventId: `dwell-entry-${i}`,
        type: "spatial.dwell",
        payload: { anon_id: anon, zone_id: "z_entry", duration: 45 },
      })
    );
    if (i < 4) {
      out.push(
        wire({
          seq: ++seq,
          eventId: `enter-pod-${i}`,
          type: "spatial.zone_enter",
          payload: { anon_id: anon, zone_id: "z_pod" },
        }),
        wire({
          seq: ++seq,
          eventId: `dwell-pod-${i}`,
          type: "spatial.dwell",
          payload: { anon_id: anon, zone_id: "z_pod", duration: 95 },
        })
      );
    }
  }
  return out;
}

/** What the hook does to a page of share events. */
function readShared(batch: WireEvent[]): RealmEvent[] {
  const out: RealmEvent[] = [];
  for (const w of batch) {
    const translated = eventFromWire(w);
    if (!validatePayload(w.type, translated.payload).ok) continue;
    out.push({
      ...translated,
      seq: w.seq,
      eventId: w.eventId,
      occurredAt: w.occurredAt,
      recordedAt: w.recordedAt,
    } as RealmEvent);
  }
  return out;
}

describe("a shared report's figures", () => {
  it("matches what the operator sees", () => {
    const card = computeScorecard(readShared(sharedFeed()), {
      zones: ZONES,
      engagedThresholdSec: 60,
      activationCost: 5000,
    });

    expect(card.reach.uniqueVisitors).toBe(6);
    expect(card.reach.entries).toBe(6);
    // Four visitors cleared the 60s threshold, in the pod.
    expect(card.engagement.engagementRate).toBeCloseTo(0.667, 3);
    // (6 × 45 + 4 × 95) / 10
    expect(card.engagement.avgDwellSec).toBe(65);
    // 6 × 45 × 1 + 4 × 95 × 3
    expect(card.engagement.dwellWeightedAttention).toBe(1410);
    expect(card.pipeline.costPerEngagedVisit).toBe(1250);
  });

  it("is what the untranslated version got wrong", () => {
    // The bug, pinned. Handing the wire payloads straight to the scorecard
    // gives one phantom visitor — `persons.add(undefined)` — and NaN, which is
    // what the page actually rendered before the translation was added.
    const raw = sharedFeed().map((w, i) => ({
      ...w,
      seq: i + 1,
    })) as unknown as RealmEvent[];

    const broken = computeScorecard(raw, { zones: ZONES, engagedThresholdSec: 60 });

    expect(broken.reach.uniqueVisitors).toBe(1);
    expect(Number.isNaN(broken.engagement.avgDwellSec)).toBe(true);
  });

  it("drops an event no reader could use rather than averaging it", () => {
    const feed = [
      ...sharedFeed(),
      wire({ seq: 99, eventId: "bad", type: "spatial.dwell", payload: { anon_id: "P-X" } }),
    ];

    const card = computeScorecard(readShared(feed), {
      zones: ZONES,
      engagedThresholdSec: 60,
    });

    expect(card.reach.uniqueVisitors).toBe(6);
    expect(card.engagement.avgDwellSec).toBe(65);
  });
});
