/**
 * realmspace — the report's derived views, computed from the event log.
 *
 * `computeScorecard` produces the four ROI layers. These are the rest of what
 * the report shows — the funnel, the zone table, surface exposure, the traffic
 * shape, the notable moments — kept out of the page component so each is a pure
 * function that can be tested against a known event list.
 *
 * All of it is derivation, never invention. Where a figure needs something the
 * log does not contain, these functions return an empty result and the page
 * says why, rather than substituting a plausible number.
 */

import type {
  GazePayload,
  DwellPayload,
  RealmEvent,
  SurfaceInteractionPayload,
  ZoneMovePayload,
} from "@/lib/contracts";

export interface FunnelStep {
  zoneId: string;
  name: string;
  visitors: number;
  /** % of the first step's visitors who reached this one. */
  share: number;
  /** % lost between the previous step and this one. */
  drop: number;
}

export interface ZoneRow {
  zoneId: string;
  name: string;
  color?: string;
  visitors: number;
  avgDwellSec: number;
  /** % of all visitors who reached this zone. */
  reachPct: number;
}

export interface GazeRow {
  zoneId: string;
  name: string;
  color?: string;
  /** People who held a look at this zone. */
  watchers: number;
  /** Of those, the ones who never entered it — the number this panel is for. */
  watchersWhoNeverEntered: number;
  /** Total held-look seconds, across everybody. */
  attentionSec: number;
}
export interface SurfaceRow {
  surfaceId: string;
  /** The operator's name for it, when the session config carries one. */
  label: string;
  interactions: number;
  /** % of the most-used surface's interactions. */
  share: number;
}

export interface Moment {
  at: number;
  title: string;
  detail: string;
}

interface ZoneMeta {
  id: string;
  name: string;
  color?: string;
  funnelOrder?: number | null;
}

/** Distinct visitors who dwelled in each zone, keyed by zone id. */
function visitorsByZone(events: RealmEvent[]): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>();
  for (const e of events) {
    if (e.type !== "spatial.dwell") continue;
    const p = e.payload as DwellPayload;
    if (!p.zoneId || !p.anonId) continue;
    if (!out.has(p.zoneId)) out.set(p.zoneId, new Set());
    out.get(p.zoneId)!.add(p.anonId);
  }
  return out;
}

/**
 * The funnel, in the order the operator declared at setup.
 *
 * Ordered by `funnelOrder` rather than by traffic, deliberately: the funnel is a
 * claim about the journey the activation was *designed* for
 * (`roi-framework.md` §5), and sorting it by visitor count would turn every
 * activation into a success by construction — each step would always be smaller
 * than the last.
 *
 * Returns empty when no zone carries a `funnelOrder`, because then there is no
 * declared journey to report against.
 */
export function buildFunnel(events: RealmEvent[], zones: ZoneMeta[]): FunnelStep[] {
  const ordered = zones
    .filter((z) => z.funnelOrder != null)
    .sort((a, b) => (a.funnelOrder ?? 0) - (b.funnelOrder ?? 0));
  if (!ordered.length) return [];

  const byZone = visitorsByZone(events);
  const first = byZone.get(ordered[0].id)?.size ?? 0;

  return ordered.map((z, i) => {
    const visitors = byZone.get(z.id)?.size ?? 0;
    const prev = i === 0 ? visitors : byZone.get(ordered[i - 1].id)?.size ?? 0;
    return {
      zoneId: z.id,
      name: z.name,
      visitors,
      share: first ? +((100 * visitors) / first).toFixed(1) : 0,
      drop: prev ? +((100 * (prev - visitors)) / prev).toFixed(1) : 0,
    };
  });
}

