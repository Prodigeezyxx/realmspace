"use client";

/**
 * realmspace — the operator's own read of the day, on the client report.
 *
 * Everything else on the report is computed and can be traced to an event. This
 * cannot: it is a person's judgement about what happened, which is often the
 * most useful paragraph on the page and is worth nothing if a client cannot tell
 * it apart from a measurement.
 *
 * So it is labelled, italicised, and sits under a heading that says who wrote
 * it. The separation is the feature.
 */

import { PenLine } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { sessionActions } from "@/lib/session/store";
import type { Session } from "@/lib/session/types";

/**
 * Mount this with `key={session.id}`. Switching sessions must discard an
 * unsaved draft rather than carry it across — one activation's commentary
 * appearing on another's report is worse than losing a few typed words. A `key`
 * does that by remounting, which is the React idiom; syncing it in an effect
 * would be derived state and would flash the old note for a frame first.
 */
export function OperatorNote({ session }: { session: Session }) {
  const [draft, setDraft] = useState(session.reportNote ?? "");
  const [editing, setEditing] = useState(false);

  function save() {
    sessionActions.updateSession(session.id, {
      reportNote: draft.trim() || undefined,
    });
    setEditing(false);
  }

  if (!editing && !session.reportNote) {
    return (
      // Nothing written, so nothing to print: an empty "Add a note" prompt in a
      // client's PDF is the operator's to-do list on the client's document.
      <Panel
        title="Operator note"
        subtitle="Your read of the day — optional, and marked as commentary"
        data-print-hide
      >
        <Button
          variant="secondary"
          size="sm"
          icon={<PenLine size={14} />}
          onClick={() => setEditing(true)}
        >
          Add a note
        </Button>
      </Panel>
    );
  }

  return (
    <Panel
      title="Operator note"
      subtitle="Written by the team who ran the activation — commentary, not computed"
      action={
        !editing ? (
          // The note itself prints — it is often the most useful paragraph on
          // the page. The affordance for changing it does not.
          <span data-print-hide>
            <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
              Edit
            </Button>
          </span>
        ) : undefined
      }
    >
      {editing ? (
        <div className="space-y-3">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={5}
            autoFocus
            placeholder="What the numbers don't show. Weather, a competitor's stand next door, the queue at 4pm…"
            className="w-full px-3 py-2.5 rounded-lg bg-bg-canvas border border-border-subtle text-sm leading-relaxed focus:border-accent focus:outline-none transition-colors"
          />
          <div className="flex gap-2">
            <Button variant="primary" size="sm" onClick={save}>
              Save note
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setDraft(session.reportNote ?? "");
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap italic">
          {session.reportNote}
        </p>
      )}
    </Panel>
  );
}
