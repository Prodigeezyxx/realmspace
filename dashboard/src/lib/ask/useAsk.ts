"use client";

/**
 * Ask the Room, client side.
 *
 * Everything here is a fetch. The question goes to `POST /v1/ask`, which routes
 * it to a named measurement in `backend/app/llm/catalogue.py`, runs that
 * measurement against the graph or the log, and returns the rows it read. The
 * browser does not translate questions, does not hold answers, and does not know
 * what Cypher is — the same split Phase 3 made when the browser stopped owning
 * rules.
 *
 * What this replaces is `@/lib/mock/ask-answers`: nine regexes returning
 * invented numbers behind an `isDemo` gate, so a real activation asked a
 * question and got nothing.
 *
 * ## `basis` is rendered, always
 *
 * With no AI provider configured — open decision 2, still open — a deterministic
 * matcher picks the measurement and the backend phrases the answer from the
 * entry's own template. Every figure is still measured, but an operator has to
 * be able to tell that apart from a model's answer, so `basis` travels with
 * every response and the page shows it.
 */

import { useCallback, useState } from "react";

import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";

export interface CatalogueParam {
  name: string;
  type: string;
  default: unknown;
  description: string;
}

export interface CatalogueEntry {
  query: string;
  asks: string;
  examples: string[];
  params: CatalogueParam[];
}

export interface AskAnswer {
  question: string;
  answer: string;
  /** Which measurement served it. `null` when the question was refused. */
  query: string | null;
  params: Record<string, unknown>;
  rows: Record<string, unknown>[];
  chart: string | null;
  /** `deterministic`, or the configured provider's name. Never absent. */
  basis: string;
  /** Sent on a refusal: a refusal with no alternative is a dead end. */
  canAnswer: CatalogueEntry[];
  tookMs: number;
}

export type AskStatus = "idle" | "asking" | "ready" | "offline" | "error";

export interface AskTurn {
  question: string;
  answer: AskAnswer | null;
  /** Set when the request itself failed — distinct from a refusal, which is an
   * answer the backend gave and which arrives as a 200. */
  error: string | null;
}

export function useAsk(sessionId: string | null | undefined) {
  const [turns, setTurns] = useState<AskTurn[]>([]);
  const [status, setStatus] = useState<AskStatus>("idle");

  const ask = useCallback(
    async (question: string) => {
      const trimmed = question.trim();
      if (!trimmed || !sessionId) return;

      setTurns((cur) => [...cur, { question: trimmed, answer: null, error: null }]);
      setStatus("asking");

      const result = await askOnce(trimmed, sessionId);
      setTurns((cur) => {
        const next = [...cur];
        next[next.length - 1] = result.turn;
        return next;
      });
      setStatus(result.status);
    },
    [sessionId]
  );

  return { turns, status, ask };
}

async function askOnce(
  question: string,
  sessionId: string
): Promise<{ turn: AskTurn; status: AskStatus }> {
  if (!isRemoteBusEnabled()) {
    return {
      status: "offline",
      turn: {
        question,
        answer: null,
        error:
          "No backend configured, so there is nothing to ask. Answers are measured from the durable log and the graph, which only the backend has.",
      },
    };
  }

  const token = await ensureToken(busEmail());
  if (!token) {
    return {
      status: "error",
      turn: { question, answer: null, error: "Could not authenticate with the bus." },
    };
  }

  try {
    const res = await fetch(`${busUrl()}/v1/ask`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ question, sessionId }),
    });
    if (!res.ok) {
      return {
        status: "error",
        turn: { question, answer: null, error: `The bus returned ${res.status}.` },
      };
    }
    return {
      status: "ready",
      turn: { question, answer: (await res.json()) as AskAnswer, error: null },
    };
  } catch {
    return {
      status: "error",
      turn: { question, answer: null, error: "The bus is unreachable." },
    };
  }
}

/**
 * The questions this deployment can answer, from the catalogue itself.
 *
 * The suggestions used to be a hardcoded list sitting beside a hardcoded set of
 * answers, which is how a demo ends up offering a question the real system
 * cannot serve. These come from the same structure the backend routes against,
 * so a suggestion that cannot be answered is not something this codebase can
 * express.
 */
export async function fetchCatalogue(): Promise<CatalogueEntry[]> {
  if (!isRemoteBusEnabled()) return [];
  const token = await ensureToken(busEmail());
  if (!token) return [];
  try {
    const res = await fetch(`${busUrl()}/v1/ask/catalogue`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return [];
    const body = (await res.json()) as { queries: CatalogueEntry[] };
    return body.queries ?? [];
  } catch {
    return [];
  }
}
