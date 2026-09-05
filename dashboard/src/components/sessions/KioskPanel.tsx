"use client";

/**
 * Putting a consent kiosk on a plinth.
 *
 * `consent.captured` has had every reader since Phase 4 — the identity
 * consumer, attribution, five CRM adapters, the follow-up drafter, the ledger —
 * and its only producer was `curl`. `roadmap.md` said so in one line: *"the
 * surfaces themselves (badge/QR/kiosk hardware) are not built"*. A phone
 * pointed at a QR code is that surface, and this is where an operator makes
 * one.
 *
 * ## The link is shown once
 *
 * As `TabletPanel`'s is, and for the same reason: the backend stores a sha256
 * and returns the token in exactly one response. The operator's copy is the QR
 * code they print. Losing it is not a lockout — mint another and withdraw the
 * old one.
 *
 * ## Why it says what it cannot do
 *
 * A kiosk has no camera, so a consent is joined to a visitor's path only when
 * exactly one person was in the surface's zone. Unlike a tap, the consent is
 * **always recorded** — it is the visitor's own claim about themselves, and the
 * path is the second half. That difference is stated here rather than left to
 * be worked out from a report where the two figures disagree.
 */

import { Copy, Link2, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { useKiosks } from "@/lib/ops/useKiosks";
import type { Touchpoint } from "@/lib/session/types";

export function KioskPanel({
  sessionId,
  touchpoints,
}: {
  sessionId: string;
  touchpoints: Touchpoint[];
}) {
  const { status, kiosks, detail, mint, revoke } = useKiosks(sessionId);
  const [minted, setMinted] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  async function place(surfaceId: string) {
    setBusy(surfaceId);
    const url = await mint(surfaceId);
    if (url) setMinted((prev) => ({ ...prev, [surfaceId]: url }));
    setBusy(null);
  }

  async function copy(surfaceId: string, url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(surfaceId);
      window.setTimeout(() => setCopied(null), 1500);
    } catch {
      // Clipboard refused (an insecure origin, or the permission). The link is
      // rendered in full beside the button precisely so this is a nuisance and
      // not a dead end.
    }
  }

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <ShieldCheck size={14} />
          Consent kiosks
        </span>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-text-secondary leading-relaxed">
          A phone or plinth opening this link shows the wording you set for this
          activation and records one consent per visitor, with no account and no
          app. It is the only thing that turns an anonymous visit into a lead —
          the identified half of the funnel, and everything the CRM ever
          receives.
        </p>
        <p className="text-sm text-text-secondary leading-relaxed">
          A kiosk has no camera, so a visitor&rsquo;s <em>path</em> is attached
          only when exactly one person was standing in the surface&rsquo;s zone.
          The consent itself is always recorded &mdash; it is what the visitor
          said about themselves, and we do not throw it away for want of knowing
          where they walked.
        </p>

        {status === "offline" && (
          <p className="text-sm text-text-muted leading-relaxed">{detail}</p>
        )}

        {status !== "offline" && !touchpoints.length && (
          <p className="text-sm text-text-muted">
            This activation has no surfaces yet. Add one in the session wizard
            and put the kiosk on it.
          </p>
        )}

        {status !== "offline" &&
          touchpoints.map((touchpoint) => {
            const live = kiosks.filter(
              (k) => k.surfaceId === touchpoint.id && k.active
            );
            const url = minted[touchpoint.id];
            return (
              <div
                key={touchpoint.id}
                className="border-b border-border-hairline last:border-0 pb-4 last:pb-0 space-y-2"
              >
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div>
                    <p className="text-sm text-text-primary">{touchpoint.name}</p>
                    <p className="text-xs text-text-muted">
                      {touchpoint.zoneId ? (
                        <>in {touchpoint.zoneId}</>
                      ) : (
                        // Stated where the operator can still fix it. Milder
                        // than the tablet's version of this line, and honestly
                        // so: the consent still counts, the path does not.
                        <>in no zone — consents will be recorded without a path</>
                      )}
                    </p>
                  </div>
                  <Button
                    variant="secondary"
                    size="sm"
                    icon={<Link2 size={14} />}
                    disabled={busy === touchpoint.id}
                    onClick={() => void place(touchpoint.id)}
                  >
                    {busy === touchpoint.id ? "Minting…" : "New kiosk link"}
                  </Button>
                </div>

                {url && (
                  <div className="flex items-center gap-2 flex-wrap">
                    <code className="text-xs bg-bg-canvas border border-border-hairline rounded-lg px-3 py-2 break-all">
                      {url}
                    </code>
                    <Button
                      variant="secondary"
                      size="sm"
                      icon={<Copy size={14} />}
                      onClick={() => void copy(touchpoint.id, url)}
                    >
                      {copied === touchpoint.id ? "Copied" : "Copy"}
                    </Button>
                    <span className="text-xs text-text-muted">
                      Shown once — print the QR code from it now.
                    </span>
                  </div>
                )}

                {live.map((kiosk) => (
                  <div
                    key={kiosk.id}
                    className="flex items-center justify-between gap-3 text-xs text-text-muted"
                  >
                    <span>
                      {/* Scans, not consents. Somebody who read the wording and
                          walked away is counted here and nowhere else — the log
                          has only the people who agreed. */}
                      …{kiosk.hint} · {kiosk.useCount}{" "}
                      {kiosk.useCount === 1 ? "scan" : "scans"}
                      {kiosk.lastUsedAt
                        ? `, last ${new Date(kiosk.lastUsedAt).toLocaleString()}`
                        : ", never opened"}
                    </span>
                    <button
                      type="button"
                      onClick={() => void revoke(kiosk.id)}
                      className="inline-flex items-center gap-1 hover:text-accent-red transition-colors"
                    >
                      <Trash2 size={12} />
                      Withdraw
                    </button>
                  </div>
                ))}
              </div>
            );
          })}

        {status === "error" && detail && (
          <p className="text-xs text-accent-red leading-relaxed">{detail}</p>
        )}
      </div>
    </Panel>
  );
}