/** Per-zone visitors and average dwell, ordered by aggregate attention. */
export function buildZoneRows(events: RealmEvent[], zones: ZoneMeta[]): ZoneRow[] {
  const totals = new Map<string, { sum: number; count: number; people: Set<string> }>();

  for (const e of events) {
    if (e.type !== "spatial.dwell") continue;
    const p = e.payload as DwellPayload;
    if (!p.zoneId || typeof p.durationSec !== "number") continue;
    if (!totals.has(p.zoneId)) {
      totals.set(p.zoneId, { sum: 0, count: 0, people: new Set() });
    }
    const t = totals.get(p.zoneId)!;
    t.sum += p.durationSec;
    t.count++;
    if (p.anonId) t.people.add(p.anonId);
  }

  const allVisitors = new Set<string>();
  for (const t of totals.values()) for (const p of t.people) allVisitors.add(p);

  return [...totals.entries()]
    .map(([zoneId, t]) => {
      const meta = zones.find((z) => z.id === zoneId);
      return {
        zoneId,
        name: meta?.name ?? zoneId,
        color: meta?.color,
        visitors: t.people.size,
        avgDwellSec: t.count ? +(t.sum / t.count).toFixed(1) : 0,
        reachPct: allVisitors.size
          ? +((100 * t.people.size) / allVisitors.size).toFixed(1)
          : 0,
      };
    })
    .sort((a, b) => b.visitors * b.avgDwellSec - a.visitors * a.avgDwellSec);
}

/**
 * Interactions per surface.
 *
 * Note this counts *interactions*, not exposure seconds. `roi-framework.md` §2
 * defines sponsor exposure as dwell-weighted time in front of a branded
 * surface, which needs gaze or surface-level dwell. `spatial.gaze` exists now
 * (`buildGazeRows` below) but resolves against **zones**, because a Surface
 * carries no geometry to aim at — so exposure at surface level is still not
 * measurable and this still counts interactions. Reporting them under an
 * "exposure seconds" heading would be the sort of quiet substitution the report
 * exists to avoid, so the page labels this for what it is.
 */
export function buildSurfaceRows(
  events: RealmEvent[],
  touchpoints: { id: string; label: string }[] = []
): SurfaceRow[] {
  const counts = new Map<string, number>();
  for (const e of events) {
    if (e.type !== "surface.interaction") continue;
    const p = e.payload as SurfaceInteractionPayload;
    if (!p.surfaceId) continue;
    counts.set(p.surfaceId, (counts.get(p.surfaceId) ?? 0) + 1);
  }

  const rows = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const top = rows[0]?.[1] ?? 0;
  return rows.map(([surfaceId, interactions]) => ({
    surfaceId,
    // Falls back to the id when the session config has no touchpoint by that
    // name — which means something is reporting against a surface nobody
    // configured, and showing the raw id is the honest way to surface that.
    label: touchpoints.find((t) => t.id === surfaceId)?.label ?? surfaceId,
    interactions,
    share: top ? +((100 * interactions) / top).toFixed(1) : 0,
  }));
}

/**
 * Average dwell per hour, for the dwell sparkline.
 *
 * Hours with no dwell come back as 0 rather than being skipped, so the series
 * stays aligned with `hourlyVisitors` — two sparklines on the same row implying
 * the same x-axis had better share one.
 */
/**
 * Attention on a zone from people standing somewhere else.
 *
 * The question nothing else in the report can answer. Dwell measures the people
 * who walked in; pass-by measures the ones who came close and did not. This
 * measures the ones who **looked** — a stand that draws the eye from across the
 * room and no footsteps is a different problem from one nobody notices, and
 * until now they produced identical reports.
 *
 * `watchersWhoNeverEntered` is the column that matters, which is why it is
 * carried rather than left to be derived: somebody who studies a wall and then
 * walks into it is already counted by the funnel.
 *
 * Deliberately **not** part of `computeScorecard`. Folding gaze into Engagement
 * would change figures against definitions clients have already been shown and
 * agreed per activation, which is the one thing `roi-framework.md` §3 rules
 * out. This is a panel of its own, beside the scorecard rather than inside it.
 */
