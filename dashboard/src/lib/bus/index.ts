/**
 * realmspace bus — public entrypoint.
 *
 * `log.ts`  = durable append-only, idempotent, replayable event log
 *             (docs/event-bus-spec.md). Use this for anything that must survive
 *             reloads or be replayed by a consumer (perception, attribution,
 *             ROI, CRM handoff).
 *
 * The legacy in-memory pub/sub (channels) still lives in `@/lib/event-bus` for
 * ephemeral UI signalling (agent stream, twin ticks). New durable work should
 * prefer the log below.
 */
export * as bus from "./log";
export {
  append,
  read,
  readAll,
  replay,
  drain,
  subscribe,
  headSeq,
  getCursor,
  setCursor,
  clearPartition,
} from "./log";
export { emit, type EmitOptions } from "./emit";

/**
 * `remote.ts` bridges this log to the FastAPI backend when NEXT_PUBLIC_BUS_URL
 * is set: events out via POST /events, events in over the live WebSocket.
 * Unset, none of it runs and the app stays self-contained.
 */
export {
  connectLiveFeed,
  backfillSession,
  fetchSessionEvents,
  ensureTenantId,
  flushOutbound,
  markLocalOnly,
  ensureToken,
  signUpOrganisation,
  clearToken,
  isRemoteBusEnabled,
  busUrl,
  busEmail,
  getBusStatus,
  subscribeBusStatus,
  getRemoteSeq,
  mirror,
  type BusStatus,
} from "./remote";
export {
  eventFromWire,
  eventToWire,
  payloadFromWire,
  payloadToWire,
  type WireEvent,
} from "./wire";

/**
 * `quarantine.ts` holds the events this browser refused as unreadable — the
 * client-side counterpart of the backend's dead-letter queue. Read by `/report`,
 * which has to say when a figure was computed from fewer events than arrived,
 * and by `/ops`, which shows the two queues side by side.
 */
export {
  readRefused,
  listRefusedSessions,
  getRefusalVersion,
  refusalSnapshot,
  type RefusedSession,
  recordRefusal,
  summariseRefusals,
  subscribeRefusals,
  clearRefusals,
  type RefusedEvent,
} from "./quarantine";
