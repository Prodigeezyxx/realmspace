/**
 * In-memory pub/sub — swap for Redis later without changing callers.
 */

export interface BusMessage<T = unknown> {
  channel: string;
  timestamp: number;
  payload: T;
}

type Handler = (msg: BusMessage) => void;

const handlers = new Map<string, Set<Handler>>();

export function busSubscribe(channel: string, handler: Handler): () => void {
  if (!handlers.has(channel)) handlers.set(channel, new Set());
  handlers.get(channel)!.add(handler);
  return () => handlers.get(channel)?.delete(handler);
}

export function busEmit<T>(channel: string, payload: T) {
  const msg: BusMessage<T> = { channel, timestamp: Date.now(), payload };
  handlers.get(channel)?.forEach((h) => h(msg as BusMessage));
  handlers.get("*")?.forEach((h) => h(msg as BusMessage));
}

export function busSubscribeAll(handler: Handler): () => void {
  return busSubscribe("*", handler);
}

export const AGENT_STREAM_CHANNEL = "agent:stream";
export const AGENT_ALERT_CHANNEL = "agent:alert";
export const TWIN_UPDATE_CHANNEL = "twin:update";
