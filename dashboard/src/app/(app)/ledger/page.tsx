"use client";

/**
 * realmspace — the attribution ledger, and the CFO one-pager above it.
 *
 * `roi-framework.md` §4 lists these as two deliverables: an "exportable,
 * auditable list of every booth-touch → outcome link with timestamps and consent
 * basis", and "a single auto-generated page: cost in, ROI ratio out, vs.
 * benchmark, with the attribution model stated plainly". They are one screen
 * here because they are the same artifact at two zoom levels — a summary a
 * finance team can act on, and the evidence underneath it that they can check.
 *
 * ## The one thing this page is really about
 *
 * Until now `revenue_influenced` was a figure an operator typed in, and the ROI
 * ratio was arithmetic on a client's own assertion. This is the first screen
 * where it is **measured** — summed from recorded outcomes that fall inside the
 * agreed attribution window. Both are shown when both exist, because where they
 * disagree that disagreement is the most interesting number on the page.
 *
 * Nothing here computes attribution. The rows, the window judgement and the
 * totals all come from `GET /v1/ledger/{session}`; the ROI ratio and benchmark
 * are `@/lib/roi/scorecard`'s, shared with the report.
 */

import { AlertTriangle, Download, FileSpreadsheet, Info, RefreshCw } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import {
  downloadLedgerCsv,
  useLedger,
  type Ledger,
  type LedgerRow,
} from "@/lib/ledger/useLedger";
import { benchmarkVerdict, roiRatio } from "@/lib/roi/scorecard";
import { useActiveSession } from "@/lib/session/store";

const VERDICT_COPY: Record<string, string> = {
  exceptional: "Above the 5:1 end of the industry benchmark.",
  strong: "Inside the 3:1–5:1 industry benchmark.",
  below: "Below the 3:1 industry benchmark.",
  unknown: "Not computable yet — see what is missing below.",
};

export default function LedgerPage() {
  const session = useActiveSession();
  const { status, ledger, detail, refresh } = useLedger(session?.id);
  const [csvError, setCsvError] = useState<string | null>(null);

  async function exportCsv() {
    if (!session?.id) return;
    setCsvError(await downloadLedgerCsv(session.id));
  }

  return (
    <div className="max-w-[1100px] mx-auto p-6 md:p-10 space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <Pill variant="info" className="mb-2">
            <FileSpreadsheet size={11} />
            Attribution ledger
          </Pill>
          <h1 className="text-2xl font-semibold tracking-tight">
            Booth touch → outcome
          </h1>
          <p className="text-sm text-text-secondary mt-1 max-w-2xl leading-relaxed">
            Every lead this activation produced, what it turned into, and the
            consent that permitted it. This is the auditable version — each row
            traces to an event on the log, and nothing here is estimated.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            icon={<RefreshCw size={14} />}
            onClick={() => void refresh()}
          >
            Refresh
          </Button>
          <Button
            variant="primary"
            size="sm"
            icon={<Download size={14} />}
            disabled={status !== "ready"}
            onClick={() => void exportCsv()}
          >
            Export CSV
          </Button>
        </div>
      </div>

      {csvError && <p className="text-xs text-accent-red">{csvError}</p>}

      {status === "loading" && (
        <EmptyState variant="page" title="Reading the ledger…" />
      )}

      {(status === "offline" || status === "error") && (
        <EmptyState
          variant="page"
          icon={<Info size={20} />}
          title={
            status === "offline" ? "No backend configured" : "Could not read the ledger"
          }
          hint={detail ?? undefined}
        />
      )}

      {status === "ready" && ledger && (
        <>
          <OnePager ledger={ledger} />
          {ledger.rows.length === 0 ? (
            <EmptyState
              variant="page"
              icon={<Info size={20} />}
              title="No leads yet"
              hint="A lead appears here once a visitor consents. This is a measured empty, not a missing feed."
            />
          ) : (
            <Panel
              title="The ledger"
              subtitle={`${ledger.rows.length} ${
                ledger.rows.length === 1 ? "lead" : "leads"
              } · every row traces to an event on the log`}
            >
              <LedgerTable rows={ledger.rows} />
            </Panel>
          )}
        </>
      )}
    </div>
  );
}

/**
 * Cost in, ROI out, versus benchmark, with the model stated plainly.
 *
 * The stating is not decoration — `roi-framework.md` §3's design principle is
 * that a client picks the model and we never inflate, which only means anything
 * if the model is on the page next to the number it produced.
 */
