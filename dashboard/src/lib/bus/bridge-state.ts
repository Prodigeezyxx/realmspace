/**
 * Shared connection state for the edge bus bridge (one WS for the app).
 */

import type { BusConnectionState } from "@/lib/bus/remote";

type Listener = () => void;

let state: BusConnectionState = "off";
let lastSeq = 0;
const listeners = new Set<Listener>();

function emit() {
  listeners.forEach((l) => l());
}

export function getRemoteBusState() {
  return state;
}

export function getRemoteBusLastSeq() {
  return lastSeq;
}

export function setRemoteBusState(s: BusConnectionState) {
  if (state === s) return;
  state = s;
  emit();
}

export function setRemoteBusLastSeq(seq: number) {
  if (seq <= lastSeq) return;
  lastSeq = seq;
  emit();
}

export function subscribeRemoteBusState(listener: Listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
