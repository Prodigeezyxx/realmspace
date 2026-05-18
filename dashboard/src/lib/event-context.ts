/**
 * Event context — metadata agents and skills read for the active session.
 * Populated from the session store; no external API required.
 */

import type { Session, Zone } from "@/lib/session/types";

export interface EventContext {
  sessionId: string;
  eventName: string;
  venue: string;
  startAt: string;
  endAt?: string;
  zones: Zone[];
  boothSize: { width: number; depth: number };
}

const DEFAULT_BOOTH = { width: 10, depth: 6 };

let context: EventContext = {
  sessionId: "default",
  eventName: "Untitled event",
  venue: "—",
  startAt: new Date().toISOString(),
  zones: [],
  boothSize: DEFAULT_BOOTH,
};

export function getEventContext(): EventContext {
  return context;
}

export function setEventContextFromSession(session: Session, boothSize = DEFAULT_BOOTH) {
  context = {
    sessionId: session.id,
    eventName: session.name,
    venue: session.venue,
    startAt: session.startAt,
    endAt: session.endAt,
    zones: session.zones,
    boothSize,
  };
}

export function patchEventContext(patch: Partial<EventContext>) {
  context = { ...context, ...patch };
}
