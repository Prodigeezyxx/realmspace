/**
 * realmspace — tenant context.
 *
 * realmspace is a multi-tenant public product (see docs/multi-tenant.md): every
 * event, contact, integration and benchmark is scoped by tenantId. Floats is
 * simply tenant #1 on the same code path as everyone else.
 *
 * For the prototype there is a single default tenant; the resolver is centralised
 * here so that when auth (Firebase → org → role) is wired, only this file
 * changes — callers keep using getTenantId().
 */

const DEFAULT_TENANT_ID = "t_floats";
const STORAGE_KEY = "rs:tenantId";

let current: string = DEFAULT_TENANT_ID;

/**
 * Notified when the verified tenant arrives, which is later than you think.
 *
 * The value below starts as a guess — the default, or whatever this browser
 * had stored — and only becomes the truth once a token exchange has said so
 * (`lib/bus/remote.ts`, `fetchToken`). Anything that reads the tenant
 * synchronously at the start of an effect therefore reads the guess, and on a
 * first load for any other organisation the guess is wrong.
 *
 * That is not hypothetical. It made a report render "the session is configured
 * correctly and the log is genuinely empty" over an activation full of
 * visitors: the backfill mirrored nothing because every event's tenant failed
 * the comparison, and the local partition it then read was empty. A reload
 * fixed it, which is exactly why it survived.
 *
 * So the value is subscribable, and an effect keyed on it re-runs when the real
 * one lands.
 */
const listeners = new Set<() => void>();

function hasWindow() {
  return typeof window !== "undefined";
}

// hydrate from storage on first import (client only)
if (hasWindow()) {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved) current = saved;
  } catch {
    /* ignore */
  }
}

export function getTenantId(): string {
  return current;
}

export function setTenantId(tenantId: string) {
  const next = tenantId || DEFAULT_TENANT_ID;
  const changed = next !== current;
  current = next;
  if (hasWindow()) {
    try {
      window.localStorage.setItem(STORAGE_KEY, current);
    } catch {
      /* ignore */
    }
  }
  // Only on a real change. Notifying on every token refresh would re-run every
  // subscribed effect hourly for nothing — and `useSyncExternalStore` would
  // still bail out, so the work would be invisible rather than absent.
  if (changed) for (const listener of listeners) listener();
}

/** Subscribe to tenant changes. Returns the unsubscribe, as React expects. */
export function subscribeTenantId(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export { DEFAULT_TENANT_ID };