export function buildGazeRows(events: RealmEvent[], zones: ZoneMeta[]): GazeRow[] {
  const entered = new Map<string, Set<string>>();
  for (const e of events) {
    if (e.type !== "spatial.zone_enter") continue;
    const p = e.payload as ZoneMovePayload;
    if (!p.zoneId || !p.anonId) continue;
    if (!entered.has(p.zoneId)) entered.set(p.zoneId, new Set());
    entered.get(p.zoneId)!.add(p.anonId);
  }

  const looks = new Map<string, { people: Set<string>; seconds: number }>();
  for (const e of events) {
    if (e.type !== "spatial.gaze") continue;
    const p = e.payload as GazePayload;
    if (!p.targetId || !p.anonId || typeof p.durationSec !== "number") continue;
    if (!Number.isFinite(p.durationSec)) continue;
    if (!looks.has(p.targetId)) looks.set(p.targetId, { people: new Set(), seconds: 0 });
    const row = looks.get(p.targetId)!;
    row.people.add(p.anonId);
    row.seconds += p.durationSec;
  }

  return [...looks.entries()]
    .map(([zoneId, row]) => {
      const meta = zones.find((z) => z.id === zoneId);
      const walkedIn = entered.get(zoneId) ?? new Set<string>();
      let never = 0;
      for (const person of row.people) if (!walkedIn.has(person)) never++;
      return {
        zoneId,
        name: meta?.name ?? zoneId,
        color: meta?.color,
        watchers: row.people.size,
        watchersWhoNeverEntered: never,
        attentionSec: +row.seconds.toFixed(1),
      };
    })
    // By the thing the panel is for, not by total attention: a zone twenty
    // people looked at and nobody entered is the finding.
    .sort((a, b) => b.watchersWhoNeverEntered - a.watchersWhoNeverEntered);
}

export function hourlyAvgDwell(events: RealmEvent[]): number[] {
  const byHour = new Map<number, { sum: number; count: number }>();
  for (const e of events) {
    if (e.type !== "spatial.dwell") continue;
    const p = e.payload as DwellPayload;
    if (typeof p.durationSec !== "number") continue;
    const hour = Math.floor(e.occurredAt / 3_600_000);
    const t = byHour.get(hour) ?? { sum: 0, count: 0 };
    t.sum += p.durationSec;
    t.count++;
    byHour.set(hour, t);
  }

  const out: number[] = [];
  for (const hour of spanOf(byHour)) {
    const t = byHour.get(hour);
    out.push(t && t.count ? +(t.sum / t.count).toFixed(1) : 0);
  }
  return out;
}

/**
 * Cumulative leads captured per hour — a capture curve, not a rate.
 *
 * Cumulative on purpose: per-hour counts of a rare event are mostly zeros and
 * read as noise at sparkline size, where a curve reads as progress.
 */
export function hourlyLeads(events: RealmEvent[]): number[] {
  const byHour = new Map<number, number>();
  for (const e of events) {
    if (e.type !== "consent.captured" && e.type !== "identity.resolved") continue;
    const hour = Math.floor(e.occurredAt / 3_600_000);
    byHour.set(hour, (byHour.get(hour) ?? 0) + 1);
  }

  const out: number[] = [];
  let running = 0;
  for (const hour of spanOf(byHour)) {
    running += byHour.get(hour) ?? 0;
    out.push(running);
  }
  return out;
}

/**
 * Every hour from the first key to the last, including the empty ones.
 *
 * Skipping quiet hours would compress a lull into a straight line and make a
 * dead afternoon look like a steady one — the sparkline's whole job is the
 * shape.
 */
function spanOf(byHour: Map<number, unknown>): number[] {
  if (!byHour.size) return [];
  const hours = [...byHour.keys()].sort((a, b) => a - b);
  const out: number[] = [];
  for (let h = hours[0]; h <= hours[hours.length - 1]; h++) out.push(h);
  return out;
}

/** Distinct visitors per hour of the day, for the traffic sparkline. */
export function hourlyVisitors(events: RealmEvent[]): number[] {
  const byHour = new Map<number, Set<string>>();
  for (const e of events) {
    if (e.type !== "spatial.zone_enter") continue;
    const p = e.payload as ZoneMovePayload;
    if (!p.anonId) continue;
    const hour = Math.floor(e.occurredAt / 3_600_000);
    if (!byHour.has(hour)) byHour.set(hour, new Set());
    byHour.get(hour)!.add(p.anonId);
  }
  if (!byHour.size) return [];

  const hours = [...byHour.keys()].sort((a, b) => a - b);
  const out: number[] = [];
  for (let h = hours[0]; h <= hours[hours.length - 1]; h++) {
    out.push(byHour.get(h)?.size ?? 0);
  }
  return out;
}

