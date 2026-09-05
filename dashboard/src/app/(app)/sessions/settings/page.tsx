"use client";

/**
 * realmspace — an activation's settings, after the wizard.
 *
 * Until this screen existed, every parameter the ROI report divides by was
 * write-once: set in the five-step wizard, then unreachable. The consequences
 * were not theoretical. `roi-framework.md` §3 says influenced revenue is *the
 * client's own figure*, supplied after the activation — and it is the numerator
 * of the ratio on the front of the report, with no field anywhere to type it
 * into. The activation cost, its denominator, is often only final once the
 * build is paid for. Both meant `curl`.
 *
 * Calibrates the **active session**, like every other session-scoped screen
 * here: `output: export` means a dynamic segment would need
 * `generateStaticParams` and there is no build-time list of activations.
 *
 * ## Two kinds of change, kept apart on purpose
 *
 * `roi-framework.md` §5 asks for the engagement threshold, the attribution
 * model and its window to be agreed with the client *before doors open*, "so
 * the ROI number is pre-agreed and un-arguable afterwards". Everything else —
 * the cost, the client's revenue figure, a mistyped venue, the consent wording
 * — is a correction, and refusing those would be worse than allowing them.
 *
 * So the scoring rules sit in their own panel, which says what they are, and
 * changing one after visitors have been counted is recorded on the log as
 * `session.config_updated` and shown on the report. Not blocked: an operator
 * who set 30 seconds and meant 60 must be able to fix it. Not silent either.
 */

import { AlertTriangle, Save } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  Field,
  NumberInput,
  Select,
  TextArea,
  TextInput,
} from "@/components/sessions/WizardChrome";
import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { fetchSessionEvents } from "@/lib/bus/remote";
import { useTenantId } from "@/lib/tenant/useTenantId";
import { copyVersionFor } from "@/lib/session/consentCopy";
import { sessionActions, useActiveSession } from "@/lib/session/store";
import {
  useSessionSettings,
  type SettingsPatch,
} from "@/lib/ops/useSessionSettings";

/** The form's own shape: strings, because that is what inputs hold. */
interface Draft {
  client: string;
  campaign: string;
  venue: string;
  city: string;
  activationCost: string;
  currency: string;
  revenueInfluenced: string;
  qualifiedLeads: string;
  engagedThresholdSeconds: string;
  attributionModel: string;
  attributionWindowDays: string;
  consentCopy: string;
  consentTier: "T1" | "T2" | "T3";
  anonymousHandoffs: boolean;
  insightIntervalMinutes: string;
}

const EMPTY: Draft = {
  client: "",
  campaign: "",
  venue: "",
  city: "",
  activationCost: "",
  currency: "USD",
  revenueInfluenced: "",
  qualifiedLeads: "",
  engagedThresholdSeconds: "60",
  attributionModel: "influenced",
  attributionWindowDays: "90",
  consentCopy: "",
  consentTier: "T2",
  anonymousHandoffs: false,
  insightIntervalMinutes: "10",
};

const str = (v: string | null | undefined) => v ?? "";
const num = (v: number | null | undefined) => (v == null ? "" : String(v));

/** `""` means "not set", which the backend stores as null — not as zero. */
const toNumber = (v: string): number | null =>
  v.trim() === "" ? null : Number(v);

