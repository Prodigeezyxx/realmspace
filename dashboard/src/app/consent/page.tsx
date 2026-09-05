"use client";

/**
 * The consent kiosk: a phone or a plinth screen a visitor reaches from a QR
 * code. The producer `consent.captured` never had.
 *
 * ## Outside the app shell, like `/touch` and `/shared`
 *
 * No nav rail, no status bar, no session switcher — each of those assumes a
 * signed-in operator, and none of them belongs in front of a member of the
 * public. What this page shows is the wording the operator wrote, what agreeing
 * means, and two buttons.
 *
 * ## The token is a query parameter, for `/shared`'s reason
 *
 * `output: export` means a dynamic segment would need `generateStaticParams`,
 * and these tokens do not exist at build time. A token in a query string
 * reaches server logs and referrer headers; the mitigation is the same as the
 * share link's — a short life, and revocation from the activation's settings.
 *
 * ## Declining sends nothing
 *
 * `consent-and-identity.md` §4: *"declines → stays anonymous (T0). Path still
 * counts for ROI."* So the decline button is not a `consent.given` with a "no"
 * in it. Nothing is recorded, because there is nothing to record and no consent
 * to keep it under — the anonymous path was never consent-gated, and it carries
 * on measuring zones and dwells whatever this screen is used for. The page says
 * that in as many words rather than leaving somebody to assume they have been
 * switched off.
 *
 * ## What the receipt is for
 *
 * The consent id is shown once it is given, because it is the one identifier the
 * visitor holds that would let them withdraw later — and the only copy of it on
 * their side. Not a count, not the activation's name, nothing about anybody
 * else: `routers/kiosk.py` returns none of that for the same reason.
 */

import { Check, ShieldCheck, WifiOff } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { mintConsentId, useKiosk, type ContactDetails } from "@/lib/consent/useKiosk";

export default function ConsentPage() {
  // `useSearchParams` needs a Suspense boundary under static export.
  return (
    <Suspense fallback={<Centered title="Opening…" />}>
      <Kiosk />
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

/** What each tier means, in the visitor's words rather than the schema's. */
const TIER_MEANING: Record<string, string> = {
  T1: "Keep my details with this visit.",
  T2: "Keep my details, and you may contact me about it.",
  T3: "Keep my details, contact me, and look me up to save me typing.",
};

function Kiosk() {
  const token = useSearchParams().get("t");
  const { status, kiosk, pending, give } = useKiosk(token);

  // Minted **before the copy is shown**, which is the whole of why it is here
  // and not inside `give()`. See `useKiosk`'s module docstring: an id assigned
  // when the request is sent makes a retry over bad wifi two consent records
  // for one conversation.
  const consentId = useMemo(() => mintConsentId(), []);

  const [contact, setContact] = useState<ContactDetails>({});
  const [given, setGiven] = useState(false);
  const [declined, setDeclined] = useState(false);

  useEffect(() => {
    if (!given) return;
    // Nothing to time out — the receipt stays until the next visitor reloads
    // the page. A screen that cleared itself would leave somebody unsure
    // whether it took.
  }, [given]);

  if (status === "invalid") {
    return (
      <Centered
        title="This consent link is no longer valid"
        hint="It may have expired or been withdrawn. Ask a member of staff for a new one."
      />
    );
  }
  if (status === "no-backend") {
    return (
      <Centered
        title="No backend configured"
        hint="This kiosk has nowhere to record a consent. Set NEXT_PUBLIC_BUS_URL on the build it is opening."
      />
    );
  }
  if (status === "unconfigured") {
    return (
      <Centered
        title="This kiosk is not ready yet"
        hint="Nobody has set the wording for this activation, and we will not ask you to agree to something we cannot show you. Please tell a member of staff."
      />
    );
  }
  if (status === "loading" || !kiosk) {
    return <Centered title="Opening…" hint="Waiting for the stand's network." />;
  }

  if (declined) {
    return (
      <Centered
        title="Nothing was recorded"
        hint="No details were taken and nothing was sent. You are welcome to look round the stand."
      />
    );
  }

  if (given) {
    return (
      <main className="min-h-screen flex flex-col items-center justify-center gap-4 p-8 text-center bg-bg-canvas">
        <Check size={56} className="text-accent" />
        <h1 className="font-display text-2xl text-text-primary">Thank you</h1>
        <p className="text-sm text-text-secondary max-w-sm">
          You agreed to {kiosk.copyVersion}. Keep this reference if you would
          like us to remove your details later.
        </p>
        <code className="font-mono text-xs text-text-secondary break-all max-w-sm">
          {consentId}
        </code>
        {pending > 0 && (
          <span className="inline-flex items-center gap-2 text-xs text-text-secondary">
            <WifiOff size={12} />
            waiting for the stand&apos;s network — it will send itself
          </span>
        )}
      </main>
    );
  }

  return (
    <main className="min-h-screen flex flex-col bg-bg-canvas">
      <header className="px-6 pt-8 flex items-center gap-3">
        <ShieldCheck size={20} className="text-accent shrink-0" />
        <h1 className="font-display text-2xl text-text-primary">{kiosk.label}</h1>
      </header>

      <section className="px-6 pt-6 space-y-4">
        {/* The operator's exact wording, and the version of it that will be
            recorded. Rendered as text, never as markup: this string comes from
            a session config and a kiosk is not a place to interpret one. */}
        <p className="text-sm text-text-primary whitespace-pre-wrap">
          {kiosk.copyText}
        </p>
        <p className="text-xs text-text-secondary">
          {TIER_MEANING[kiosk.tier] ?? ""}{" "}
          <span className="font-mono">{kiosk.copyVersion}</span>
        </p>
      </section>

      <form
        className="px-6 pt-6 flex-1 flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          const trimmed: ContactDetails = Object.fromEntries(
            Object.entries(contact)
              .map(([k, v]) => [k, (v ?? "").trim()])
              .filter(([, v]) => v !== "")
          );
          give(consentId, trimmed);
          setGiven(true);
        }}
      >
        <Field
          label="Email"
          type="email"
          value={contact.email ?? ""}
          onChange={(email) => setContact((c) => ({ ...c, email }))}
          required
        />
        <Field
          label="Name"
          value={contact.name ?? ""}
          onChange={(name) => setContact((c) => ({ ...c, name }))}
        />
        <Field
          label="Company"
          value={contact.company ?? ""}
          onChange={(company) => setContact((c) => ({ ...c, company }))}
        />

        <div className="mt-auto pb-8 pt-6 flex flex-col gap-3">
          <button
            type="submit"
            className="rounded-xl px-5 py-4 text-base font-medium bg-accent-action text-bg-canvas active:opacity-90"
          >
            I agree
          </button>
          {/* Not a quiet link. Declining is a real answer and this page must not
              make it the hard one to find. */}
          <button
            type="button"
            onClick={() => setDeclined(true)}
            className="rounded-xl px-5 py-4 text-base border border-border-subtle text-text-secondary"
          >
            No thanks
          </button>
        </div>
      </form>
    </main>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  required = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  required?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-text-secondary">
        {label}
        {!required && " (optional)"}
      </span>
      <input
        type={type}
        value={value}
        required={required}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-lg bg-bg-panel border border-border-subtle px-3 py-3 text-base text-text-primary"
      />
    </label>
  );
}
