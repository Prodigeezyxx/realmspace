"use client";

/**
 * realmspace — rules, client side.
 *
 * The store half of ADR-002's first reason for rules being data: "an operator
 * writes rules, not an engineer. The composer UI is plain-English → spec →
 * operator confirms. That is only possible if a rule is a value the UI can
 * build, show back, and store."
 *
 * Same shape as `lib/ops/useDeadLetters.ts`, deliberately — both are screens
 * over a backend table, and the interesting decision in each is what to show
 * when there is no backend.
 *
 * ## No backend is not an empty list
 *
 * The laptop demo runs with `NEXT_PUBLIC_BUS_URL` unset. An empty rule list in
 * that state would read as "this booth has no rules", which is a different claim
 * from "there is nowhere to keep one" — the same distinction the report makes
 * about revenue it cannot see, and the cost tile about spend it cannot measure.
 * So `offline` is its own status and the screen says what is missing.
 */

import { useCallback, useEffect, useState } from "react";

import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";
import type { RuleDocument, StoredRule } from "@/lib/contracts/rules";

/**
 * What `POST /v1/rules/compose` returns: a document nobody has armed.
 *
 * `rule` is null when nothing was built, and then `reason` says why and
 * `canBuild` lists what it could have. `basis` is `deterministic` or the
 * provider's name, and the screen renders it — an operator who cannot tell a
 * model's draft from a keyword match cannot judge either of them.
 */
export interface ComposedRule {
  instruction: string;
  rule: RuleDocument | null;
  reason: string;
  warnings: string[];
  basis: string;
  canBuild: { shape: string; asks: string; examples: string[] }[];
  tookMs: number;
}

export type RulesStatus = "loading" | "ready" | "offline" | "error";

export interface RulesState {
  status: RulesStatus;
  items: StoredRule[];
  detail: string | null;
}

async function authed(path: string, init?: RequestInit): Promise<Response | null> {
  const token = await ensureToken(busEmail());
  if (!token) return null;
  return fetch(`${busUrl()}${path}`, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  });
}

function initialState(): RulesState {
  return isRemoteBusEnabled()
    ? { status: "loading", items: [], detail: null }
    : {
        status: "offline",
        items: [],
        detail:
          "No backend configured, so there is nowhere to store a rule. " +
          "Presets below can still be previewed against this session's events.",
      };
}

export function useRules() {
  const [state, setState] = useState<RulesState>(initialState);

  const refresh = useCallback(async () => {
    if (!isRemoteBusEnabled()) return;
    try {
      const res = await authed("/v1/rules");
      if (!res) {
        setState({
          status: "error",
          items: [],
          detail: "Could not authenticate with the bus.",
        });
        return;
      }
      if (!res.ok) {
        setState({
          status: "error",
          items: [],
          detail: `The bus returned ${res.status}.`,
        });
        return;
      }
      setState({ status: "ready", items: await res.json(), detail: null });
    } catch {
      setState({ status: "error", items: [], detail: "The bus is unreachable." });
    }
  }, []);

  useEffect(() => {
    if (!isRemoteBusEnabled()) return;
    // Every setState in `refresh` happens after an `await`, so none runs
    // synchronously during the effect. Suppressed narrowly and with the reason,
    // as in useDeadLetters.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  /**
   * Save one rule. Returns null on success, or why it was refused.
   *
   * A 422 is surfaced verbatim rather than as "invalid": the backend's validator
   * is the authority on the spec, and paraphrasing it here would be a fourth
   * place that knows the rule language.
   */
  const save = useCallback(
    async (rule: RuleDocument): Promise<string | null> => {
      if (!isRemoteBusEnabled()) {
        return "No backend configured — a rule saved here would not be armed.";
      }
      const res = await authed(`/v1/rules/${encodeURIComponent(rule.ruleId)}`, {
        method: "PUT",
        body: JSON.stringify(rule),
      });
      if (!res) return "Could not authenticate with the bus.";
      if (res.status === 403) {
        return "Saving a rule arms an action in the room — that needs an operator account.";
      }
      if (res.status === 402) {
        // A plan limit (`app/plans.py`) — `gtm.md` calls a rule a "custom
        // agent" and caps them per tier. The detail already names the tier, the
        // cap and the tier that would lift it, so it is shown as written rather
        // than wrapped in this app's own phrasing or its raw JSON.
        const body = await res.json().catch(() => null);
        return typeof body?.detail === "string"
          ? body.detail
          : "This organisation's plan does not allow another agent.";
      }
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        return `The bus refused it (${res.status}). ${body}`.trim();
      }
      await refresh();
      return null;
    },
    [refresh]
  );

  /**
   * Plain English in, an **unsaved** document out. Nothing is armed here.
   *
   * The composing happens on the edge because that is where the provider key
   * and the budget are, and because the document is validated by the same
   * members `PUT /v1/rules` enforces — a second validator in the browser would
   * be a fourth place that knows the rule language.
   *
   * A refusal is not an error: "I could not turn that into a rule" is an answer
   * and the request succeeded, which is `/v1/ask`'s reasoning. What is returned
   * here is a *transport* failure, so the screen can tell "we could not reach
   * our own backend" from "that instruction is not a rule".
   */
  const compose = useCallback(
    async (
      instruction: string,
      sessionId: string
    ): Promise<ComposedRule | string> => {
      if (!isRemoteBusEnabled()) {
        return "No backend configured, so there is nothing to compose with.";
      }
      const res = await authed("/v1/rules/compose", {
        method: "POST",
        body: JSON.stringify({ instruction, sessionId }),
      });
      if (!res) return "Could not authenticate with the bus.";
      if (res.status === 403) {
        return "Composing a rule is part of building agents — that needs an analyst or admin account.";
      }
      if (!res.ok) return `The bus refused it (${res.status}).`;
      return (await res.json()) as ComposedRule;
    },
    []
  );

  const remove = useCallback(
    async (ruleId: string): Promise<string | null> => {
      if (!isRemoteBusEnabled()) return "No backend configured.";
      const res = await authed(`/v1/rules/${encodeURIComponent(ruleId)}`, {
        method: "DELETE",
      });
      if (!res) return "Could not authenticate with the bus.";
      if (!res.ok && res.status !== 404) return `The bus returned ${res.status}.`;
      await refresh();
      return null;
    },
    [refresh]
  );

  return { ...state, refresh, save, remove, compose };
}
