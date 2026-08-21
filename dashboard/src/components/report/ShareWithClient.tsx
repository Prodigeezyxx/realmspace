"use client";

/**
 * "Share with client" — which, here, means giving them a viewer seat.
 *
 * The button existed on `/report` from the start with **no `onClick` at all**.
 * It rendered as a primary action on the deliverable the product is sold on and
 * did nothing — the same class of thing this page's own docstring describes
 * deleting ("1,287 visitors", "4.2× ROI"), except a lying button is worse than a
 * lying number: the operator finds out in front of the client.
 *
 * ## Why a seat and not a link
 *
 * `multi-tenant.md` §3 defines Viewer as "read-only dashboard + report — the
 * sponsor or brand stakeholder". Sharing a report with a client *is* granting
 * them that role, so this posts to `POST /v1/users` — the same endpoint the
 * onboarding invite uses, with the role fixed.
 *
 * A public share link would be nicer for the client and is deliberately not
 * this: it is a new unauthenticated read path into tenant data, and it needs a
 * signed expiring token, a revocation story and a decision about what a leaked
 * URL exposes. That is its own piece of work, not a button's implementation
 * detail.
 *
 * ## It does not claim to send anything
 *
 * There is no email provider in this repo — the absence Phase 5's SDR sits
 * behind, where the recorded decision was that it "drafts and does not send".
 * So the confirmation says what actually happened: this address can now sign in.
 * `emailSent` comes back from the API as a field precisely so this component
 * cannot get that wrong by assuming.
 */

import { Check, Share2, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { busEmail, busUrl, ensureToken, isRemoteBusEnabled } from "@/lib/bus";

interface InviteResult {
  email: string;
  role: string;
  emailSent: boolean;
  detail: string;
}

export function ShareWithClient() {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<InviteResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function share() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const token = await ensureToken(busEmail());
      if (!token) {
        setError("Could not authenticate with the bus.");
        return;
      }
      const res = await fetch(`${busUrl()}/v1/users`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ email: email.trim(), role: "viewer" }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        // The backend's refusals explain themselves — a 409 says why moving
        // somebody between organisations is not on offer, a 403 names the
        // capability. Passing the sentence through is the whole value of
        // having written it there.
        setError(
          typeof body?.detail === "string"
            ? body.detail
            : `The bus returned ${res.status}.`
        );
        return;
      }
      setResult(body as InviteResult);
      setEmail("");
    } catch {
      setError("The bus is unreachable.");
    } finally {
      setBusy(false);
    }
  }

  if (!isRemoteBusEnabled()) {
    // No backend means no accounts to grant. Rendering the button anyway would
    // put it straight back to being decorative, which is the bug.
    return null;
  }

  return (
    <div className="relative">
      <Button
        variant="secondary"
        size="sm"
        icon={<Share2 size={14} />}
        onClick={() => setOpen((v) => !v)}
      >
        Share with client
      </Button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-[22rem] z-20 panel p-4 space-y-3 shadow-[var(--shadow-lg)]">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold tracking-tight">
                Give them a viewer seat
              </p>
              <p className="text-xs text-text-muted mt-1 leading-relaxed">
                They will be able to sign in and read this session&rsquo;s
                dashboard and report — and nothing else. Viewers cannot see
                leads or contact details.
              </p>
            </div>
            <button
              type="button"
              aria-label="Close"
              className="text-text-muted hover:text-text-secondary"
              onClick={() => setOpen(false)}
            >
              <X size={14} />
            </button>
          </div>

          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@brand.example"
            className="w-full text-sm bg-bg-canvas border border-border-hairline rounded-lg px-3 py-2 placeholder:text-text-muted"
          />

          <Button
            variant="primary"
            size="sm"
            fullWidth
            icon={<Check size={14} />}
            disabled={busy || !email.trim()}
            onClick={() => void share()}
          >
            {busy ? "Granting…" : "Grant access"}
          </Button>

          {result && (
            <p className="text-xs text-text-secondary leading-relaxed">
              {result.detail}
              {!result.emailSent && (
                <>
                  {" "}
                  <span className="text-text-muted">
                    Send them the link yourself — we have no mail server, and
                    saying &ldquo;invitation sent&rdquo; when nothing was sent
                    would be worse than saying nothing.
                  </span>
                </>
              )}
            </p>
          )}
          {error && (
            <p className="text-xs text-accent-red leading-relaxed">{error}</p>
          )}
        </div>
      )}
    </div>
  );
}
