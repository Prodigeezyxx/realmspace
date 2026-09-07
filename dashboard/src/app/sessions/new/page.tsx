"use client";

import {
  Activity,
  Building2,
  CalendarClock,
  Camera as CameraIcon,
  Check,
  Layers,
  Lock,
  MapPin,
  Plus,
  Sparkles,
  Trash2,
  Users,
  Zap,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { PrefabPicker } from "@/components/sessions/PrefabPicker";
import { TouchpointEditor } from "@/components/sessions/TouchpointEditor";
import { TypeCard } from "@/components/sessions/TypeCard";
import {
  Field,
  NumberInput,
  Select,
  TextArea,
  TextInput,
  WizardFooter,
  WizardHeader,
  WizardStep,
} from "@/components/sessions/WizardChrome";
import { ZoneEditor } from "@/components/sessions/ZoneEditor";
import { Pill } from "@/components/ui/Pill";
import {
  PRIMARY_OBJECTIVE_OPTIONS,
  presetTouchpointsFor,
  presetZonesFor,
  SESSION_TYPES,
  getTypeMeta,
} from "@/lib/session/presets";
import { dispatchTrigger } from "@/lib/agent-engine";
import { setActivePrefabId } from "@/lib/prefab-store";
import { applyPrefabToDraft } from "@/lib/prefabs/apply";
import { getPrefab } from "@/lib/prefabs";
import { sessionActions } from "@/lib/session/store";
import type {
  Camera,
  PrimaryObjective,
  PrivacyMode,
  SessionDraft,
  SessionType,
  Touchpoint,
  Zone,
} from "@/lib/session/types";

const TOTAL_STEPS = 5;

const TIMEZONES = [
  "Africa/Lagos",
  "Europe/London",
  "America/New_York",
  "America/Los_Angeles",
  "Europe/Paris",
  "Asia/Dubai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];

function uid(prefix: string) {
  return `${prefix}_${Math.random().toString(36).slice(2, 9)}`;
}

function todayLocalDatetime(plusHours = 0) {
  const d = new Date(Date.now() + plusHours * 3600 * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function toIso(local: string): string {
  if (!local) return new Date().toISOString();
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? new Date().toISOString() : d.toISOString();
}

export default function NewSessionPage() {
  const router = useRouter();
  const [step, setStep] = useState(1);

  // ── Step 1: type
  const [type, setType] = useState<SessionType | null>(null);
  const [name, setName] = useState("");
  const [brand, setBrand] = useState("");
  const [client, setClient] = useState("");
  const [agency, setAgency] = useState("");

  // ── Step 2: where & when
  const [venue, setVenue] = useState("");
  const [address, setAddress] = useState("");
  const [city, setCity] = useState("");
  const [timezone, setTimezone] = useState("Africa/Lagos");
  const [startAtLocal, setStartAtLocal] = useState(todayLocalDatetime(0));
  const [endAtLocal, setEndAtLocal] = useState(todayLocalDatetime(72));
  const [expectedDailyFootfall, setExpectedDailyFootfall] = useState<number | "">(500);

  // ── Step 2b: cameras
  const [cameras, setCameras] = useState<Camera[]>([
    { id: uid("cam"), name: "Camera 1", placement: "Ceiling — central", device: "" },
  ]);

  // ── Step 3: space template + zones
  const [prefabId, setPrefabId] = useState<string | null>(null);
  const [boothSize, setBoothSize] = useState<
    { width: number; depth: number } | undefined
  >();
  const [zones, setZones] = useState<Zone[]>([]);

  // ── Step 4: touchpoints
  const [touchpoints, setTouchpoints] = useState<Touchpoint[]>([]);

  // ── Step 5: privacy + goals
  const [privacyMode, setPrivacyMode] = useState<PrivacyMode>("default");
  const [consentSignage, setConsentSignage] = useState(true);
  const [retentionDays, setRetentionDays] = useState<number | "">(30);
  const [recipientsCsv, setRecipientsCsv] = useState("");
  const [primaryObjective, setPrimaryObjective] = useState<PrimaryObjective>("brand_awareness");
  const [targetVisitors, setTargetVisitors] = useState<number | "">(1500);
  const [targetDwellSec, setTargetDwellSec] = useState<number | "">(240);
  const [targetCaptures, setTargetCaptures] = useState<number | "">(400);
  const [notes, setNotes] = useState("");

  // ── When the user picks a type, hydrate zones + touchpoints from presets.
  function selectType(t: SessionType) {
    setType(t);
    if (zones.length === 0 && touchpoints.length === 0) {
      const presetZones = presetZonesFor(t).map((z) => ({
        ...z,
        id: uid("zone"),
      })) as Zone[];
      const zoneByName = new Map(presetZones.map((z) => [z.name, z.id]));
      const presetTps = presetTouchpointsFor(t).map((tp) => ({
        ...tp,
        id: uid("tp"),
        zoneId: tp.zoneName ? zoneByName.get(tp.zoneName) : undefined,
      })) as Touchpoint[];
      setZones(presetZones);
      setTouchpoints(presetTps);
    }
  }

  const typeMeta = type ? getTypeMeta(type) : null;

  // Validation per step
  const canAdvance = useMemo(() => {
    if (step === 1) return Boolean(type && name.trim());
    if (step === 2) return Boolean(venue.trim() && startAtLocal);
    if (step === 3)
      return (
        Boolean(prefabId) &&
        zones.length > 0 &&
        zones.every((z) => z.name.trim())
      );
    if (step === 4) return touchpoints.every((t) => t.name.trim());
    return true;
  }, [step, type, name, venue, startAtLocal, prefabId, zones, touchpoints]);

  function selectPrefab(id: string) {
    const applied = applyPrefabToDraft(id, {
      newId: uid,
      prevZones: zones,
      touchpoints,
    });
    if (!applied) return;
    setPrefabId(applied.prefabId);
    setBoothSize(applied.boothSize);
    setZones(applied.zones);
    setTouchpoints(applied.touchpoints);
  }

  function back() {
    if (step > 1) setStep(step - 1);
  }
  function next() {
    if (!canAdvance) return;
    if (step < TOTAL_STEPS) setStep(step + 1);
  }

  function launch() {
    if (!type) return;
    const draft: SessionDraft = {
      name: name.trim(),
      type,
      brand: brand.trim() || undefined,
      client: client.trim() || undefined,
      agency: agency.trim() || undefined,
      venue: venue.trim(),
      address: address.trim() || undefined,
      city: city.trim() || undefined,
      timezone,
      startAt: toIso(startAtLocal),
      endAt: endAtLocal ? toIso(endAtLocal) : undefined,
      expectedDailyFootfall:
        typeof expectedDailyFootfall === "number"
          ? expectedDailyFootfall
          : undefined,
      cameras,
      prefabId: prefabId ?? undefined,
      boothSize,
      zones,
      touchpoints,
      goals: {
        primaryObjective,
        targetVisitors:
          typeof targetVisitors === "number" ? targetVisitors : undefined,
        targetDwellSec:
          typeof targetDwellSec === "number" ? targetDwellSec : undefined,
        targetCaptures:
          typeof targetCaptures === "number" ? targetCaptures : undefined,
        notes: notes.trim() || undefined,
      },
      privacy: {
        mode: privacyMode,
        consentSignage,
        retentionDays:
          typeof retentionDays === "number" ? retentionDays : 30,
        recipients: recipientsCsv
          ? recipientsCsv.split(",").map((s) => s.trim()).filter(Boolean)
          : undefined,
      },
      notes: notes.trim() || undefined,
    };
    const created = sessionActions.createSession(draft, { activate: true });
    if (created.prefabId) setActivePrefabId(created.prefabId);
    void dispatchTrigger({
      type: "twin_layout_loaded",
      timestamp: Date.now(),
      payload: {
        layout: {
          zones: created.zones
            .filter((z) => z.polygon?.length)
            .map((z) => ({
              id: z.id,
              label: z.name,
              polygon: z.polygon!,
            })),
        },
      },
    });
    router.push("/live");
  }

  return (
    <div className="min-h-screen canvas-vignette signal-grid">
      <WizardHeader total={TOTAL_STEPS} current={step} />

      {step === 1 && (
        <WizardStep
          step={1}
          total={TOTAL_STEPS}
          eyebrow="What are we mapping?"
          ghost="What are"
          title={
            <>
              What are{" "}
              <span className="text-text-faint">we mapping</span>{" "}
              today<span className="text-accent">?</span>
            </>
          }
          subtitle="Pick the type of experience this session will measure. We'll seed touchpoint ideas — you choose the space template when mapping the layout."
        >
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {SESSION_TYPES.map((meta) => (
              <TypeCard
                key={meta.type}
                meta={meta}
                selected={type === meta.type}
                onSelect={() => selectType(meta.type)}
              />
            ))}
          </div>

          {type && (
            <div className="mt-10 panel-elevated p-6 grid md:grid-cols-2 gap-6 max-w-4xl">
              <Field label="Session name">
                <TextInput
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={
                    typeMeta?.label
                      ? `${typeMeta.label} — Lagos 2026`
                      : "Session name"
                  }
                  autoFocus
                />
              </Field>
              <Field label="Brand / Client">
                <TextInput
                  value={brand}
                  onChange={(e) => setBrand(e.target.value)}
                  placeholder="e.g. Maison Vivienne"
                />
              </Field>
              <Field label="End client (optional)" hint="If different from the brand.">
                <TextInput
                  value={client}
                  onChange={(e) => setClient(e.target.value)}
                  placeholder="e.g. LVMH Group"
                />
              </Field>
              <Field label="Agency / Production (optional)">
                <TextInput
                  value={agency}
                  onChange={(e) => setAgency(e.target.value)}
                  placeholder="e.g. Floats XR"
                />
              </Field>
            </div>
          )}
        </WizardStep>
      )}

      {step === 2 && (
        <WizardStep
          step={2}
          total={TOTAL_STEPS}
          eyebrow="Where & when"
          ghost="Where and"
          title={
            <>
              Where and <span className="text-text-faint">when</span>
              <span className="text-accent">?</span>
            </>
          }
          subtitle="The physical setting and the time window. We use this to scope the report, set the timezone for live charts, and decide when the session goes from scheduled to live."
        >
          <div className="grid md:grid-cols-2 gap-5 max-w-5xl">
            <Field label="Venue" hint="Where it's happening — building or floor.">
              <TextInput
                value={venue}
                onChange={(e) => setVenue(e.target.value)}
                placeholder="e.g. Eko Hotel · Pavilion No. 7"
              />
            </Field>
            <Field label="City">
              <TextInput
                value={city}
                onChange={(e) => setCity(e.target.value)}
                placeholder="e.g. Lagos, Nigeria"
              />
            </Field>
            <Field label="Full address (optional)" className="md:col-span-2">
              <TextInput
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="Street, area, postal code"
              />
            </Field>
            <Field label="Timezone">
              <Select
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
              >
                {TIMEZONES.map((tz) => (
                  <option key={tz} value={tz}>
                    {tz}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Expected daily footfall" hint="Rough visitor estimate per day. Used for KPI targets.">
              <NumberInput
                min={1}
                value={expectedDailyFootfall}
                onChange={(e) =>
                  setExpectedDailyFootfall(
                    e.target.value ? parseInt(e.target.value, 10) : ""
                  )
                }
                placeholder="500"
              />
            </Field>
            <Field label="Starts at">
              <input
                type="datetime-local"
                value={startAtLocal}
                onChange={(e) => setStartAtLocal(e.target.value)}
                className="h-12 px-4 rounded-xl bg-bg-panel border border-border-subtle text-text-primary text-sm focus:border-accent focus:outline-none transition-colors"
              />
            </Field>
            <Field label="Ends at" hint="Leave blank for open-ended sessions.">
              <input
                type="datetime-local"
                value={endAtLocal}
                onChange={(e) => setEndAtLocal(e.target.value)}
                className="h-12 px-4 rounded-xl bg-bg-panel border border-border-subtle text-text-primary text-sm focus:border-accent focus:outline-none transition-colors"
              />
            </Field>
          </div>

          {/* Cameras */}
          <div className="mt-10 max-w-5xl">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold tracking-tight inline-flex items-center gap-2">
                <CameraIcon size={18} className="text-accent" />
                Cameras
              </h2>
              <button
                type="button"
                onClick={() =>
                  setCameras((cs) => [
                    ...cs,
                    {
                      id: uid("cam"),
                      name: `Camera ${cs.length + 1}`,
                      placement: "",
                      device: "",
                    },
                  ])
                }
                className="text-sm text-text-secondary hover:text-accent inline-flex items-center gap-1.5 transition-colors"
              >
                <Plus size={14} />
                Add camera
              </button>
            </div>
            <ul className="space-y-2">
              {cameras.map((c) => (
                <li
                  key={c.id}
                  className="panel grid grid-cols-1 md:grid-cols-[1.5fr_2fr_1.5fr_44px] gap-3 items-center p-4"
                >
                  <input
                    value={c.name}
                    onChange={(e) =>
                      setCameras((cs) =>
                        cs.map((x) =>
                          x.id === c.id ? { ...x, name: e.target.value } : x
                        )
                      )
                    }
                    className="h-10 px-3 rounded-lg bg-bg-canvas border border-border-subtle text-sm focus:border-accent focus:outline-none transition-colors"
                    placeholder="Camera name"
                  />
                  <input
                    value={c.placement ?? ""}
                    onChange={(e) =>
                      setCameras((cs) =>
                        cs.map((x) =>
                          x.id === c.id
                            ? { ...x, placement: e.target.value || undefined }
                            : x
                        )
                      )
                    }
                    className="h-10 px-3 rounded-lg bg-bg-canvas border border-border-subtle text-sm focus:border-accent focus:outline-none transition-colors"
                    placeholder="Placement (ceiling, side, etc.)"
                  />
                  <input
                    value={c.device ?? ""}
                    onChange={(e) =>
                      setCameras((cs) =>
                        cs.map((x) =>
                          x.id === c.id
                            ? { ...x, device: e.target.value || undefined }
                            : x
                        )
                      )
                    }
                    className="h-10 px-3 rounded-lg bg-bg-canvas border border-border-subtle text-sm focus:border-accent focus:outline-none transition-colors"
                    placeholder="Device (Logitech C920, etc.)"
                  />
                  <button
                    type="button"
                    onClick={() =>
                      setCameras((cs) => cs.filter((x) => x.id !== c.id))
                    }
                    disabled={cameras.length <= 1}
                    className="h-10 w-10 rounded-lg text-text-muted hover:text-accent-red hover:bg-accent-red/10 transition-colors inline-flex items-center justify-center disabled:opacity-30 disabled:cursor-not-allowed"
                    title="Remove camera"
                  >
                    <Trash2 size={15} />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </WizardStep>
      )}

      {step === 3 && (
        <WizardStep
          step={3}
          total={TOTAL_STEPS}
          eyebrow="Layout — space & zones"
          ghost="The layout"
          title={
            <>
              Map the <span className="text-text-faint">space</span>
              <span className="text-accent">.</span>
            </>
          }
          subtitle="Choose a venue template, then refine zones. This footprint drives the digital twin and how sensor tracks map into the booth."
        >
          <div className="max-w-6xl">
            <PrefabPicker value={prefabId} onChange={selectPrefab} />
            <ZoneEditor zones={zones} onChange={setZones} />
          </div>
        </WizardStep>
      )}

      {step === 4 && (
        <WizardStep
          step={4}
          total={TOTAL_STEPS}
          eyebrow="Touchpoints"
          ghost="Interactive"
          title={
            <>
              Interactive{" "}
              <span className="text-text-faint">touchpoints</span>
              <span className="text-accent">.</span>
            </>
          }
          subtitle="The digital surfaces inside your space — screens, scanners, AR mirrors, games. Each one becomes a measurable trigger in the graph."
        >
          <div className="max-w-6xl">
            <TouchpointEditor
              touchpoints={touchpoints}
              zones={zones}
              onChange={setTouchpoints}
            />
          </div>
        </WizardStep>
      )}

      {step === 5 && (
        <WizardStep
          step={5}
          total={TOTAL_STEPS}
          eyebrow="Privacy, goals & launch"
          ghost="Ready to"
          title={
            <>
              Ready to <span className="text-text-faint">launch</span>
              <span className="text-accent">.</span>
            </>
          }
          subtitle="One last pass on privacy posture, what success looks like, and a review of everything you've configured."
        >
          {/* Privacy */}
          <div className="max-w-5xl space-y-10">
            <section>
              <h2 className="text-xl font-semibold tracking-tight inline-flex items-center gap-2 mb-4">
                <Lock size={18} className="text-accent" />
                Privacy posture
              </h2>
              <div className="grid md:grid-cols-3 gap-3 mb-4">
                {(["default", "strict", "open"] as PrivacyMode[]).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setPrivacyMode(mode)}
                    className={`panel p-5 text-left transition-all ${
                      privacyMode === mode
                        ? "ring-1 ring-accent/60 !border-accent/40 shadow-[var(--glow-green)]"
                        : "hover:border-border-strong"
                    }`}
                  >
                    <div className="text-sm font-semibold tracking-tight capitalize">
                      {mode}
                    </div>
                    <div className="text-xs text-text-secondary mt-1.5 leading-relaxed">
                      {mode === "default" &&
                        "Local-first, anonymous IDs, auto-purge raw frames. The right choice for most events."}
                      {mode === "strict" &&
                        "All sensitive zones masked at pixel level, no track lifespans persisted, accelerated retention."}
                      {mode === "open" &&
                        "Full data retention for research / R&D contexts where consent is explicit."}
                    </div>
                  </button>
                ))}
              </div>

              <div className="grid md:grid-cols-3 gap-5">
                <Field label="Consent signage at entry" hint="Visible signs telling visitors they're being measured.">
                  <button
                    type="button"
                    onClick={() => setConsentSignage((v) => !v)}
                    className={`h-12 px-4 rounded-xl border text-sm font-medium inline-flex items-center justify-between transition-colors ${
                      consentSignage
                        ? "bg-accent/8 border-accent/40 text-accent"
                        : "bg-bg-panel border-border-subtle text-text-secondary"
                    }`}
                  >
                    {consentSignage ? "Yes — posted at every entry" : "Not yet"}
                    <span
                      className={`w-9 h-5 rounded-full relative transition-colors ${
                        consentSignage ? "bg-accent" : "bg-bg-elevated border border-border-subtle"
                      }`}
                    >
                      <span
                        className={`absolute top-0.5 h-4 w-4 rounded-full transition-all ${
                          consentSignage
                            ? "left-[calc(100%-18px)] bg-text-inverse"
                            : "left-0.5 bg-text-muted"
                        }`}
                      />
                    </span>
                  </button>
                </Field>
                <Field label="Retention" hint="Days the structured graph is retained.">
                  <NumberInput
                    min={1}
                    max={365}
                    value={retentionDays}
                    onChange={(e) =>
                      setRetentionDays(
                        e.target.value ? parseInt(e.target.value, 10) : ""
                      )
                    }
                  />
                </Field>
                <Field
                  label="Report recipients"
                  hint="Comma-separated emails. Optional."
                >
                  <TextInput
                    value={recipientsCsv}
                    onChange={(e) => setRecipientsCsv(e.target.value)}
                    placeholder="amaka@brand.com, ed@agency.com"
                  />
                </Field>
              </div>
            </section>

            {/* Goals */}
            <section>
              <h2 className="text-xl font-semibold tracking-tight inline-flex items-center gap-2 mb-4">
                <Activity size={18} className="text-accent" />
                Goals & success
              </h2>
              <div className="grid md:grid-cols-2 gap-5">
                <Field label="Primary objective" className="md:col-span-2">
                  <Select
                    value={primaryObjective}
                    onChange={(e) =>
                      setPrimaryObjective(e.target.value as PrimaryObjective)
                    }
                  >
                    {PRIMARY_OBJECTIVE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Target visitors">
                  <NumberInput
                    min={0}
                    value={targetVisitors}
                    onChange={(e) =>
                      setTargetVisitors(
                        e.target.value ? parseInt(e.target.value, 10) : ""
                      )
                    }
                  />
                </Field>
                <Field label="Target avg dwell (seconds)">
                  <NumberInput
                    min={0}
                    value={targetDwellSec}
                    onChange={(e) =>
                      setTargetDwellSec(
                        e.target.value ? parseInt(e.target.value, 10) : ""
                      )
                    }
                  />
                </Field>
                <Field label="Target captures (RFID / lead / photo)" className="md:col-span-2">
                  <NumberInput
                    min={0}
                    value={targetCaptures}
                    onChange={(e) =>
                      setTargetCaptures(
                        e.target.value ? parseInt(e.target.value, 10) : ""
                      )
                    }
                  />
                </Field>
                <Field
                  label="Brief notes (optional)"
                  hint="Anything we should remember for the report?"
                  className="md:col-span-2"
                >
                  <TextArea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder="e.g. The client is also using this as a press moment. The Lounge is sponsored by..."
                  />
                </Field>
              </div>
            </section>

            {/* Review */}
            <section>
              <h2 className="text-xl font-semibold tracking-tight inline-flex items-center gap-2 mb-4">
                <Sparkles size={18} className="text-accent" />
                Review
              </h2>
              <div className="grid md:grid-cols-2 gap-3">
                <ReviewLine
                  icon={<Sparkles size={14} />}
                  label="Session"
                  value={
                    <>
                      <span className="font-semibold">{name || "—"}</span>
                      {typeMeta && (
                        <Pill variant="success" className="ml-2 align-middle">
                          {typeMeta.label}
                        </Pill>
                      )}
                    </>
                  }
                />
                <ReviewLine
                  icon={<Building2 size={14} />}
                  label="Brand / Client"
                  value={brand || client || "—"}
                />
                <ReviewLine
                  icon={<MapPin size={14} />}
                  label="Venue"
                  value={[venue, city].filter(Boolean).join(" · ") || "—"}
                />
                <ReviewLine
                  icon={<CalendarClock size={14} />}
                  label="When"
                  value={`${formatLocal(startAtLocal)}${
                    endAtLocal ? ` → ${formatLocal(endAtLocal)}` : " (open-ended)"
                  }`}
                />
                <ReviewLine
                  icon={<CameraIcon size={14} />}
                  label="Cameras"
                  value={`${cameras.length} configured`}
                />
                <ReviewLine
                  icon={<Users size={14} />}
                  label="Expected footfall"
                  value={`${expectedDailyFootfall || 0} per day`}
                />
                <ReviewLine
                  icon={<Zap size={14} />}
                  label="Space template"
                  value={
                    prefabId
                      ? `${getPrefab(prefabId)?.name ?? prefabId}${
                          boothSize
                            ? ` · ${boothSize.width}m × ${boothSize.depth}m`
                            : ""
                        }`
                      : "—"
                  }
                />
                <ReviewLine
                  icon={<Layers size={14} />}
                  label="Zones · Touchpoints"
                  value={`${zones.length} zones · ${touchpoints.length} touchpoints`}
                />
                <ReviewLine
                  icon={<Lock size={14} />}
                  label="Privacy"
                  value={`${privacyMode}, retention ${
                    typeof retentionDays === "number" ? retentionDays : 30
                  } days`}
                />
              </div>
            </section>
          </div>
        </WizardStep>
      )}

      <WizardFooter
        back={step > 1 ? back : undefined}
        next={step < TOTAL_STEPS ? next : undefined}
        finish={step === TOTAL_STEPS ? launch : undefined}
        nextDisabled={!canAdvance}
      />
    </div>
  );
}

function ReviewLine({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="panel p-4 flex items-start gap-3">
      <span className="w-8 h-8 rounded-full bg-bg-elevated border border-border-subtle flex items-center justify-center text-accent shrink-0">
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[10px] uppercase tracking-[0.16em] text-text-muted font-medium">
          {label}
        </div>
        <div className="text-sm mt-0.5 truncate">{value}</div>
      </div>
      <Check size={14} className="text-accent shrink-0 mt-0.5" />
    </div>
  );
}

function formatLocal(local: string): string {
  if (!local) return "—";
  const d = new Date(local);
  if (Number.isNaN(d.getTime())) return local;
  return d.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
