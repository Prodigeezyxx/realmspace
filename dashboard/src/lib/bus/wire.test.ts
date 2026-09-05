/**
 * The payload translation, as executable claims.
 *
 * These matter more than their size suggests. Every failure they guard against
 * is silent: the field simply arrives as `undefined`, the scorecard adds it to a
 * total, and the report shows a plausible wrong number instead of an error.
 * `roi-framework.md` §3 — "we never inflate" — is only true if this file is.
 */

import { describe, expect, it } from "vitest";

import { computeScorecard } from "@/lib/roi/scorecard";
import type { RealmEvent } from "@/lib/contracts";
import {
  eventFromWire,
  eventToWire,
  payloadFromWire,
  payloadToWire,
  type WireEvent,
} from "./wire";

describe("payloadFromWire", () => {
  it("renames dwell's duration to durationSec, which is what the scorecard reads", () => {
    const out = payloadFromWire("spatial.dwell", {
      anon_id: "P-1",
      zone_id: "z_a",
      duration: 92.5,
      started_at: "2026-08-03T10:00:00Z",
      exceeded_threshold: true,
    });

    expect(out).toEqual({
      anonId: "P-1",
      zoneId: "z_a",
      durationSec: 92.5,
      startedAt: "2026-08-03T10:00:00Z",
      exceededThreshold: true,
    });
    // The rename is a move, not a copy: leaving `duration` behind invites a
    // reader to use the one the contract does not declare.
    expect(out).not.toHaveProperty("duration");
  });

  it("accepts person_id as anon_id, because the spec says producers may send either", () => {
    const out = payloadFromWire("perception.detection", {
      person_id: "P-7",
      bbox: [1, 2, 3, 4],
      frame_width: 1280,
      frame_height: 720,
    });

    expect(out.anonId).toBe("P-7");
    expect(out.frameWidth).toBe(1280);
    expect(out.frameHeight).toBe(720);
  });

  it("does not let person_id overwrite an explicit anon_id", () => {
    const out = payloadFromWire("perception.detection", {
      anon_id: "canonical",
      person_id: "fallback",
    });
    expect(out.anonId).toBe("canonical");
  });

  it("camelizes a type it has never heard of, so the taxonomy stays additive", () => {
    // spatial.passby does not exist yet (roadmap P2). It must not need an entry
    // in a translation table to arrive intact.
    const out = payloadFromWire("spatial.passby", {
      anon_id: "P-2",
      adjacent_zone_id: "z_entry",
    });
    expect(out).toEqual({ anonId: "P-2", adjacentZoneId: "z_entry" });
  });

  it("recurses through nested objects and arrays", () => {
    const out = payloadFromWire("session.zones_updated", {
      zone_ids: ["z_a", "z_b"],
      zone_count: 2,
      nested: [{ inner_key: 1 }],
    });
    expect(out).toEqual({
      zoneIds: ["z_a", "z_b"],
      zoneCount: 2,
      nested: [{ innerKey: 1 }],
    });
  });

  it("translates the Phase 6 telemetry payloads the ops panel reads", () => {
    // Both types were pinned in event-bus-spec.md §3 during Phase 3 and had no
    // producer until Phase 6, so nothing had ever exercised the translation.
    // The failure it guards against is the Phase 2 one: `camera_id` arriving
    // untranslated renders as `undefined` beside two real numbers, and a panel
    // saying "undefined has drifted" is worse than an error.
    expect(
      payloadFromWire("drift.detected", {
        camera_id: "cam-1",
        metric: "confidence_mean",
        observed: 0.55,
        baseline: 0.9,
        window_seconds: 600,
        severity: "critical",
        window: { from: "2026-08-19T09:10:00+00:00", to: "2026-08-19T09:20:00+00:00" },
      })
    ).toEqual({
      cameraId: "cam-1",
      metric: "confidence_mean",
      observed: 0.55,
      baseline: 0.9,
      windowSeconds: 600,
      severity: "critical",
      window: { from: "2026-08-19T09:10:00+00:00", to: "2026-08-19T09:20:00+00:00" },
    });

    expect(
      payloadFromWire("calibration.updated", {
        camera_id: "cam-1",
        kind: "privacy_mask",
        revision: 2,
        by: "u_op",
        note: "tripod knocked",
        masked: true,
      })
    ).toEqual({
      cameraId: "cam-1",
      kind: "privacy_mask",
      revision: 2,
      by: "u_op",
      note: "tripod knocked",
      masked: true,
    });
  });

  it("translates spatial.group, whose producer arrived in Phase 6", () => {
    // The type has been in the taxonomy since Phase 1 with nothing emitting it,
    // so this translation had never been exercised. `group_id` and `member_anon_ids`
    // arriving untranslated is the Phase 2 failure again: undefined rendered
    // beside real numbers rather than an error.
    expect(
      payloadFromWire("spatial.group", {
        group_id: "g-1",
        members: ["P-001", "P-002", "P-003"],
        size: 3,
        cohesion: 0.94,
        zone_id: "z_lounge",
        status: "changed",
      })
    ).toEqual({
      groupId: "g-1",
      // Renamed: the spec pins `members`, this contract declares
      // `memberAnonIds`. Nothing had ever put an event between them.
      memberAnonIds: ["P-001", "P-002", "P-003"],
      size: 3,
      cohesion: 0.94,
      zoneId: "z_lounge",
      status: "changed",
    });
  });

  it("survives a null or non-object payload without throwing", () => {
    expect(payloadFromWire("spatial.dwell", null)).toEqual({});
    expect(payloadFromWire("spatial.dwell", "nonsense")).toEqual({});
  });
});

