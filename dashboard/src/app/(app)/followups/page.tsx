"use client";

/**
 * Follow-up drafts, for the human who decides whether any of them is sent.
 *
 * `consumers/sdr.py` writes them onto the log after a session ends, for visitors
 * who consented at T2 or above. This page reads them and does nothing else:
 * there is no send button, because there is nothing behind one. Sending wants an
 * email provider that does not exist, a suppression list, a bounce story and an
 * audit of who pressed it, and a button that did a quarter of that would be
 * worse than none.
 *
 * The page says so rather than leaving an operator to discover it.
 */

import { Mail, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { EmptyState } from "@/components/ui/EmptyState";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";
import { useActiveSession } from "@/lib/session/store";

interface Draft {
  contact: { id: string; name?: string | null; email?: string | null };
  subject: string;
  body: string;
  basis: string;
  sent: boolean;
  drafted_at?: string | null;
  grounded_in?: {
    zones_visited?: string[];
    top_dwell_zone?: string | null;
    dwell_seconds_total?: number | null;
    surfaces_engaged?: string[];
  };
}

interface Followup {
  seq: number;
  sessionId: string;
  draftedAt: string | null;
  withdrawn: boolean;
  sent: boolean;
  basis: string;
  draft: Draft;
}

type State =
  | { status: "loading" }
  | { status: "offline" | "error"; detail: string }
  | { status: "ready"; followups: Followup[]; sendingSupported: boolean };

export default function FollowupsPage() {
  const session = useActiveSession();
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetchFollowups(session.id).then(setState);
  }, [session.id]);

  return (
    <div className="p-5 max-w-[1000px] mx-auto space-y-5">
      <div>
        <Pill variant="info" className="mb-2">
          <Mail size={11} />
          Contextual SDR
        </Pill>
        <h1 className="text-3xl font-semibold tracking-tight">Follow-up drafts</h1>
        <p className="text-sm text-text-secondary mt-1.5 max-w-2xl">
          Written after the session ends, for visitors who agreed to be contacted.
          Each one references only what this activation measured about them.
        </p>
      </div>

      <div className="panel p-4 flex items-start gap-3 text-sm">
        <ShieldCheck size={16} className="text-accent shrink-0 mt-0.5" />
        <p className="text-text-secondary leading-relaxed">
          <strong className="text-text-primary">Nothing here has been sent,</strong>{" "}
          and nothing here can be. These are drafts for you to read, copy and send
          yourself. Sending from realmspace would need its own consent question, a
          suppression list and an audit of who sent what — none of which exists
          yet, and half of it would be worse than none.
        </p>
      </div>

      {state.status === "loading" && (
        <div className="panel p-6 text-sm text-text-muted animate-pulse">
          Loading drafts…
        </div>
      )}

      {(state.status === "offline" || state.status === "error") && (
        <EmptyState
          icon={<Mail size={20} />}
          title="No drafts to show"
          hint={state.detail}
        />
      )}

      {state.status === "ready" && state.followups.length === 0 && (
        <EmptyState
          icon={<Mail size={20} />}
          title="No drafts yet"
          hint="Drafts are written when a session ends, for visitors who consented at T2 or above. An activation still running has none."
        />
      )}

      {state.status === "ready" &&
        state.followups.map((followup) => (
          <DraftCard key={followup.seq} followup={followup} />
        ))}
    </div>
  );
}

function DraftCard({ followup }: { followup: Followup }) {
  const { draft } = followup;
  const grounded = draft.grounded_in ?? {};

  return (
    <Panel
      title={draft.contact.name || draft.contact.email || draft.contact.id}
      subtitle={draft.contact.email ?? undefined}
      action={
        followup.withdrawn ? (
          <Pill variant="warn">withdrawn</Pill>
        ) : (
          <Pill variant="neutral">not sent</Pill>
        )
      }
    >
      {followup.withdrawn ? (
        <p className="text-sm text-text-secondary leading-relaxed">
          This visitor withdrew their consent. The draft is kept as a record that
          it existed and was never sent; what it said about them is gone.
        </p>
      ) : (
        <div className="space-y-3">
          <div className="text-sm font-medium">{draft.subject}</div>
          <pre className="text-sm text-text-secondary whitespace-pre-wrap font-sans leading-relaxed">
            {draft.body}
          </pre>

          {/*
            What the draft was allowed to reference. A reviewer checks a sentence
            against this rather than trusting it — the only way to catch a model
            that invented a conversation.
          */}
          <div className="pt-3 border-t border-border-hairline text-[11px] text-text-muted space-y-1">
            <div className="uppercase tracking-[0.18em]">Grounded in</div>
            <div>
              Zones: {(grounded.zones_visited ?? []).join(" → ") || "none recorded"}
            </div>
            <div>
              Surfaces:{" "}
              {(grounded.surfaces_engaged ?? []).join(", ") || "none recorded"}
            </div>
            <div>
              Total dwell: {Math.round(grounded.dwell_seconds_total ?? 0)}s
              {" · "}
              {draft.basis === "deterministic"
                ? "composed without a model"
                : `written by ${draft.basis}`}
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}

async function fetchFollowups(sessionId: string): Promise<State> {
  if (!isRemoteBusEnabled()) {
    return {
      status: "offline",
      detail:
        "No backend configured. Drafts are written by a bus consumer and read from the durable log, which only the backend has.",
    };
  }
  const token = await ensureToken(busEmail());
  if (!token) {
    return { status: "error", detail: "Could not authenticate with the bus." };
  }
  try {
    const res = await fetch(
      `${busUrl()}/v1/followups?sessionId=${encodeURIComponent(sessionId)}`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    if (res.status === 403) {
      return {
        status: "error",
        detail:
          "Follow-up drafts are an operator's to read — they are somebody's name beside a letter written about them.",
      };
    }
    if (!res.ok) {
      return { status: "error", detail: `The bus returned ${res.status}.` };
    }
    const body = (await res.json()) as {
      followups: Followup[];
      sendingSupported: boolean;
    };
    return {
      status: "ready",
      followups: body.followups ?? [],
      sendingSupported: body.sendingSupported ?? false,
    };
  } catch {
    return { status: "error", detail: "The bus is unreachable." };
  }
}
