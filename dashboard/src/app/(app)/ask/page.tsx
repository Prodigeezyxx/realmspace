"use client";

/**
 * Ask the Room.
 *
 * Every figure on this page came out of the graph or the durable log. What it
 * replaces returned nine hardcoded answers over invented numbers, behind a demo
 * gate that meant a real activation asked a question and got nothing — the same
 * category as the report's `1,287 visitors` and `/agents`' `fired: 488`.
 *
 * The invented latency breakdown went with it. There is one real number now,
 * `tookMs`, measured by the backend; "Claude 3.5 Sonnet · 420 ms" was a label
 * describing a call this system had never made.
 */

import { ArrowRight, Brain, Database, MessageSquareText, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import {
  type AskTurn,
  type CatalogueEntry,
  fetchCatalogue,
  useAsk,
} from "@/lib/ask/useAsk";
import { useActiveSession } from "@/lib/session/store";

export default function AskPage() {
  const session = useActiveSession();
  const sessionId = session.id;
  const { turns, status, ask } = useAsk(sessionId);
  const [q, setQ] = useState("");
  const [catalogue, setCatalogue] = useState<CatalogueEntry[]>([]);

  useEffect(() => {
    void fetchCatalogue().then(setCatalogue);
  }, []);

  if (!sessionId) {
    return (
      <div className="p-5 max-w-[1400px] mx-auto">
        <EmptyState
          variant="page"
          icon={<Brain size={20} />}
          title="No session selected"
          hint="Ask answers questions about one activation. Pick or create a session and the room becomes answerable."
        />
      </div>
    );
  }

  return (
    <div className="p-5 max-w-[1400px] mx-auto grid grid-cols-12 gap-5">
      <div className="col-span-12 lg:col-span-8 space-y-5">
        <div>
          <Pill variant="info" className="mb-2">
            <Brain size={11} />
            Ask the Room
          </Pill>
          <h1 className="text-3xl font-semibold tracking-tight">
            Plain English. Measured answers.
          </h1>
          <p className="text-sm text-text-secondary mt-1.5 max-w-xl">
            Your question is matched to one of the measurements this activation
            actually takes, run against the spatial graph or the event log, and
            the rows it read are shown beside the answer.
          </p>
        </div>

        <div className="space-y-5">
          {turns.map((turn, i) => (
            <AnswerCard key={i} turn={turn} />
          ))}
          {status === "asking" && (
            <div className="panel p-5 text-sm text-text-muted animate-pulse">
              Measuring…
            </div>
          )}
        </div>

        <div className="panel-elevated p-4 sticky bottom-4 z-10">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void ask(q);
              setQ("");
            }}
            className="flex items-center gap-3"
          >
            <MessageSquareText size={18} className="text-accent shrink-0 ml-1" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Ask the room…  (e.g. which zone had the longest average dwell?)"
              className="flex-1 bg-transparent outline-none text-base placeholder:text-text-muted py-2"
              autoFocus
            />
            <Button
              type="submit"
              variant="primary"
              size="md"
              iconAfter={<ArrowRight size={14} />}
              disabled={!q.trim() || status === "asking"}
            >
              Ask
            </Button>
          </form>
        </div>
      </div>

      <div className="col-span-12 lg:col-span-4 space-y-5">
        <Panel
          title="What this room can be asked"
          subtitle="Every one of these is measured"
          action={<Sparkles size={14} className="text-accent-violet" />}
        >
          {catalogue.length === 0 ? (
            <p className="text-xs text-text-muted">
              The catalogue comes from the backend. With none configured there is
              nothing to suggest — and nothing to answer.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {catalogue.flatMap((entry) =>
                entry.examples.slice(0, 1).map((example) => (
                  <li key={`${entry.query}-${example}`}>
                    <button
                      onClick={() => void ask(example)}
                      className="w-full text-left text-sm px-3 py-2.5 rounded-md hover:bg-bg-elevated transition-colors text-text-secondary hover:text-text-primary border border-transparent hover:border-border-subtle"
                    >
                      {example}
                    </button>
                  </li>
                ))
              )}
            </ul>
          )}
        </Panel>

        <Panel title="How a question is answered" subtitle="No query is generated">
          <ol className="space-y-2.5 text-xs text-text-secondary list-decimal list-inside">
            <li>
              Your question is matched to one <strong>named measurement</strong>{" "}
              from the list above.
            </li>
            <li>
              That measurement is hand-written and reviewed. Nothing generates a
              query, so nothing can generate one that reads another client&apos;s
              activation.
            </li>
            <li>It runs scoped to your tenant and this session, always.</li>
            <li>The rows it read are shown to you beside the answer.</li>
          </ol>
        </Panel>
      </div>
    </div>
  );
}