/**
 * Notable moments, each one a fact with an event behind it.
 *
 * The old page listed four hand-written moments ("Group of 6 formed in Lounge").
 * These are the same shape, but each is the actual maximum of something in the
 * log — so if the day was unremarkable, the list is short rather than padded.
 */
export function buildMoments(events: RealmEvent[], zones: ZoneMeta[]): Moment[] {
  const zoneName = (id: string) => zones.find((z) => z.id === id)?.name ?? id;
  const out: Moment[] = [];

  let longest: { p: DwellPayload; at: number } | null = null;
  for (const e of events) {
    if (e.type !== "spatial.dwell") continue;
    const p = e.payload as DwellPayload;
    if (typeof p.durationSec !== "number") continue;
    if (!longest || p.durationSec > longest.p.durationSec) {
      longest = { p, at: e.occurredAt };
    }
  }
  if (longest) {
    out.push({
      at: longest.at,
      title: `Longest single dwell — ${Math.round(longest.p.durationSec / 60)}m at ${zoneName(longest.p.zoneId)}`,
      detail: `${longest.p.anonId} stayed longer than anyone else in one place.`,
    });
  }

  const hourly = hourlyVisitors(events);
  if (hourly.length) {
    const peak = Math.max(...hourly);
    const idx = hourly.indexOf(peak);
    const firstEnter = events.find((e) => e.type === "spatial.zone_enter");
    if (firstEnter) {
      const at = firstEnter.occurredAt + idx * 3_600_000;
      out.push({
        at,
        title: `Busiest hour — ${peak} visitors`,
        detail: "The hour with the most distinct people entering a zone.",
      });
    }
  }

  const surfaces = buildSurfaceRows(events);
  if (surfaces.length) {
    const last = events[events.length - 1];
    out.push({
      at: last?.occurredAt ?? 0,
      title: `${surfaces[0].surfaceId} was the most-used surface`,
      detail: `${surfaces[0].interactions} interactions across the session.`,
    });
  }

  return out.sort((a, b) => a.at - b.at);
}

/**
 * Recommendations, derived by rule from the numbers.
 *
 * Rules, not an LLM — the Ask/insight work is deferred until a provider key
 * exists (roadmap decision #2). Each one states the figure it came from, so a
 * client can check it rather than take it on faith. Nothing is emitted unless
 * its threshold is actually crossed.
 */
export function buildRecommendations(
  funnel: FunnelStep[],
  zoneRows: ZoneRow[],
  engagementRate: number
): string[] {
  const out: string[] = [];

  const worst = [...funnel].slice(1).sort((a, b) => b.drop - a.drop)[0];
  if (worst && worst.drop >= 30) {
    out.push(
      `The biggest fall-off is into ${worst.name} — ${worst.drop}% of visitors do not reach it. Staff or re-sign that transition before anything else.`
    );
  }

  const best = zoneRows[0];
  if (best && best.avgDwellSec >= 60) {
    out.push(
      `${best.name} holds attention longest (${Math.round(best.avgDwellSec)}s average across ${best.visitors} visitors). Move the capture moment closer to it.`
    );
  }

  const coldest = [...zoneRows].sort((a, b) => a.reachPct - b.reachPct)[0];
  if (coldest && zoneRows.length > 1 && coldest.reachPct < 25) {
    out.push(
      `${coldest.name} is reached by only ${coldest.reachPct}% of visitors. Either draw traffic to it or reclaim the floor space.`
    );
  }

  if (engagementRate > 0 && engagementRate < 0.4) {
    out.push(
      `Engagement rate is ${(engagementRate * 100).toFixed(1)}% — most visitors pass through without a qualifying dwell. The problem is depth, not footfall.`
    );
  }

  return out;
}
