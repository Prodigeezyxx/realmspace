"use client";

import { useSyncExternalStore } from "react";

import { DEFAULT_TENANT_ID, getTenantId, subscribeTenantId } from "./context";

/**
 * The current tenant, as a value an effect can depend on.
 *
 * `getTenantId()` returns a guess until a token exchange has verified who this
 * browser is — see the comment on the listener set in `context.ts`. An effect
 * that reads it once at the top and never looks again is therefore pinned to
 * that guess for the life of the component, which on a first load for any
 * organisation other than the default means reading an empty partition of the
 * local log and reporting it as a quiet day.
 *
 * Used as a dependency, this re-runs the effect when the real tenant arrives.
 *
 * The server snapshot is the default rather than the stored value, because the
 * server has no storage to read: returning anything else would be inventing a
 * tenant for a render that cannot know one.
 */
export function useTenantId(): string {
  return useSyncExternalStore(
    subscribeTenantId,
    getTenantId,
    () => DEFAULT_TENANT_ID
  );
}
