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
  current = tenantId || DEFAULT_TENANT_ID;
  if (hasWindow()) {
    try {
      window.localStorage.setItem(STORAGE_KEY, current);
    } catch {
      /* ignore */
    }
  }
}

export { DEFAULT_TENANT_ID };
