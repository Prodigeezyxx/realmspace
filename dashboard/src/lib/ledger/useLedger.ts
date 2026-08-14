"use client";

/**
 * The attribution ledger, client side.
 *
 * `roi-framework.md` §4 calls it "what a CFO/auditor asks for". Everything shown
 * comes from `GET /v1/ledger/{session}` — the arithmetic lives in
 * `backend/app/attribution/ledger.py`, and a second implementation here is
 * exactly the split-brain ADR-002 exists to end. This file fetches and nothing
 * more.
 */

import { useCallback, useEffect, useState } from "react";

import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";

export interface LedgerOutcome {
  outcomeId: string | null;
  stage: "won" | "lost" | "open" | null;
  value: number | null;
  currency: string | null;
  closedAt: string | null;
  source: string | null;
  externalRef: string | null;
  recordedBy: string | null;
  daysToClose: number | null;
  /** `null` = not judgeable, which an open opportunity genuinely is not. */
  inWindow: boolean | null;
}

export interface LedgerRow {
  dedupe_key: string;
  contact_id: string | null;
  contact_name: string | null;
  contact_email: string | null;
  anon_id: string | null;
  first_touch_at: string | null;
  final_touch_at: string | null;
  zones_visited: string[];
  dwell_seconds_total: number | null;
  lead_score: number | null;
  lead_score_basis: string | null;
  consent_tier: string | null;
  consent_basis: string | null;
  consent_copy_version: string | null;
  consent_captured_at: string | null;
  /** PII is stripped from the row, but the touch and its basis remain. */
  withdrawn: boolean;
  /** An outcome naming a lead this activation never produced. */
  orphan: boolean;
  outcomes: LedgerOutcome[];
  attributed_value: number | null;
  currency: string | null;
}

export interface LedgerTotals {
  leads: number;
  withdrawn: number;
  orphan_outcomes: number;
  outcomes: number;
  outcomes_in_window: number;
  influenced_leads: number;
  /** `null` when the model cannot be honoured, or currencies are mixed. */
  revenue_influenced: number | null;
  currency: string | null;
  mixed_currencies: string[];
}

export interface Ledger {
  session_id: string;
  attribution_model: string;
  attribution_window_days: number;
  /** False for linear/time_decay — see `model_note` for the reason. */
  model_supported: boolean;
  model_note: string | null;
  activation_cost: number | null;
  /** The client's own typed-in figure, carried beside the measured one. */
  revenue_influenced_stated: number | null;
  truncated: boolean;
  rows: LedgerRow[];
  totals: LedgerTotals;
}

export type LedgerStatus = "loading" | "ready" | "offline" | "error";

export interface LedgerState {
  status: LedgerStatus;
  ledger: Ledger | null;
  detail: string | null;
}

/** Exported and React-free so it can be tested without rendering anything. */
export async function fetchLedger(sessionId: string): Promise<LedgerState> {
  if (!isRemoteBusEnabled()) {
    return {
      status: "offline",
      ledger: null,
      detail:
        "No backend configured, so there is no ledger to read. The ledger is built from the durable log, which only the backend has.",
    };
  }

  const token = await ensureToken(busEmail());
  if (!token) {
    return {
      status: "error",
      ledger: null,
      detail: "Could not authenticate with the bus.",
    };
  }

  try {
    const res = await fetch(
      `${busUrl()}/v1/ledger/${encodeURIComponent(sessionId)}`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    if (!res.ok) {
      return {
        status: "error",
        ledger: null,
        detail: `The bus returned ${res.status}.`,
      };
    }
    return { status: "ready", ledger: await res.json(), detail: null };
  } catch {
    return { status: "error", ledger: null, detail: "The bus is unreachable." };
  }
}

/**
 * The CSV, downloaded through an authenticated fetch rather than a plain link.
 *
 * An `<a href>` cannot carry the bearer token, and a ledger endpoint that
 * accepted a token in the query string would put PII-bearing credentials into
 * every proxy log between here and the backend.
 */
export async function downloadLedgerCsv(sessionId: string): Promise<string | null> {
  const token = await ensureToken(busEmail());
  if (!token) return "Could not authenticate with the bus.";

  try {
    const res = await fetch(
      `${busUrl()}/v1/ledger/${encodeURIComponent(sessionId)}?format=csv`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    if (!res.ok) return `The bus returned ${res.status}.`;

    const url = URL.createObjectURL(await res.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `ledger-${sessionId}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    return null;
  } catch {
    return "The bus is unreachable.";
  }
}

export function useLedger(sessionId: string | null | undefined) {
  const [state, setState] = useState<LedgerState>({
    status: "loading",
    ledger: null,
    detail: null,
  });

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    setState(await fetchLedger(sessionId));
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    // Every setState happens after an await, so none runs synchronously during
    // the effect — same narrow suppression, and same reason, as useDeadLetters.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh, sessionId]);

  return { ...state, refresh };
}

// The ROI ratio and the benchmark verdict are `@/lib/roi/scorecard`'s — the
// report and this page must never be able to disagree about the number a CFO
// reads first. They were inline inside `computeScorecard` and are now exported
// from it rather than copied here.