describe("payloadToWire", () => {
  it("is the inverse for the shapes we send", () => {
    const browser = { anonId: "P-1", zoneId: "z_a", durationSec: 30 };
    const wire = payloadToWire("spatial.dwell", browser);
    expect(wire).toEqual({ anon_id: "P-1", zone_id: "z_a", duration: 30 });
    expect(payloadFromWire("spatial.dwell", wire)).toEqual(browser);
  });

  it("snake-cases frame dimensions, which the backend requires by that name", () => {
    const wire = payloadToWire("perception.detection", {
      anonId: "P-1",
      frameWidth: 640,
      frameHeight: 480,
    });
    expect(wire).toEqual({
      anon_id: "P-1",
      frame_width: 640,
      frame_height: 480,
    });
  });
});

describe("eventFromWire", () => {
  const wire: WireEvent = {
    seq: 42,
    eventId: "e-1",
    tenantId: "t_test",
    sessionId: "s_1",
    type: "spatial.zone_enter",
    payload: { anon_id: "P-1", zone_id: "z_a", at: "2026-08-03T10:00:00Z" },
    occurredAt: 1_754_215_200_000,
    recordedAt: 1_754_215_200_100,
  };

  it("keeps the eventId, which is what makes the mirror idempotent", () => {
    expect(eventFromWire(wire).eventId).toBe("e-1");
  });

  it("drops the remote seq rather than mixing it into the local log's counter", () => {
    expect(eventFromWire(wire)).not.toHaveProperty("seq");
  });

  it("round-trips through eventToWire", () => {
    const local = { ...eventFromWire(wire), seq: 1, recordedAt: 0 } as RealmEvent;
    const back = eventToWire(local);
    expect(back.eventId).toBe("e-1");
    expect(back.payload).toEqual({
      anon_id: "P-1",
      zone_id: "z_a",
      at: "2026-08-03T10:00:00Z",
    });
  });
});

describe("the reason this module exists", () => {
  /** One dwell event, exactly as the backend tracker writes it. */
  const backendDwell: WireEvent = {
    seq: 1,
    eventId: "e-dwell",
    tenantId: "t_test",
    sessionId: "s_1",
    type: "spatial.dwell",
    payload: {
      anon_id: "P-1",
      zone_id: "z_a",
      duration: 120,
      started_at: "2026-08-03T10:00:00Z",
      ended_at: "2026-08-03T10:02:00Z",
      exceeded_threshold: true,
    },
    occurredAt: 1_754_215_200_000,
    recordedAt: 1_754_215_200_000,
  };

  function asEvent(payload: unknown): RealmEvent {
    return {
      seq: 1,
      eventId: "e-dwell",
      tenantId: "t_test",
      sessionId: "s_1",
      type: "spatial.dwell",
      payload: payload as never,
      occurredAt: 0,
      recordedAt: 0,
    };
  }

  it("translated, the scorecard sees the dwell", () => {
    const card = computeScorecard([
      asEvent(payloadFromWire("spatial.dwell", backendDwell.payload)),
    ]);

    expect(card.reach.uniqueVisitors).toBe(1);
    expect(card.engagement.avgDwellSec).toBe(120);
    expect(card.engagement.dwellWeightedAttention).toBe(120);
  });

  it("untranslated, it silently produces garbage — which is the whole point", () => {
    // Feeding the raw wire payload straight in, as would happen if the mirror
    // skipped translation. Nothing throws. That is precisely the danger.
    const card = computeScorecard([asEvent(backendDwell.payload)]);

    expect(Number.isNaN(card.engagement.avgDwellSec)).toBe(true);
    // A phantom visitor, counted from `undefined`.
    expect(card.reach.uniqueVisitors).toBe(1);
  });
});
