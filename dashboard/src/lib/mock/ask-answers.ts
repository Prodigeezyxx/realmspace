/**
 * Pre-canned answers for the "Ask the Room" demo.
 * In production, the user query is sent to /api/query which:
 *   1. uses Claude/GPT-4o to translate NL → Cypher
 *   2. runs against Neo4j
 *   3. returns a structured response (natural answer + chart type + payload)
 *
 * For the prototype, we match keywords client-side and return convincing answers.
 */

export type AskChartType =
  | "number"
  | "bar"
  | "line"
  | "rank"
  | "subgraph"
  | "text";

export interface AskAnswer {
  query: string;
  match: RegExp;
  natural: string;
  cypher: string;
  chartType: AskChartType;
  data: unknown;
  insights?: string[];
}

export const suggestedQueries: string[] = [
  "How many unique visitors today?",
  "Which zone had the longest average dwell?",
  "Did anyone look at the Bottle Wall for more than 10 seconds?",
  "Show me the busiest 5 minutes.",
  "What's the conversion rate from Mirror Room to RFID capture?",
  "Which interactive surfaces drove the most lounge dwell?",
  "Where are people losing interest?",
  "Compare today to yesterday.",
];

export const answers: AskAnswer[] = [
  {
    query: "How many unique visitors today?",
    match: /how many|unique|visitors|people.*today/i,
    natural:
      "1,287 unique anonymous visitors entered the activation today — that's +18% vs. yesterday and the strongest day of the campaign so far.",
    cypher: `MATCH (p:Person)
WHERE p.first_seen >= date('2026-05-18')
RETURN count(DISTINCT p) AS unique_visitors`,
    chartType: "number",
    data: { value: 1287, unit: "visitors", delta: "+18%" },
  },
  {
    query: "Which zone had the longest average dwell?",
    match: /longest.*dwell|average dwell|which zone.*dwell/i,
    natural:
      "Lounge held visitors longest with an average dwell of 6m 50s — 2.9× longer than the next-closest zone. Visitors who passed through the Mirror Room before reaching Lounge dwelled even longer (8m 12s).",
    cypher: `MATCH (p:Person)-[d:DWELLED_IN]->(z:Zone)
RETURN z.name AS zone, avg(d.duration) AS avg_dwell
ORDER BY avg_dwell DESC`,
    chartType: "rank",
    data: [
      { label: "Lounge", value: 410, unit: "s" },
      { label: "Mirror Room", value: 142, unit: "s" },
      { label: "Bottle Wall", value: 71, unit: "s" },
      { label: "Exit + RFID Wall", value: 32, unit: "s" },
      { label: "Entry Arch", value: 18, unit: "s" },
    ],
    insights: [
      "Visitors who tried the Scent Quiz dwelled 2.4× longer in the Lounge.",
      "Groups of 3+ in the Lounge averaged 9m 22s.",
    ],
  },
  {
    query: "Did anyone look at the Bottle Wall for more than 10 seconds?",
    match: /bottle wall|gaze|look(ed)?.*at|attention.*product/i,
    natural:
      "Yes — 71 visitors held gaze on the Bottle Wall for more than 10s today. Of those, 58 (82%) then captured a memory at the RFID Wall on their way out. The most-looked-at bottle was 'Rose Nuit' (avg gaze: 14.2s).",
    cypher: `MATCH (p:Person)-[g:LOOKED_AT]->(o:Object {label: 'Bottle Wall'})
WHERE g.duration > 10
OPTIONAL MATCH (p)-[:INTERACTED_WITH]->(r:Object {label: 'RFID Wall'})
RETURN p.anon_id, g.duration, r IS NOT NULL AS captured`,
    chartType: "bar",
    data: [
      { label: "Looked >10s", value: 71 },
      { label: "Then captured RFID", value: 58 },
      { label: "Looked <10s, captured", value: 12 },
    ],
  },
  {
    query: "Show me the busiest 5 minutes.",
    match: /busiest|peak|busy|highest|maximum/i,
    natural:
      "The peak was 18:42–18:47 — 31 concurrent visitors with 7 active groups. This coincided with the Scent Quiz reaching its first leaderboard. Consider scheduling staff lifts around 18:30 tomorrow.",
    cypher: `MATCH (p:Person)
WITH p, datetime({epochMillis: p.last_seen}) AS t
RETURN date_trunc('minute', t) AS minute, count(p) AS concurrent
ORDER BY concurrent DESC LIMIT 5`,
    chartType: "line",
    data: {
      label: "Concurrent visitors · today",
      series: [
        { t: "17:00", v: 8 },
        { t: "17:30", v: 12 },
        { t: "18:00", v: 17 },
        { t: "18:30", v: 24 },
        { t: "18:42", v: 31 },
        { t: "18:45", v: 30 },
        { t: "19:00", v: 22 },
        { t: "19:30", v: 14 },
        { t: "20:00", v: 9 },
      ],
    },
  },
  {
    query: "What's the conversion rate from Mirror Room to RFID capture?",
    match: /conversion|funnel|mirror.*rfid|capture.*rate/i,
    natural:
      "62% of visitors who entered the Mirror Room ended up capturing a memory at the RFID Wall — vs. 23% for visitors who skipped it. The Mirror Room is your single strongest predictor of conversion.",
    cypher: `MATCH (p:Person)-[:DWELLED_IN]->(m:Zone {name: 'Mirror Room'})
OPTIONAL MATCH (p)-[:INTERACTED_WITH]->(r:Object {label: 'RFID Wall'})
RETURN count(p) AS visited_mirror,
       count(r) AS converted,
       100.0 * count(r) / count(p) AS conversion_rate`,
    chartType: "bar",
    data: [
      { label: "Saw Mirror → captured", value: 62, unit: "%" },
      { label: "Skipped Mirror → captured", value: 23, unit: "%" },
    ],
    insights: [
      "Recommendation: surface the Mirror Room earlier in the funnel for tomorrow's session.",
    ],
  },
  {
    query: "Where are people losing interest?",
    match: /losing interest|drop.?off|leave|exit early|bounce/i,
    natural:
      "Two friction points: (1) 38% of visitors leave within 30s of entering the Entry Arch — the queue line is unclear. (2) Visitors who reach the Bottle Wall but don't engage the AR Mirror first leave 2.1× faster.",
    cypher: `MATCH (p:Person)-[:ENTERED]->(z:Zone)
WITH p, z, duration.between(p.first_seen, p.last_seen) AS stay
WHERE stay.seconds < 30
RETURN z.name, count(p) AS bounces`,
    chartType: "rank",
    data: [
      { label: "Entry Arch bounces", value: 38, unit: "%" },
      { label: "Bottle Wall — no Mirror", value: 2.1, unit: "× faster exit" },
      { label: "Lounge cold seat (NW corner)", value: 12, unit: "% empty" },
    ],
  },
  {
    query: "Which interactive surfaces drove the most lounge dwell?",
    match: /interactive|surface|drove|lounge dwell|game/i,
    natural:
      "The Scent Quiz is the standout — visitors who completed it spent on average 6m 12s in the Lounge afterward (vs. 2m 32s for those who didn't). The AR Mirror is the second-strongest driver.",
    cypher: `MATCH (p:Person)-[:INTERACTED_WITH]->(s:Object),
      (p)-[d:DWELLED_IN]->(l:Zone {name: 'Lounge'})
RETURN s.label AS surface, avg(d.duration) AS lounge_dwell
ORDER BY lounge_dwell DESC`,
    chartType: "rank",
    data: [
      { label: "Scent Quiz", value: 372, unit: "s lounge dwell" },
      { label: "AR Mirror", value: 288, unit: "s lounge dwell" },
      { label: "Bottle Wall", value: 198, unit: "s lounge dwell" },
      { label: "(no interaction)", value: 152, unit: "s lounge dwell" },
    ],
  },
  {
    query: "Compare today to yesterday.",
    match: /compare|yesterday|trend|vs ?\.?|previous day/i,
    natural:
      "Today is outperforming yesterday across the board: visitors +18%, avg dwell +12%, RFID captures +24%, group formations +31%. The Mirror Room is the biggest swing — +47% engagement.",
    cypher: `MATCH (p:Person)
WITH date(p.first_seen) AS day, count(p) AS visitors
RETURN day, visitors ORDER BY day DESC LIMIT 2`,
    chartType: "bar",
    data: [
      { label: "Visitors", value: 18, unit: "%" },
      { label: "Avg dwell", value: 12, unit: "%" },
      { label: "RFID captures", value: 24, unit: "%" },
      { label: "Group formations", value: 31, unit: "%" },
      { label: "Mirror engagement", value: 47, unit: "%" },
    ],
  },
];

export function findAnswer(q: string): AskAnswer | null {
  const trimmed = q.trim();
  if (!trimmed) return null;
  return answers.find((a) => a.match.test(trimmed)) ?? null;
}
