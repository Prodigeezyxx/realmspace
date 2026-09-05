"use client";

/**
 * realmspace — this activation against the client's own history.
 *
 * `roi-framework.md` §2 names this the most useful benchmark there is: *"the
 * most useful benchmark is the client's own history"*. The report already
 * carried the industry 3–5:1 band on the ROI pill, which tells a client how
 * they compare to a category. This tells them whether they beat themselves.
 *
 * Two things it will not do:
 *
 * - **It never renders an absence as a zero.** A first activation has not
 *   declined from anything; an activation whose cost nobody typed in has no ROI
 *   ratio, not a ratio of nought. Both come through as an em dash with the
 *   reason underneath.
 * - **It shows no realmspace network median.** That figure needs anonymised
 *   aggregates across tenants (`multi-tenant.md` §6) and nothing in this system
 *   can read another tenant's data. Named as absent on the card, the same way
 *   the surfaces panel names its missing interaction counts.
 */

import { History, Info } from "lucide-react";

import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import type { Benchmark, BenchmarkRow } from "@/lib/report/useBenchmark";

function format(row: BenchmarkRow, value: number | null): string {
  if (value == null) return "—";
  switch (row.key) {
    case "engagementRate":
      return `${(value * 100).toFixed(1)}%`;
    case "avgDwellSec":
      return `${value.toFixed(0)}s`;
    case "roiRatio":
      return `${value.toFixed(2)}:1`;
    default:
      return value.toLocaleString();
  }
}

/**
 * The change, as a percentage of the baseline.
 *
 * Null whenever either side is missing or the baseline is zero — a change from
 * nothing is not an improvement of infinity, and printing one would be the
 * report's version of the invented ROI ratio that Phase 2 removed.
 */
function delta(row: BenchmarkRow): number | null {
  if (row.current == null || row.median == null || row.median === 0) return null;
  return ((row.current - row.median) / Math.abs(row.median)) * 100;
}

export function BenchmarkCard({ benchmark }: { benchmark: Benchmark }) {
  if (benchmark.status === "none" && !benchmark.missing.length) return null;

  const compared = benchmark.comparedWith.length;

  return (
    <Panel
      title="Against this client's own history"
      subtitle={
        compared
          ? `Median of the ${compared} previous activation${compared > 1 ? "s" : ""} on record`
          : "No previous activations on record yet"
      }
      action={
        <Pill variant={compared ? "info" : "neutral"}>
          <History size={11} />
          {benchmark.status === "loading" ? "Reading history…" : `n = ${compared}`}
        </Pill>
      }
    >
      <div className="p-6 space-y-4">
        {compared > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[11px] uppercase tracking-[0.18em] text-text-secondary">
                  <th className="text-left font-normal pb-3">Figure</th>
                  <th className="text-right font-normal pb-3">This activation</th>
                  <th className="text-right font-normal pb-3">Their median</th>
                  <th className="text-right font-normal pb-3">Change</th>
                  <th className="text-right font-normal pb-3">Based on</th>
                </tr>
              </thead>
              <tbody>
                {benchmark.rows.map((row) => {
                  const d = delta(row);
                  return (
                    <tr
                      key={row.key}
                      className="border-t border-border-hairline"
                    >
                      <td className="py-3 text-text-primary">{row.label}</td>
                      <td className="py-3 text-right tabular-nums">
                        {format(row, row.current)}
                      </td>
                      <td className="py-3 text-right tabular-nums text-text-secondary">
                        {format(row, row.median)}
                      </td>
                      <td
                        className={
                          "py-3 text-right tabular-nums " +
                          // `accent` and `accent-red`, not arbitrary colours:
                          // both are remapped in the print block to shades that
                          // clear 4.5:1 on white. The brand greens are tuned for
                          // a near-black background and measure 1.36:1 on paper,
                          // which is how the ROI figure came to print invisible.
                          (d == null
                            ? "text-text-muted"
                            : d >= 0
                              ? "text-accent"
                              : "text-accent-red")
                        }
                      >
                        {d == null
                          ? "—"
                          : `${d >= 0 ? "+" : ""}${d.toFixed(1)}%`}
                      </td>
                      <td className="py-3 text-right tabular-nums text-text-muted">
                        {/* Per figure, not per activation: an earlier session
                            with no cost counts towards the visitor median and
                            not towards the ROI one, and the two rows should not
                            look equally well evidenced. */}
                        {row.n === 0 ? "—" : `${row.n} activation${row.n > 1 ? "s" : ""}`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {benchmark.missing.length > 0 && (
          <div className="flex items-start gap-2 text-xs text-text-secondary leading-relaxed">
            <Info size={13} className="mt-0.5 shrink-0 text-text-muted" />
            <div className="space-y-1">
              {benchmark.missing.map((m) => (
                <div key={m}>{m}</div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Panel>
  );
}
