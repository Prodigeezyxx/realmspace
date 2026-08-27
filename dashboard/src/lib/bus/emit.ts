/**
 * realmspace — ergonomic, context-bound event producer.
 *
 * Wraps the durable log's append() with the active tenant + session so callers
 * (perception, agents, capture surfaces, ROI) don't repeat scoping. Enforces the
 * consent redline at the producer boundary: a PII event without a consent basis
 * is refused (see docs/consent-and-identity.md).
 */

import {
  type RealmEvent,
  type RealmEventInput,
  type RealmEventPayload,
  type RealmEventType,
  isPiiEventType,
  validatePayload,
} from "@/lib/contracts";
import { append as logAppend } from "./log";
import { flushOutbound, isRemoteBusEnabled } from "./remote";
import { getTenantId } from "@/lib/tenant/context";
import { getEventContext } from "@/lib/event-context";

export interface EmitOptions {
  /** override the active session (defaults to event-context sessionId) */
  sessionId?: string;
  /** override tenant (defaults to active tenant) */
  tenantId?: string;
  /** producer-supplied idempotency key */
  eventId?: string;
  /** override occurredAt (defaults to now) */
  occurredAt?: number;
  /**
   * Consent redline bypass guard. PII events are refused unless this proof is
   * present. The real backend enforces this transactionally; here we enforce it
   * at the producer boundary so no PII event is emitted without a basis.
   */
  consentProof?: { tier: "T1" | "T2" | "T3"; contactId: string };
}

/**
 * Emit a typed event onto the durable bus, scoped to the active tenant+session.
 * Throws if a PII event is emitted without consent proof (fail closed), or if
 * the payload is one no reader in this app could use.
 */
export function emit<P = RealmEventPayload>(
  type: RealmEventType,
  payload: P,
  opts: EmitOptions = {}
): RealmEvent<P> {
  if (isPiiEventType(type) && !opts.consentProof && type !== "consent.captured") {
    // consent.captured IS the basis-creating event, so it's exempt; everything
    // else that carries PII must present proof.
    throw new Error(
      `[realmspace] refused to emit PII event "${type}" without consent proof — see docs/consent-and-identity.md`
    );
  }

  // The producer half of the refusal `bus/remote.ts` applies to everything
  // arriving from the backend. Symmetry is the point: this app must not emit
  // the malformation it has just started refusing to read, and a `spatial.dwell`
  // with no `zoneId` would be dead-lettered by the backend the moment it was
  // posted — silently, from the browser's point of view.
  //
  // A throw rather than a quarantine, in the shape of the consent redline
  // above, because every caller here is our own code with a fixed payload
  // shape. There is no high-rate producer in the browser: this is a programming
  // error, and it should surface in a test rather than on a floor.
  const check = validatePayload(type, payload);
  if (!check.ok) {
    throw new Error(
      `[realmspace] refused to emit "${type}": ${check.reasons.join(", ")} — ` +
        `see lib/contracts/validate.ts`
    );
  }

  const ctx = getEventContext();
  const tenantId = opts.tenantId ?? getTenantId();
  const sessionId = opts.sessionId ?? ctx.sessionId;
  const input: RealmEventInput<P> = {
    tenantId,
    sessionId,
    type,
    payload,
    eventId: opts.eventId,
    occurredAt: opts.occurredAt,
  };
  const event = logAppend<P>(input);

  // Dual-write to the backend, if one is configured. Deliberately not awaited:
  // the local append has already made the event durable, so a producer must not
  // be made to wait on the network — nor to handle its failure. `flushOutbound`
  // walks forward from a stored cursor, so an event that fails to send here is
  // sent later, in order, from the local log. The local log *is* the outbox.
  if (isRemoteBusEnabled()) {
    void flushOutbound(tenantId, sessionId);
  }

  return event;
}