function OnePager({ ledger }: { ledger: Ledger }) {
  const measured = ledger.totals.revenue_influenced;
  const stated = ledger.revenue_influenced_stated;
  const cost = ledger.activation_cost;
  const ratio = roiRatio(measured, cost);
  const verdict = benchmarkVerdict(ratio);

  return (
    <Panel
      title="Where the money went, and what came back"
      subtitle={`${ledger.attribution_model} model · ${ledger.attribution_window_days}-day window · agreed before doors opened`}
    >
      <div className="space-y-4">
        {!ledger.model_supported && (
          <p className="text-xs text-accent-red/90 flex items-start gap-2 leading-relaxed">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>{ledger.model_note}</span>
          </p>
        )}

        <div className="grid sm:grid-cols-4 gap-4">
          <Figure label="Activation cost" value={money(cost, ledger.totals.currency)} />
          <Figure
            label="Influenced revenue"
            value={money(measured, ledger.totals.currency)}
            note={
              measured == null
                ? "No closed-won deal inside the window yet"
                : "Measured from recorded outcomes"
            }
          />
          <Figure
            label="ROI ratio"
            value={ratio == null ? "—" : `${ratio.toFixed(2)}×`}
            note={VERDICT_COPY[verdict]}
          />
          <Figure
            label="Leads"
            value={String(ledger.totals.leads)}
            note={`${ledger.totals.influenced_leads} with a deal in window`}
          />
        </div>

        {stated != null && (
          <p className="text-xs text-text-muted leading-relaxed">
            The client also stated an influenced revenue of{" "}
            <strong>{money(stated, ledger.totals.currency)}</strong>. It is shown
            beside the measured figure rather than instead of it — where the two
            disagree, that difference is the thing worth talking about.
          </p>
        )}

        {ledger.totals.mixed_currencies.length > 0 && (
          <p className="text-xs text-text-muted leading-relaxed">
            Outcomes were recorded in {ledger.totals.mixed_currencies.join(" and ")}.
            They are not added together — a total across currencies is a number
            with no unit.
          </p>
        )}

        {ledger.totals.anonymous_touches > 0 && (
          <p className="text-xs text-text-muted leading-relaxed">
            {ledger.totals.anonymous_touches} rows here are anonymous touches —
            visitors this activation measured and never named. They carry a path
            and no contact, and they are deliberately not counted in the lead
            figure above: that number is people who gave us their details, not
            people who walked in.
          </p>
        )}

        {ledger.totals.withdrawn > 0 && (
          <p className="text-xs text-text-muted leading-relaxed">
            {ledger.totals.withdrawn} of these visitors withdrew consent. Their
            rows stay, without their names: the touch happened and the record of
            it is what shows their data stopped being used.
          </p>
        )}

        {ledger.totals.orphan_outcomes > 0 && (
          <p className="text-xs text-accent-red/90 leading-relaxed">
            {ledger.totals.orphan_outcomes} outcome
            {ledger.totals.orphan_outcomes === 1 ? "" : "s"} named a lead this
            activation never produced. Kept and flagged rather than dropped —
            that is a discrepancy worth chasing, not tidying away.
          </p>
        )}

        {ledger.truncated && (
          <p className="text-xs text-accent-red/90 leading-relaxed">
            This activation has more events than one read returns, so the ledger
            below is incomplete. Do not export it as an audit record.
          </p>
        )}
      </div>
    </Panel>
  );
}

function Figure({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.16em] text-text-muted mb-1">
        {label}
      </div>
      <div className="text-2xl font-semibold tracking-tight tabular">{value}</div>
      {note && (
        <div className="text-[11px] text-text-muted mt-1 leading-snug">{note}</div>
      )}
    </div>
  );
}

function LedgerTable({ rows }: { rows: LedgerRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-[0.16em] text-text-muted">
            <th className="py-2 pr-4 font-normal">Lead</th>
            <th className="py-2 pr-4 font-normal">Path</th>
            <th className="py-2 pr-4 font-normal">Consent</th>
            <th className="py-2 pr-4 font-normal">Outcome</th>
            <th className="py-2 pr-4 font-normal text-right">Attributed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.dedupe_key}
              className="border-t border-border-hairline align-top"
            >
              <td className="py-3 pr-4">
                <div className="font-medium">
                  {row.withdrawn ? (
                    <span className="text-text-muted italic">Withdrawn</span>
                  ) : (
                    row.contact_name || row.contact_email || row.anon_id || "—"
                  )}
                </div>
                <div className="text-[11px] text-text-muted">
                  {row.first_touch_at
                    ? new Date(row.first_touch_at).toLocaleString()
                    : "—"}
                </div>
              </td>
              <td className="py-3 pr-4 text-text-secondary">
                <div>{row.zones_visited.join(" › ") || "—"}</div>
                {row.lead_score != null && (
                  <div className="text-[11px] text-text-muted">
                    score {row.lead_score} · {row.lead_score_basis}
                  </div>
                )}
              </td>
              <td className="py-3 pr-4 text-text-secondary">
                <div>{row.consent_tier ?? "—"}</div>
                {/* The versioned copy is the field a disputed consent is settled
                    by, so it is on the row rather than a click away. */}
                <div className="text-[11px] text-text-muted">
                  {row.consent_copy_version ?? "—"}
                </div>
              </td>
              <td className="py-3 pr-4">
                {row.outcomes.length === 0 ? (
                  <span className="text-text-muted">Still in play</span>
                ) : (
                  row.outcomes.map((outcome) => (
                    <div key={outcome.outcomeId} className="mb-1 last:mb-0">
                      <span className="capitalize">{outcome.stage}</span>
                      {outcome.daysToClose != null && (
                        <span className="text-text-muted">
                          {" "}
                          · {outcome.daysToClose}d
                        </span>
                      )}
                      {outcome.inWindow === false && (
                        <span className="text-accent-red/90"> · out of window</span>
                      )}
                    </div>
                  ))
                )}
              </td>
              <td className="py-3 pr-4 text-right tabular">
                {money(row.attributed_value, row.currency)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** `—` for absent, never `0`. The two are different answers. */
function money(value: number | null | undefined, currency: string | null): string {
  if (value == null) return "—";
  return `${currency ? `${currency} ` : ""}${value.toLocaleString()}`;
}
