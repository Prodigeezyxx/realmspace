"use client";

/**
 * Handing a tablet to a touchpoint.
 *
 * `surface.interaction` has had every reader since Phase 2 and no producer, so
 * the Engagement layer of the report's four rendered `0*` on every real
 * activation. The roadmap's reason was that "real interactions need booth
 * hardware" — true of an RFID plinth, and not true of a tablet in a browser.
 * This is where an operator turns one into a producer.
 *
 * ## The link is shown once
 *
 * The backend stores a sha256 and returns the token in exactly one response, so
 * this panel holds it for as long as it is open and no longer. Losing it is not
 * a lockout: mint another and withdraw the old one, which is the same thing an
 * operator should do about a tablet that has gone missing.
 *
 * ## Why it says what it cannot do
 *
 * A tablet has no camera, so a tap can only be attributed to a visitor when
 * exactly one person was in the touchpoint's zone. That is stated here rather
 * than discovered from a report where the two tallies disagree — and it is why
 * a touchpoint with no zone is called out as counted but unattributable.
 */

import { Copy, Hand, Link2, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { useTablets } from "@/lib/ops/useTablets";
import type { Touchpoint } from "@/lib/session/types";

export function TabletPanel({
  sessionId,
  touchpoints,
}: {
  sessionId: string;
  touchpoints: Touchpoint[];
}) {
  const { status, tablets, detail, mint, revoke } = useTablets(sessionId);
  const [minted, setMinted] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  async function hand(surfaceId: string) {
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
          <Hand size={14} />
          Tablets at touchpoints
        </span>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-text-secondary leading-relaxed">
          A tablet running this link records one interaction per tap, with no
          account and no app. It is the only thing that produces the Engagement
          layer&rsquo;s surface interactions &mdash; without one, that figure on
          the report is an absent signal rather than a zero.
        </p>
        <p className="text-sm text-text-secondary leading-relaxed">
          A tablet has no camera, so a tap is credited to a visitor only when
          <em> exactly one</em> person was standing in the touchpoint&rsquo;s
          zone. Every tap is counted either way; the ones we cannot name are the
          difference between the two figures.
        </p>

        {status === "offline" && (
          <p className="text-sm text-text-muted leading-relaxed">{detail}</p>
        )}

        {status !== "offline" && !touchpoints.length && (
          <p className="text-sm text-text-muted">
            This activation has no touchpoints yet. They come from the session
            wizard.
          </p>
        )}

        {status !== "offline" &&
          touchpoints.map((touchpoint) => {
            const live = tablets.filter(
              (t) => t.surfaceId === touchpoint.id && t.active
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
                        // Stated where the operator can still fix it, rather
                        // than as a silent shortfall on the report.
                        <>in no zone — taps will be counted and not attributed</>
                      )}
                    </p>
                  </div>
                  <Button
                    variant="secondary"
                    size="sm"
                    icon={<Link2 size={14} />}
                    disabled={busy === touchpoint.id}
                    onClick={() => void hand(touchpoint.id)}
                  >
                    {busy === touchpoint.id ? "Minting…" : "New tablet link"}
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
                      Shown once — open it on the tablet now.
                    </span>
                  </div>
                )}

                {live.map((tablet) => (
                  <div
                    key={tablet.id}
                    className="flex items-center justify-between gap-3 text-xs text-text-muted"
                  >
                    <span>
                      …{tablet.hint} · {tablet.useCount}{" "}
                      {tablet.useCount === 1 ? "tap" : "taps"}
                      {tablet.lastUsedAt
                        ? `, last ${new Date(tablet.lastUsedAt).toLocaleString()}`
                        : ", never used"}
                    </span>
                    <button
                      type="button"
                      onClick={() => void revoke(tablet.id)}
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
