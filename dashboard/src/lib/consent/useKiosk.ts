"use client";

/**
 * The consent kiosk: what it asks, and the yeses it owes the backend.
 *
 * ## Why the queue is here at all
 *
 * `useTablet`'s argument, one step sharper. The one thing a stand's wifi is
 * reliably bad at is the moment the stand is busy, and a lost tap is a number
 * that comes out slightly low. A lost consent is a person who handed over their
 * details, was told "thank you", and never hears from anybody — the failure the
 * whole identified funnel exists to prevent, and the one the visitor cannot
 * discover or repair.
 *
 * So a consent mints its `consentId` **before the copy is shown**, goes into
 * `localStorage` the instant it is given, and is retried until the backend takes
 * it. A retry of one the backend already has is a no-op there: the `event_id`
 * derives from this id, and the log's UNIQUE constraint turns the second POST
 * into the same row. `routers/consent.py`'s module docstring is where that rule
 * is written down — a surface generating the id late produces two consent
 * records for one conversation, differing only in id, with no way afterwards to
 * tell which the person actually read.
 *
 * ## The wording is cached, and the version is why that is safe
 *
 * A kiosk opened *during* an outage cannot ask the backend what to display, and
 * a consent form with no wording on it is the one thing this page must never
 * render. So the last wording this token was served is kept in `localStorage`
 * and used when the fetch fails — the same shape as `perception/mask.py`, which
 * keeps its polygon on disk for exactly this reason: the thing it needs before
 * it may run must survive the network.
 *
 * What makes it honest rather than convenient is that `copyVersion` travels
 * with the consent. If an operator changed the wording during the outage, the
 * record still says which text the person actually read, which is the whole
 * contract of that field (`docs/event-bus-spec.md` §3). The alternative —
 * turning visitors away because a server is down — loses the consent and tells
 * them nothing.
 *
 * ## What it deliberately does not do
 *
 * No count, no "you're the 41st today", no confirmation that names the
 * activation. Whoever is holding this phone is a member of the public;
 * `routers/kiosk.py` returns nothing else for the same reason. What they do get
 * is their own consent id, because it is the one thing they need to withdraw
 * later and the only copy of it that exists on their side.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { busUrl } from "@/lib/bus";

/** What the visitor's phone is told about the kiosk it is standing at. */
export interface Kiosk {
  surfaceId: string;
  label: string;
  copyText: string;
  copyVersion: string;
  tier: "T1" | "T2" | "T3";
  basis: "explicit_optin" | "contract" | "legitimate_interest";
}

/** What a visitor typed. Every field optional, as the backend's contact is. */
export interface ContactDetails {
  email?: string;
  name?: string;
  company?: string;
  title?: string;
}

/** One unsent consent. */
export interface QueuedConsent {
  consentId: string;
  at: string;
  contact: ContactDetails;
}

/**
 * `unconfigured` is the activation's missing consent wording — a 409 from the
 * backend — and not a broken link. The two are different sentences on the page
 * for the reason `/report` separates "nobody came" from "nothing was
 * measuring": one is the operator's to fix, and telling a visitor the link is
 * dead when it is the copy that is missing sends them away for nothing.
 */
export type KioskStatus = "loading" | "ready" | "invalid" | "unconfigured" | "no-backend";

const queueKey = (token: string) => `realmspace.consent.queue.${token}`;
const kioskKey = (token: string) => `realmspace.consent.kiosk.${token}`;

function readCachedKiosk(token: string): Kiosk | null {
  try {
    const raw = window.localStorage.getItem(kioskKey(token));
    const parsed = raw ? (JSON.parse(raw) as Kiosk) : null;
    // Checked rather than trusted: a half-written or older-shaped record must
    // not become a form with a blank statement above it.
    return parsed && parsed.copyText && parsed.copyVersion ? parsed : null;
  } catch {
    return null;
  }
}

function writeCachedKiosk(token: string, kiosk: Kiosk): void {
  try {
    window.localStorage.setItem(kioskKey(token), JSON.stringify(kiosk));
  } catch {
    /* see readQueue */
  }
}

function readQueue(token: string): QueuedConsent[] {
  try {
    const raw = window.localStorage.getItem(queueKey(token));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as QueuedConsent[]) : [];
  } catch {
    // A private window, cleared site data, or a browser refusing storage. The
    // form still works; it just cannot survive being closed mid-outage.
    return [];
  }
}