export default function SettingsPage() {
  const session = useActiveSession();
  const tenantId = useTenantId();
  const { status, config, detail, save } = useSessionSettings(session.id);

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [hasMeasured, setHasMeasured] = useState(false);

  /**
   * Seeded from the backend where there is one, and from this browser's own
   * session where there is not — the laptop demo, where the store is the only
   * copy that exists.
   *
   * Adjusted **during render** rather than in an effect, which is React's own
   * answer for state derived from props and the reason this repo replaced its
   * mount guards with `useIsHydrated` when CI started gating on lint: an effect
   * here costs a second render pass to say something already known.
   *
   * **And never over something the operator has typed**, which the browser walk
   * found the hard way. The backend's answer arrives a moment after the screen
   * opens, and re-seeding on it silently emptied a field somebody had already
   * filled in — the save then reported "Saved." with nothing in it, which is
   * the worst version of this: a lying confirmation. So a touched form is left
   * alone, and the seed is what happens to an untouched one.
   */
  const seedKey = `${session.id}:${config ? "remote" : "local"}`;
  const [seeded, setSeeded] = useState<string | null>(null);
  // Whether anything has been typed since the last seed or save.
  const [dirty, setDirty] = useState(false);
  if (seeded !== seedKey && !dirty) {
    const m = session.measurement ?? {};
    const p = session.privacy;
    setSeeded(seedKey);
    setDraft({
      client: str(config?.client ?? session.client),
      campaign: str(config?.campaign ?? session.name),
      venue: str(config?.venue ?? session.venue),
      city: str(config?.city ?? session.city),
      activationCost: num(config?.activationCost ?? m.activationCost),
      currency: str(config?.currency ?? m.currency) || "USD",
      revenueInfluenced: num(config?.revenueInfluenced ?? m.revenueInfluenced),
      qualifiedLeads: num(config?.qualifiedLeads ?? m.qualifiedLeads),
      engagedThresholdSeconds: num(
        config?.engagedThresholdSeconds ?? m.engagedThresholdSec ?? 60
      ),
      attributionModel: str(
        config?.attributionModel ?? m.attributionModel ?? "influenced"
      ),
      attributionWindowDays: num(config?.attributionWindowDays ?? 90),
      consentCopy: str(config?.consentCopy ?? p?.consentCopy),
      consentTier: config?.consentTier ?? p?.consentTier ?? "T2",
      anonymousHandoffs: config?.anonymousHandoffs ?? false,
      insightIntervalMinutes: num(config?.insightIntervalMinutes ?? 10),
    });
  }

  // Whether anybody has been counted yet, which is what makes a scoring change
  // a departure from something rather than the initial agreement. Asked of the
  // backend rather than of this browser's mirrored log: an operator on a second
  // laptop has an empty local log and the same activation.
  useEffect(() => {
    let live = true;
    void (async () => {
      const rows = await fetchSessionEvents(tenantId, session.id, {
        types: ["perception.detection", "spatial.zone_enter"],
        maxPages: 1,
      });
      if (live) setHasMeasured(rows.length > 0);
    })();
    return () => {
      live = false;
    };
  }, [tenantId, session.id]);

  const consentVersion = useMemo(
    () => copyVersionFor(draft.consentCopy),
    [draft.consentCopy]
  );

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setSaved(false);
    setDirty(true);
    setDraft((d) => ({ ...d, [key]: value }));
  }

  /**
   * Only what moved. The endpoint applies exactly the fields a request set, so
   * anything omitted here keeps the value it has — including the zones,
   * touchpoints and cameras this screen must never send.
   */
  function patch(): SettingsPatch {
    const p: SettingsPatch = {};
    const put = <K extends keyof SettingsPatch>(
      key: K,
      next: SettingsPatch[K],
      current: unknown
    ) => {
      if (next !== current) p[key] = next;
    };

    put("client", draft.client.trim() || null, config?.client ?? null);
    put("campaign", draft.campaign.trim() || null, config?.campaign ?? null);
    put("venue", draft.venue.trim() || null, config?.venue ?? null);
    put("city", draft.city.trim() || null, config?.city ?? null);
    put(
      "activationCost",
      toNumber(draft.activationCost),
      config?.activationCost ?? null
    );
    put("currency", draft.currency.trim().toUpperCase(), config?.currency);
    put(
      "revenueInfluenced",
      toNumber(draft.revenueInfluenced),
      config?.revenueInfluenced ?? null
    );
    put(
      "qualifiedLeads",
      toNumber(draft.qualifiedLeads),
      config?.qualifiedLeads ?? null
    );

    const threshold = Number(draft.engagedThresholdSeconds);
    if (Number.isFinite(threshold) && threshold > 0) {
      put("engagedThresholdSeconds", threshold, config?.engagedThresholdSeconds);
    }
    put("attributionModel", draft.attributionModel, config?.attributionModel);
    put(
      "attributionWindowDays",
      Number(draft.attributionWindowDays),
      config?.attributionWindowDays
    );
    put("anonymousHandoffs", draft.anonymousHandoffs, config?.anonymousHandoffs);
    const interval = Number(draft.insightIntervalMinutes);
    if (Number.isFinite(interval) && interval >= 1) {
      put("insightIntervalMinutes", interval, config?.insightIntervalMinutes);
    }

    const copy = draft.consentCopy.trim();
    if (copy !== (config?.consentCopy ?? "")) {
      p.consentCopy = copy || null;
      // The version follows the words rather than being typed — see
      // `lib/session/consentCopy.ts`. Sent together, always, so a corrected
      // sentence cannot keep the old version's name.
      p.consentCopyVersion = copy ? consentVersion : null;
    }
    put("consentTier", draft.consentTier, config?.consentTier);
    return p;
  }

  async function commit() {
    setSaving(true);
    setError(null);
    const p = patch();
    const failure = await save(p);
    setSaving(false);
    if (failure) {
      setError(failure);
      return;
    }
    // Mirrored locally too: with no backend this store is the only copy, and
    // everything that reads a figure resolves the two through
    // `lib/roi/measurement.ts` rather than choosing one.
    sessionActions.updateSession(session.id, {
      client: draft.client.trim() || undefined,
      name: draft.campaign.trim() || session.name,
      venue: draft.venue.trim() || session.venue,
      city: draft.city.trim() || undefined,
      measurement: {
        ...(session.measurement ?? {}),
        engagedThresholdSec: Number(draft.engagedThresholdSeconds) || 60,
        activationCost: toNumber(draft.activationCost) ?? undefined,
        currency: draft.currency.trim().toUpperCase() || "USD",
        attributionModel: draft.attributionModel as never,
        revenueInfluenced: toNumber(draft.revenueInfluenced) ?? undefined,
        qualifiedLeads: toNumber(draft.qualifiedLeads) ?? undefined,
      },
      privacy: {
        ...session.privacy,
        consentCopy: draft.consentCopy.trim() || undefined,
        consentCopyVersion: draft.consentCopy.trim() ? consentVersion : undefined,
        consentTier: draft.consentTier,
      },
    });
    setDirty(false);
    setSaved(true);
  }

  const scoringMoved =
    hasMeasured &&
    config != null &&
    (Number(draft.engagedThresholdSeconds) !== config.engagedThresholdSeconds ||
      draft.attributionModel !== config.attributionModel ||
      Number(draft.attributionWindowDays) !== (config.attributionWindowDays ?? 90));

  return (
    <div className="p-5 space-y-5 max-w-[900px] mx-auto">
      <header className="space-y-1">
        <h1 className="font-display text-2xl text-text-primary">
          {session.name} — settings
        </h1>
        <p className="text-sm text-text-secondary">
          What this activation is scored by. The wizard sets these once; this is
          where they are corrected afterwards.
        </p>
      </header>

      {status === "offline" && (
        <p className="text-sm text-text-muted">{detail}</p>
      )}
      {status === "error" && detail && (
        <p className="text-sm text-accent-red">{detail}</p>
      )}

      <Panel title="The activation">
        <div className="grid md:grid-cols-2 gap-5">
          <Field label="Client" hint="The name on the front of the report.">
            <TextInput
              value={draft.client}
              onChange={(e) => set("client", e.target.value)}
            />
          </Field>
          <Field label="Campaign">
            <TextInput
              value={draft.campaign}
              onChange={(e) => set("campaign", e.target.value)}
            />
          </Field>
          <Field label="Venue">
            <TextInput
              value={draft.venue}
              onChange={(e) => set("venue", e.target.value)}
            />
          </Field>
          <Field label="City">
            <TextInput
              value={draft.city}
              onChange={(e) => set("city", e.target.value)}
            />
          </Field>
        </div>
      </Panel>

      <Panel title="The figures the report divides by">
        <p className="text-sm text-text-secondary leading-relaxed mb-4">
          Influenced revenue and qualified leads are <em>the client&rsquo;s own
          figures</em>, not measured here, and every surface that shows them says
          so. Left blank they read as unknown rather than as zero &mdash; which
          are different answers, and the report must not conflate them.
        </p>
        <div className="grid md:grid-cols-2 gap-5">
          <Field
            label="Activation cost"
            hint="The denominator of cost per engaged visit, cost per lead and the ROI ratio."
          >
            <NumberInput
              min={0}
              value={draft.activationCost}
              onChange={(e) => set("activationCost", e.target.value)}
            />
          </Field>
          <Field label="Currency" hint="Three letters.">
            <TextInput
              maxLength={3}
              value={draft.currency}
              onChange={(e) => set("currency", e.target.value)}
            />
          </Field>
          <Field
            label="Influenced revenue"
            hint="Supplied by the client, not measured by realmspace."
          >
            <NumberInput
              min={0}
              value={draft.revenueInfluenced}
              onChange={(e) => set("revenueInfluenced", e.target.value)}
            />
          </Field>
          <Field label="Qualified leads" hint="The client's own count.">
            <NumberInput
              min={0}
              value={draft.qualifiedLeads}
              onChange={(e) => set("qualifiedLeads", e.target.value)}
            />
          </Field>
        </div>
      </Panel>

      <Panel
        title={
          <span className="flex items-center gap-2">
            Scoring rules
            {hasMeasured && <Pill variant="warn">this activation has run</Pill>}
          </span>
        }
      >
        <p className="text-sm text-text-secondary leading-relaxed mb-4">
          Agreed with the client <em>before doors open</em>, so the ROI number is
          pre-agreed and un-arguable afterwards (`roi-framework.md` §5). You can
          still correct one &mdash; somebody who typed 30 seconds and meant 60
          must be able to &mdash; but once visitors have been counted the change
          is recorded on the log and shown on the report.
        </p>
        <div className="grid md:grid-cols-3 gap-5">
          <Field
            label="Engaged above (seconds)"
            hint="Dwell longer than this counts as an engaged visit."
          >
            <NumberInput
              min={1}
              value={draft.engagedThresholdSeconds}
              onChange={(e) => set("engagedThresholdSeconds", e.target.value)}
            />
          </Field>
          <Field label="Attribution model">
            <Select
              value={draft.attributionModel}
              onChange={(e) => set("attributionModel", e.target.value)}
            >
              <option value="influenced">Influenced</option>
              <option value="first_touch">First touch</option>
              <option value="last_touch">Last touch</option>
              <option value="linear">Linear</option>
              <option value="time_decay">Time decay</option>
            </Select>
          </Field>
          <Field label="Attribution window">
            <Select
              value={draft.attributionWindowDays}
              onChange={(e) => set("attributionWindowDays", e.target.value)}
            >
              <option value="30">30 days</option>
              <option value="60">60 days</option>
              <option value="90">90 days</option>
            </Select>
          </Field>
        </div>
        {scoringMoved && (
          <p className="mt-4 text-sm text-accent-action flex items-start gap-2">
            <AlertTriangle size={16} className="shrink-0 mt-0.5" />
            This activation has already measured visitors. Saving this change
            records it on the log, and the report will say a scoring rule moved
            after the fact.
          </p>
        )}
      </Panel>

      <Panel title="Consent kiosk">
        <div className="grid md:grid-cols-3 gap-5">
          <div className="md:col-span-2">
            <Field
              label="Wording"
              hint="Shown on the phone or plinth a visitor scans. This exact text is what they agree to."
            >
              <TextArea
                rows={5}
                value={draft.consentCopy}
                onChange={(e) => set("consentCopy", e.target.value)}
              />
            </Field>
            <p className="mt-2 text-xs text-text-muted">
              Recorded as{" "}
              <span className="font-mono">
                {consentVersion || "— nothing to record"}
              </span>
              . The version follows the words, so every consent can be traced to
              what was on the screen. Kiosk links already handed out keep
              working; nothing is re-minted.
            </p>
          </div>
          <Field
            label="What it asks for"
            hint="Below T2, nothing reaches a CRM."
          >
            <Select
              value={draft.consentTier}
              onChange={(e) => set("consentTier", e.target.value as Draft["consentTier"])}
            >
              <option value="T1">T1 — keep details with this visit</option>
              <option value="T2">T2 — and may be contacted</option>
              <option value="T3">T3 — and may be enriched</option>
            </Select>
          </Field>
        </div>
      </Panel>

      <Panel title="Delivery and insights">
        <div className="grid md:grid-cols-2 gap-5">
          <Field
            label="Anonymous handoffs"
            hint="One lead per un-consented visitor at close. Off by default: a busy day is several hundred of them."
          >
            <button
              type="button"
              onClick={() => set("anonymousHandoffs", !draft.anonymousHandoffs)}
              className={`h-12 px-4 rounded-xl border text-sm font-medium text-left transition-colors ${
                draft.anonymousHandoffs
                  ? "bg-accent/8 border-accent/40 text-accent"
                  : "bg-bg-panel border-border-subtle text-text-secondary"
              }`}
            >
              {draft.anonymousHandoffs ? "On" : "Off"}
            </button>
          </Field>
          <Field
            label="Insight interval (minutes)"
            hint="How often the room is summarised, in event time."
          >
            <NumberInput
              min={1}
              max={240}
              value={draft.insightIntervalMinutes}
              onChange={(e) => set("insightIntervalMinutes", e.target.value)}
            />
          </Field>
        </div>
      </Panel>

      <div className="flex items-center gap-3">
        <Button icon={<Save size={14} />} disabled={saving} onClick={() => void commit()}>
          {saving ? "Saving…" : "Save changes"}
        </Button>
        {saved && <span className="text-sm text-accent">Saved.</span>}
        {error && <span className="text-sm text-accent-red">{error}</span>}
      </div>
    </div>
  );
}
