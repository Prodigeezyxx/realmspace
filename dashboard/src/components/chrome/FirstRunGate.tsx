"use client";

/**
 * realmspace — what a brand-new organisation sees first.
 *
 * The Phase 6 acceptance walk found a customer who had just signed themselves
 * up landing on the seeded demo activation: another client's name, another
 * client's venue, and a set of figures belonging to nobody. It was recorded
 * rather than fixed, because what to do about it is a product decision.
 *
 * This is that decision. **The demo stays** — it is the laptop demo's whole
 * point, it is badged on `/sessions`, and it is still one click away in the
 * switcher. What changes is that it is no longer what a real customer is shown
 * before they have anything of their own.
 *
 * ## What it deliberately does not do
 *
 * It does not make `useActiveSession()` nullable. That would be the cleanest
 * end state and it is eighteen call sites — `/live`, `/report`, `/twin`,
 * `/ask`, `/ledger`, `/agents`, `/followups`, calibration and the chrome — each
 * needing its own designed empty state. The complaint on the roadmap is about
 * the first screen, and the first screen is one component.
 *
 * ## Three routes are never gated
 *
 * `/sessions/new` is the way out and gating it would be a trap. `/sessions`
 * already has its own empty state and the demo filter. `/ops` carries the plan
 * and its limits, which is exactly what a new organisation may need to read
 * before running anything.
 */

import { ArrowUpRight, Plus, Sparkles } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { useFirstRun } from "@/lib/session/useFirstRun";

/** Routes that must stay reachable with nothing configured. */
const ALWAYS_OPEN = ["/sessions", "/ops"];

/**
 * Dismissal is per-tab, not persisted.
 *
 * Somebody who wants to look around the demo should be able to, and should get
 * the invitation back next time rather than having silently turned it off on a
 * machine they will hand to somebody else.
 */
const DISMISS_KEY = "rs:firstRunDismissed";

function readDismissed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.sessionStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

export function FirstRunGate({ children }: { children: ReactNode }) {
  const status = useFirstRun();
  const pathname = usePathname();
  const [dismissed, setDismissed] = useState(readDismissed);

  const open = ALWAYS_OPEN.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`)
  );

  // "checking" renders the page. The alternative is a flash of a first-run
  // screen on every load for every established organisation, which would be a
  // worse lie than the one this replaces.
  if (status !== "first-run" || open || dismissed) return <>{children}</>;

  return (
    <FirstRunScreen
      onDismiss={() => {
        try {
          window.sessionStorage.setItem(DISMISS_KEY, "1");
        } catch {
          /* private mode — dismissing for this render is enough */
        }
        setDismissed(true);
      }}
    />
  );
}

function FirstRunScreen({ onDismiss }: { onDismiss: () => void }) {
  return (
    <div className="min-h-[70vh] flex items-center justify-center p-6">
      <div className="panel-elevated p-12 text-center max-w-2xl">
        <div className="w-14 h-14 rounded-2xl bg-bg-elevated border border-border-subtle inline-flex items-center justify-center text-accent mb-5">
          <Sparkles size={22} />
        </div>
        <h3 className="text-2xl font-semibold tracking-tight">
          Nothing measured here yet.
        </h3>
        <p className="mt-2 text-text-secondary text-sm leading-relaxed">
          This organisation has not run an activation. Set one up — the space,
          its zones, the touchpoints and what it cost — and every screen here
          fills in from what the cameras actually see.
        </p>
        <Link
          href="/sessions/new"
          className="mt-6 inline-flex items-center gap-2 bg-accent-action text-text-inverse h-11 pl-5 pr-2.5 rounded-full font-semibold text-sm hover:bg-accent-action-bright transition-colors shadow-[var(--glow-action)]"
        >
          <Plus size={15} />
          Set up your first activation
          <span className="w-9 h-9 rounded-full bg-text-inverse text-accent inline-flex items-center justify-center">
            <ArrowUpRight size={14} />
          </span>
        </Link>
        <div className="mt-6">
          <button
            onClick={onDismiss}
            className="text-sm text-text-muted hover:text-text-secondary transition-colors underline underline-offset-4"
          >
            Look at the sample activation instead
          </button>
          <p className="mt-2 text-xs text-text-muted">
            Its figures are invented, for a client who does not exist.
          </p>
        </div>
      </div>
    </div>
  );
}
