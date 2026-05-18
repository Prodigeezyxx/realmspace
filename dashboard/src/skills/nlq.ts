/**
 * Stack: query string → { cypher, explanation, chartData? }
 * Local keyword match — no Claude API.
 */
import { answers } from "@/lib/mock/ask-answers";
import type { SkillModule, SkillRunInput } from "./types";

export interface ChartSpec {
  type: "bar" | "line" | "pie";
  labels: string[];
  values: number[];
}

export interface NlqOutput {
  query: string;
  cypher: string;
  explanation: string;
  chartData?: ChartSpec;
  raw?: unknown;
}

function toChart(data: unknown, chartType: string): ChartSpec | undefined {
  if (!data || typeof data !== "object") return undefined;
  const d = data as Record<string, unknown>;
  if (Array.isArray(d.labels) && Array.isArray(d.values)) {
    const type =
      chartType === "line" ? "line" : chartType === "pie" ? "pie" : "bar";
    return {
      type,
      labels: d.labels as string[],
      values: d.values as number[],
    };
  }
  if (typeof d.value === "number") {
    return { type: "bar", labels: ["Result"], values: [d.value] };
  }
  return undefined;
}

export const nlqSkill: SkillModule<NlqOutput> = {
  id: "nlq",
  stack: 'trigger.payload.query → { cypher, explanation, chartData? }',
  async run(input: SkillRunInput) {
    const query = String(input.trigger.payload.query ?? "").trim();
    const hit = answers.find((a) => a.match.test(query));
    if (!hit) {
      return {
        query,
        cypher: "// no match",
        explanation:
          "I could not map that question to the spatial graph. Try asking about visitors, dwell, zones, or conversions.",
      };
    }
    return {
      query,
      cypher: hit.cypher,
      explanation: hit.natural,
      chartData: toChart(hit.data, hit.chartType),
      raw: hit.data,
    };
  },
};
