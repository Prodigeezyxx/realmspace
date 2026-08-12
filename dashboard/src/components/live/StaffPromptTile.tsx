"use client";

/**
 * realmspace — what the room is being asked to do, right now.
 *
 * The "Next-Step surface" of `roadmap.md` Phase 3, and the end of the chain the
 * phase's acceptance criterion measures: a rule matches on the edge, the
 * dispatcher appends `rule.staff_prompt`, the broadcast consumer carries it over
 * the WebSocket, and it appears here. `< 3s` for all of it
 * (`event-bus-spec.md` §4).
 *
 * ## Why this panel is designed to empty itself
 *
 * Every other tile on `/live` accumulates: more visitors, more dwell, a longer
 * sparkline. This one must not. A prompt is worth acting on for about as long as
 * the situation that caused it lasts — "greet the group at the entrance" is
 * worthless once they have moved on — and a panel still showing it twenty
 * minutes later is not information, it is furniture that teaches the floor staff
 * to stop reading the screen. `staffPrompts` drops anything past
 * `PROMPT_TTL_MS`, so the empty state is the normal state and a filled panel
 * means something is happening now.
 *
 * ## Why there is no acknowledge button
 *
 * It would need somewhere to record the acknowledgement, and the only honest
 * place is the bus — an event type, a producer, a consumer that reads it. None
 * of that exists, and a button that dismissed a prompt in one browser tab while
 * leaving it on the tablet by the door would be worse than no button: two
 * members of staff would disagree about whether the room had been handled.
 * Expiry is the mechanism until there is an event for it.
 */

import { AlertTriangle, Megaphone } from "lucide-react";

import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import type { StaffPrompt } from "@/lib/live/derive";
import { cn, formatRelative } from "@/lib/utils";

const PRIORITY: Record<
  StaffPrompt["priority"],
  { label: string; dot: string; ring: string }
> = {
  high: {
    label: "High",
    dot: "bg-accent-red",
    ring: "border-accent-red/40 bg-accent-red/5",
  },
  normal: { label: "Normal", dot: "bg-accent-blue", ring: "border-border-subtle" },
  low: { label: "Low", dot: "bg-text-muted", ring: "border-border-subtle" },
};

export function StaffPromptTile({
  prompts,
  now,
}: {
  prompts: StaffPrompt[];
  /**
   * The log's clock, not the browser's — passed in for the same reason
   * `staffPrompts` takes it: a replay must render what was on screen at that
   * moment, not what is on screen now.
   */
  now: number;
}) {
  return (
    <Panel
      title="Next step · staff prompts"
      subtitle="What a rule has asked the floor to do. Recent only — prompts expire rather than pile up."
      action={
        <Pill variant={prompts.length ? "warn" : "neutral"}>
          {prompts.length ? `${prompts.length} active` : "nothing pending"}
        </Pill>
      }
    >
      {prompts.length ? (
        <ul className="flex flex-col gap-2">
          {prompts.map((prompt) => {
            const meta = PRIORITY[prompt.priority] ?? PRIORITY.normal;
            return (
              <li
                key={prompt.eventId}
                className={cn(
                  "flex items-start gap-3 rounded-lg border px-3 py-2.5",
                  meta.ring
                )}
              >
                <span
                  className={cn("mt-1.5 size-2 shrink-0 rounded-full", meta.dot)}
                  aria-label={meta.label}
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm leading-snug text-text-primary">
                    {prompt.message}
                  </p>
                  <p className="mt-1 text-xs text-text-muted">
                    {/* Which rule asked, so an operator who disagrees with the
                        prompt knows what to go and change. */}
                    {prompt.ruleName}
                    {prompt.zoneId ? ` · ${prompt.zoneId}` : ""} ·{" "}
                    {formatRelative(Math.max(0, now - prompt.at))}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="flex items-start gap-3">
          <Megaphone size={16} className="mt-0.5 shrink-0 text-text-muted" />
          <p className="text-sm leading-relaxed text-text-secondary">
            No rule is asking for anything. Prompts appear here within seconds of
            a rule matching and clear themselves once they are stale — an empty
            panel is the room running itself, not a broken feed.
          </p>
        </div>
      )}
    </Panel>
  );
}

/**
 * A rule fired, and the action it asked for could not be carried out.
 *
 * Exported separately because the failure belongs on `/ops` with the rest of the
 * dead-letter queue, not here: a prompt panel that showed errors would make the
 * floor staff responsible for a backend problem they cannot fix.
 */
export function PromptDeliveryNote({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <p className="flex items-center gap-1.5 text-xs text-accent-amber">
      <AlertTriangle size={12} />
      {count} rule {count === 1 ? "action" : "actions"} could not be delivered —
      see the review queue on /ops.
    </p>
  );
}