function AnswerCard({ turn }: { turn: AskTurn }) {
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        <div className="w-7 h-7 rounded-full bg-bg-elevated border border-border-subtle flex items-center justify-center shrink-0 text-text-secondary">
          <MessageSquareText size={13} />
        </div>
        <div className="flex-1 panel-elevated px-4 py-3">
          <div className="text-sm font-medium">{turn.question}</div>
        </div>
      </div>

      <div className="flex items-start gap-3">
        <div className="w-7 h-7 rounded-full bg-accent flex items-center justify-center shrink-0 shadow-[var(--glow-green)]">
          <Brain size={13} className="text-text-inverse" />
        </div>
        <div className="flex-1 panel p-5 space-y-4">
          {turn.error ? (
            <p className="text-base leading-relaxed text-text-secondary">
              {turn.error}
            </p>
          ) : (
            turn.answer && <Answer answer={turn.answer} />
          )}
        </div>
      </div>
    </div>
  );
}

function Answer({ answer }: { answer: NonNullable<AskTurn["answer"]> }) {
  return (
    <>
      <p className="text-base leading-relaxed">{answer.answer}</p>

      {answer.rows.length > 0 && <Rows rows={answer.rows} chart={answer.chart} />}

      {answer.query === null && answer.canAnswer.length > 0 && (
        <div className="text-xs text-text-secondary space-y-1.5 pt-1">
          <div className="text-text-muted">It can answer:</div>
          <ul className="space-y-1">
            {answer.canAnswer.map((entry) => (
              <li key={entry.query}>· {entry.asks}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex items-center justify-between gap-3 pt-1 text-[11px] text-text-muted border-t border-border-hairline">
        <span className="inline-flex items-center gap-1.5 pt-2">
          <Database size={11} />
          {answer.query ? (
            <span className="font-mono">{answer.query}</span>
          ) : (
            "no measurement matched"
          )}
        </span>
        {/*
          The basis, always. With no AI provider configured a deterministic
          matcher picks the measurement — every figure is still measured, and an
          operator has to be able to tell that apart from a model's answer.
        */}
        <span className="pt-2 tabular">
          {answer.basis === "deterministic"
            ? "matched without a model"
            : `answered by ${answer.basis}`}{" "}
          · {answer.tookMs} ms
        </span>
      </div>
    </>
  );
}

function Rows({
  rows,
  chart,
}: {
  rows: Record<string, unknown>[];
  chart: string | null;
}) {
  const columns = Object.keys(rows[0] ?? {});
  const numeric = columns.find((c) =>
    rows.every((r) => typeof r[c] === "number")
  );

  if (chart === "bar" && numeric && columns.length > 1) {
    const label = columns.find((c) => c !== numeric) ?? columns[0];
    const max = Math.max(...rows.map((r) => Number(r[numeric]) || 0), 1);
    return (
      <div className="space-y-2 py-1">
        {rows.map((row, i) => (
          <div key={i} className="space-y-1">
            <div className="flex justify-between text-xs">
              <span className="text-text-secondary">{String(row[label])}</span>
              <span className="tabular text-text-primary font-medium">
                {Number(row[numeric]).toLocaleString(undefined, {
                  maximumFractionDigits: 1,
                })}
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-bg-elevated overflow-hidden">
              <div
                className="h-full bg-accent rounded-full"
                style={{ width: `${(Number(row[numeric]) / max) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-muted text-left">
            {columns.map((c) => (
              <th key={c} className="font-normal py-1.5 pr-4 font-mono text-[10px]">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-t border-border-hairline">
              {columns.map((c) => (
                <td key={c} className="py-1.5 pr-4 tabular text-text-secondary">
                  {typeof row[c] === "number"
                    ? Number(row[c]).toLocaleString(undefined, {
                        maximumFractionDigits: 1,
                      })
                    : String(row[c] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
