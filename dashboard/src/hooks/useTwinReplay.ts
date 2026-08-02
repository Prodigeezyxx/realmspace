"use client";

import { useEffect, useMemo, useState } from "react";

import {
  isRemoteBusConfigured,
  remoteReadAll,
  remoteSessionList,
} from "@/lib/bus/remote";
import { listLocalSessions, readAll } from "@/lib/bus/log";
import type { SessionMeta } from "@/lib/contracts";
import type { PersonTrack } from "@/lib/mock/people";
import { getTenantId } from "@/lib/tenant/context";
import { reduceReplaySession } from "@/lib/twin/replay-tracks";

export type ReplaySource = "none" | "local" | "remote";

export interface TwinReplayState {
  status: "loading" | "ready" | "empty";
  source: ReplaySource;
  tracks: PersonTrack[];
  durationSec: number;
  eventCount: number;
}

/**
 * Rebuild twin tracks for a recorded session from the bus.
 * The local log renders synchronously (derive, no effect state); the remote
 * bus upgrades it asynchronously — the remote log is the durable source of
 * truth for recorded sessions.
 */
export function useTwinReplay(
  sessionId: string | null | undefined
): TwinReplayState {
  const tenantId = getTenantId();

  const localEvents = useMemo(
    () => (sessionId ? readAll(tenantId, sessionId) : []),
    [tenantId, sessionId]
  );
  const localReplay = useMemo(() => reduceReplaySession(localEvents), [localEvents]);

  const [remoteReplay, setRemoteReplay] = useState<{
    tracks: PersonTrack[];
    durationSec: number;
    eventCount: number;
  } | null>(null);

  useEffect(() => {
    if (!sessionId || !isRemoteBusConfigured()) return;
    let cancelled = false;
    remoteReadAll(tenantId, sessionId)
      .then((events) => {
        if (cancelled || events.length <= localEvents.length) return;
        const reduced = reduceReplaySession(events);
        setRemoteReplay({
          tracks: reduced.tracks,
          durationSec: reduced.durationSec,
          eventCount: reduced.eventCount,
        });
      })
      .catch(() => {
        /* keep local / empty */
      });
    return () => {
      cancelled = true;
    };
  }, [tenantId, sessionId, localEvents.length]);

  return useMemo<TwinReplayState>(() => {
    if (!sessionId) {
      return { status: "empty", source: "none", tracks: [], durationSec: 0, eventCount: 0 };
    }
    if (remoteReplay && remoteReplay.eventCount > 0) {
      return {
        status: "ready",
        source: "remote",
        tracks: remoteReplay.tracks,
        durationSec: remoteReplay.durationSec,
        eventCount: remoteReplay.eventCount,
      };
    }
    if (localReplay.eventCount > 0) {
      return {
        status: "ready",
        source: "local",
        tracks: localReplay.tracks,
        durationSec: localReplay.durationSec,
        eventCount: localReplay.eventCount,
      };
    }
    return { status: "empty", source: "none", tracks: [], durationSec: 0, eventCount: 0 };
  }, [sessionId, remoteReplay, localReplay]);
}

/**
 * Recorded sessions available for replay: local partitions + remote bus,
 * merged on sessionId (remote wins), most recent first.
 */
export function useRecordedSessions(tenantId: string): SessionMeta[] {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);

  useEffect(() => {
    let cancelled = false;
    const collect = async () => {
      const merged = new Map<string, SessionMeta>();
      for (const s of listLocalSessions(tenantId)) merged.set(s.sessionId, s);
      if (isRemoteBusConfigured()) {
        try {
          for (const s of await remoteSessionList(tenantId)) merged.set(s.sessionId, s);
        } catch {
          /* local-only picker is fine */
        }
      }
      const all = [...merged.values()].sort((a, b) => b.lastAt - a.lastAt);
      if (!cancelled) setSessions(all);
    };
    void collect();
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  return sessions;
}
