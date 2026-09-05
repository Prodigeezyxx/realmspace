"use client";

/**
 * The screen on a tablet at a booth touchpoint. One button.
 *
 * ## Outside the app shell, like `/shared`
 *
 * No nav rail, no status bar, no session switcher — every one of those assumes
 * a signed-in operator, and none of them is anything to put in front of a
 * member of the public standing at a stand. What this page shows is the
 * touchpoint's own name and a button, which is also the honest shape of what
 * the link grants: one `surface.touched` per press and no reads at all.
 *
 * ## The token is a query parameter, for `/shared`'s reason
 *
 * `output: export` means a dynamic segment would need `generateStaticParams`,
 * and these tokens do not exist at build time. A token in a query string
 * reaches server logs and referrer headers; the mitigation is the same as the
 * share link's — a short life, and revocation from the activation's settings.
 *
 * ## Why the feedback is deliberately thin
 *
 * A press acknowledges itself and says nothing about the activation. No running
 * total, no "you're the 41st today": the numbers belong to the client, not to
 * whoever is standing here. The only status shown is about this tablet's own
 * unsent taps, because that is the one thing the person setting it up needs to
 * be able to see from across the stand.
 */

import { Check, Hand, WifiOff } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { useTablet } from "@/lib/touch/useTablet";

export default function TouchpointPage() {
  // `useSearchParams` needs a Suspense boundary under static export.
  return (
    <Suspense fallback={<Centered title="Opening…" />}>
      <Touchpoint />
    </Suspense>
  );
}

function Centered({ title, hint }: { title: string; hint?: string }) {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-3 p-8 text-center bg-bg-canvas">
      <h1 className="font-display text-2xl text-text-primary">{title}</h1>
      {hint && <p className="text-sm text-text-secondary max-w-sm">{hint}</p>}
    </main>
  );
}

function Touchpoint() {
  const token = useSearchParams().get("t");
  const { status, tablet, pending, tap } = useTablet(token);
  const [flashing, setFlashing] = useState(false);

  useEffect(() => {
    if (!flashing) return;
    const timer = window.setTimeout(() => setFlashing(false), 900);
    return () => window.clearTimeout(timer);
  }, [flashing]);

  if (status === "invalid") {
    return (
      <Centered
        title="This touchpoint link is no longer valid"
        hint="It may have expired or been withdrawn. Ask whoever set the stand up for a new one."
      />
    );
  }
  if (status === "unconfigured") {
    return (
      <Centered
        title="No backend configured"
        hint="This tablet has nowhere to send a tap. Set NEXT_PUBLIC_BUS_URL on the build it is opening."
      />
    );
  }
  if (status === "loading" || !tablet) {
    return <Centered title="Opening…" hint="Waiting for the stand's network." />;
  }

  return (
    <main className="min-h-screen flex flex-col bg-bg-canvas select-none">
      <header className="px-6 pt-8 text-center">
        <h1 className="font-display text-3xl text-text-primary">{tablet.label}</h1>
      </header>

      <button
        type="button"
        onClick={() => {
          tap();
          setFlashing(true);
        }}
        className={[
          "flex-1 m-6 rounded-3xl border transition-colors duration-150",
          "flex flex-col items-center justify-center gap-4",
          // The Action accent, which `brand.md` §3 reserves for primary CTAs —
          // and this page is one button that is nothing but a call to action.
          flashing
            ? "bg-accent-action-soft border-accent-action"
            : "bg-bg-panel border-border-subtle active:bg-accent-action-soft",
        ].join(" ")}
        // A tablet is pressed by people who are not looking at it carefully.
        aria-label={`Record an interaction with ${tablet.label}`}
      >
        {flashing ? (
          <>
            <Check size={64} className="text-accent-action" />
            <span className="text-lg text-text-primary">Thanks</span>
          </>
        ) : (
          <>
            <Hand size={64} className="text-accent" />
            <span className="text-lg text-text-secondary">Tap to try this</span>
          </>
        )}
      </button>

      <footer className="px-6 pb-8 h-6 text-center text-xs text-text-secondary">
        {pending > 0 && (
          <span className="inline-flex items-center gap-2">
            <WifiOff size={12} />
            {pending} {pending === 1 ? "tap" : "taps"} waiting for the network
          </span>
        )}
      </footer>
    </main>
  );
}