function writeQueue(token: string, queue: QueuedConsent[]): void {
  try {
    window.localStorage.setItem(queueKey(token), JSON.stringify(queue));
  } catch {
    /* see readQueue */
  }
}

/** A new consent id, minted before the copy is read. See the module docstring. */
export function mintConsentId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? `c_${crypto.randomUUID()}`
    : `c_${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function useKiosk(token: string | null) {
  // Derived, not stored, as `useTablet` derives its two — a state variable
  // holding something the arguments already decide is a second render pass to
  // say what was true before the effect ran.
  const [fetched, setFetched] = useState<KioskStatus>("loading");
  const [kiosk, setKiosk] = useState<Kiosk | null>(null);
  const [pending, setPending] = useState(0);
  const flushing = useRef(false);

  const status: KioskStatus = !token
    ? "invalid"
    : !busUrl()
      ? "no-backend"
      : fetched;

  useEffect(() => {
    if (!token || !busUrl()) return;

    let live = true;
    void (async () => {
      try {
        const res = await fetch(`${busUrl()}/v1/kiosk/${encodeURIComponent(token)}`);
        if (!live) return;
        if (res.status === 409) {
          // The activation has no wording. Not a bad link — see `KioskStatus`.
          setFetched("unconfigured");
          return;
        }
        if (!res.ok) {
          setFetched("invalid");
          return;
        }
        const fresh = (await res.json()) as Kiosk;
        writeCachedKiosk(token, fresh);
        setKiosk(fresh);
        setFetched("ready");
      } catch {
        // Offline at open. The token may be perfectly good, so this is not
        // `invalid`. If this kiosk has been opened before, its wording is on
        // disk and the stand keeps working; if it never has, there is nothing
        // to show and a consent form with no wording on it is the one thing
        // this page must never render.
        if (!live) return;
        const cached = readCachedKiosk(token);
        if (cached) {
          setKiosk(cached);
          setFetched("ready");
        } else {
          setFetched("loading");
        }
      }
    })();
    return () => {
      live = false;
    };
  }, [token]);

  const flush = useCallback(async () => {
    if (!token || flushing.current || !busUrl()) return;
    flushing.current = true;
    try {
      let queue = readQueue(token);
      setPending(queue.length);
      while (queue.length) {
        const [head, ...rest] = queue;
        const res = await fetch(
          `${busUrl()}/v1/kiosk/${encodeURIComponent(token)}/consent`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              consentId: head.consentId,
              at: head.at,
              // Omitted rather than sent empty, as the backend omits it: "no
              // details were given" and "these details are blank" are different
              // statements about what a person handed over.
              ...(Object.keys(head.contact).length
                ? { contact: head.contact }
                : {}),
            }),
          }
        );
        if (res.status === 404) {
          // Revoked, expired, or never real. Retrying forever would fill the
          // queue with consents that can never land; the remedy is a new link.
          setFetched("invalid");
          break;
        }
        if (res.status === 409) {
          // The wording was removed between the scan and the submit. Keep the
          // consent queued — the operator can put the copy back, and the id was
          // minted before the visitor read it, so what lands is still the
          // record of what they agreed to.
          setFetched("unconfigured");
          break;
        }
        if (!res.ok) break; // still offline, or the backend is down. Keep it.
        queue = rest;
        writeQueue(token, queue);
        setPending(queue.length);
      }
    } catch {
      /* offline. The queue is on disk and the next submit or reconnect retries. */
    } finally {
      flushing.current = false;
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;
    // The same narrow suppression as `useTablet` and `useCalibration`, for the
    // same reason: the queue lives in `localStorage`, which does not exist
    // while this page is being prerendered.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void flush();
    const onOnline = () => void flush();
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [token, flush]);

  const give = useCallback(
    (consentId: string, contact: ContactDetails) => {
      if (!token) return;
      const queued: QueuedConsent = {
        consentId,
        at: new Date().toISOString(),
        contact,
      };
      const queue = [...readQueue(token), queued];
      writeQueue(token, queue);
      setPending(queue.length);
      void flush();
    },
    [token, flush]
  );

  return { status, kiosk, pending, give };
}
