import {
  ArrowRight,
  Brain,
  Cctv,
  ChevronRight,
  Eye,
  Gauge,
  Lock,
  Package,
  Sparkles,
  Wand2,
  Workflow,
  Zap,
} from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";

export default function LandingPage() {
  return (
    <div className="min-h-screen canvas-vignette text-text-primary">
      {/* ── Top nav */}
      <nav className="sticky top-0 z-40 backdrop-blur-xl bg-bg-base/70 border-b border-border-hairline">
        <div className="max-w-7xl mx-auto h-14 px-6 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-accent-cyan to-accent-blue flex items-center justify-center shadow-[var(--glow-cyan)]">
              <div className="w-2.5 h-2.5 rounded-full bg-bg-base" />
            </div>
            <span className="text-sm font-semibold tracking-tight">RealmSpace</span>
            <Pill variant="info" className="ml-2">Preview</Pill>
          </div>
          <div className="hidden md:flex items-center gap-7 text-sm text-text-secondary">
            <a href="#loop" className="hover:text-text-primary transition-colors">The loop</a>
            <a href="#stack" className="hover:text-text-primary transition-colors">Stack</a>
            <a href="#privacy" className="hover:text-text-primary transition-colors">Privacy</a>
            <a href="#pricing" className="hover:text-text-primary transition-colors">Pricing</a>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/live">
              <Button variant="secondary" size="sm">Open demo</Button>
            </Link>
            <Link href="/live">
              <Button variant="primary" size="sm" iconAfter={<ArrowRight size={14} />}>
                Book a pilot
              </Button>
            </Link>
          </div>
        </div>
      </nav>

      {/* ── Hero */}
      <section className="relative max-w-7xl mx-auto px-6 pt-24 pb-32">
        <div className="grid lg:grid-cols-12 gap-10 items-center">
          <div className="lg:col-span-7">
            <Pill variant="live" className="mb-6">
              <span className="live-dot" />
              Live in Lagos · Pavilion No. 7
            </Pill>
            <h1 className="text-5xl md:text-7xl font-semibold tracking-tight leading-[0.95]">
              Watch the room
              <br />
              <span className="text-text-muted">think.</span>
            </h1>
            <p className="mt-6 text-lg text-text-secondary max-w-xl leading-relaxed">
              RealmSpace is the measurement, replay and intelligence layer for
              physical brand experiences. Plug in any camera. Get a queryable
              graph of attention, dwell and behavior — plus a 3D digital twin
              you can scrub through and ask questions of.
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-3">
              <Link href="/live">
                <Button variant="primary" size="lg" iconAfter={<ArrowRight size={16} />}>
                  Open the demo
                </Button>
              </Link>
              <Link href="/report">
                <Button variant="secondary" size="lg">See a client report</Button>
              </Link>
              <span className="text-xs text-text-muted ml-2">
                No signup · No backend · Runs on this laptop
              </span>
            </div>

            <div className="mt-12 grid grid-cols-3 gap-6 max-w-xl">
              <Metric value="1,287" label="Visitors today" delta="+18%" />
              <Metric value="6m 50s" label="Avg lounge dwell" delta="+12%" />
              <Metric value="62%" label="Mirror → RFID" delta="+24%" />
            </div>
          </div>

          <div className="lg:col-span-5">
            <HeroPreview />
          </div>
        </div>
      </section>

      {/* ── The Loop */}
      <section id="loop" className="border-y border-border-hairline bg-bg-canvas/60">
        <div className="max-w-7xl mx-auto px-6 py-24">
          <div className="max-w-3xl">
            <Pill variant="violet" className="mb-4">The loop</Pill>
            <h2 className="text-4xl md:text-5xl font-semibold tracking-tight">
              Designed, built and measured
              <br />
              by the same hands.
            </h2>
            <p className="mt-5 text-text-secondary text-lg leading-relaxed">
              Most measurement vendors show up after the booth is built. We
              don&apos;t. We design the activation, ship the interactive
              experiences inside it, and instrument the whole thing — so the
              data we deliver is structurally cleaner than anything a
              bolt-on tool can produce.
            </p>
          </div>

          <div className="mt-14 grid md:grid-cols-4 gap-4">
            <LoopStep
              i={1}
              icon={<Package size={20} />}
              title="Booth"
              text="We design and fabricate the physical activation — modular, sponsor-ready, camera-aware."
            />
            <LoopStep
              i={2}
              icon={<Wand2 size={20} />}
              title="Experience"
              text="AR mirrors, scent quizzes, RFID memory walls. We build the digital layer that gives visitors a reason to dwell."
            />
            <LoopStep
              i={3}
              icon={<Cctv size={20} />}
              title="Measure"
              text="One laptop, one camera. Anonymous tracking, zone analytics, gaze, dwell, group dynamics."
            />
            <LoopStep
              i={4}
              icon={<Sparkles size={20} />}
              title="Twin"
              text="A 3D replay of the activation the client can scrub through — and ask questions of, in plain English."
            />
          </div>
        </div>
      </section>

      {/* ── Three product pillars */}
      <section id="stack" className="max-w-7xl mx-auto px-6 py-28">
        <div className="grid lg:grid-cols-3 gap-6">
          <Pillar
            icon={<Gauge size={22} />}
            tag="Capture"
            title="The room as live telemetry."
            body="OpenCV + YOLOv8 + ByteTrack stream people, gaze, dwell and proximity into a Postgres + Neo4j store at 22fps — locally, on a MacBook. Faces never persist. Visitors are anonymous, session-scoped IDs."
            cta={{ href: "/live", label: "Live dashboard" }}
          />
          <Pillar
            icon={<Eye size={22} />}
            tag="Twin"
            title="A 3D replay of every minute."
            body="Anonymous avatars walk through a scale model of the activation. Scrub the timeline, fly the camera, isolate a zone. The client sees what happened, not just numbers."
            cta={{ href: "/twin", label: "Open the twin" }}
            accent
          />
          <Pillar
            icon={<Brain size={22} />}
            tag="Ask"
            title="Plain-English questions, real answers."
            body="Claude or GPT-4o translates the client&apos;s question into a Cypher query against the spatial graph. Answers come back in seconds, with charts and a highlighted subgraph. No analyst required."
            cta={{ href: "/ask", label: "Ask the room" }}
          />
        </div>
      </section>

      {/* ── Privacy moat */}
      <section
        id="privacy"
        className="border-y border-border-hairline bg-bg-canvas/60"
      >
        <div className="max-w-7xl mx-auto px-6 py-24 grid lg:grid-cols-2 gap-12 items-center">
          <div>
            <Pill variant="success" className="mb-4">
              <Lock size={11} />
              Privacy first
            </Pill>
            <h2 className="text-4xl md:text-5xl font-semibold tracking-tight">
              No faces. No re-identification.
              <br />
              <span className="text-text-muted">Local by default.</span>
            </h2>
            <p className="mt-5 text-text-secondary text-lg leading-relaxed">
              Frames are processed on-device. Raw video auto-purges in
              minutes; only the structured graph survives. Person IDs reset
              per session — we never link a visitor to themselves across days,
              cameras or activations.
            </p>
            <p className="mt-3 text-text-muted text-sm">
              GDPR / CCPA / UK DPA-aware by construction. Per-pixel masking
              available for sensitive surfaces.
            </p>
          </div>

          <div className="panel-elevated p-7 grid grid-cols-2 gap-5 text-sm">
            <PrivacyRow ok>Local-first perception</PrivacyRow>
            <PrivacyRow ok>Anonymous session IDs</PrivacyRow>
            <PrivacyRow ok>Auto-delete raw video</PrivacyRow>
            <PrivacyRow ok>No biometric storage</PrivacyRow>
            <PrivacyRow ok>Per-zone opt-out masking</PrivacyRow>
            <PrivacyRow ok>Avatar replay (not video)</PrivacyRow>
            <PrivacyRow ko>No facial recognition</PrivacyRow>
            <PrivacyRow ko>No cross-session linking</PrivacyRow>
          </div>
        </div>
      </section>

      {/* ── Pricing */}
      <section id="pricing" className="max-w-7xl mx-auto px-6 py-28">
        <div className="text-center max-w-2xl mx-auto">
          <Pill variant="info" className="mb-4">Pricing</Pill>
          <h2 className="text-4xl md:text-5xl font-semibold tracking-tight">
            Per activation. Built for agency calendars.
          </h2>
          <p className="mt-4 text-text-secondary">
            We deliver a kit, a live dashboard, a client-ready report and a
            scrubbable 3D twin. You bill it through as a measurement line item
            — or as part of the booth itself.
          </p>
        </div>

        <div className="mt-14 grid md:grid-cols-3 gap-5">
          <PriceTier
            name="Booth"
            price="$5,800"
            unit="per 3-day activation"
            features={[
              "Single camera kit (laptop + USB cam)",
              "Live counter, zones, heatmap",
              "Branded PDF report (24h delivery)",
              "30-day data retention",
            ]}
          />
          <PriceTier
            name="Pavilion"
            price="$14,500"
            unit="per activation"
            featured
            features={[
              "Up to 4 cameras + sensor fusion",
              "3D digital twin replay",
              "Ask the Room — unlimited queries",
              "2 custom agent rules",
              "Sponsor exposure measurement",
              "90-day retention + raw export",
            ]}
          />
          <PriceTier
            name="Campaign"
            price="Custom"
            unit="multi-city · season-long"
            features={[
              "Multi-venue, multi-city deployments",
              "Cross-activation benchmarking",
              "White-labeled client portal",
              "Dedicated success + onsite support",
              "On-prem or VPC delivery",
            ]}
          />
        </div>
      </section>

      {/* ── Footer */}
      <footer className="border-t border-border-hairline">
        <div className="max-w-7xl mx-auto px-6 py-12 flex flex-col md:flex-row gap-6 items-start md:items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-6 h-6 rounded-full bg-gradient-to-br from-accent-cyan to-accent-blue" />
            <span className="text-sm font-semibold">RealmSpace</span>
            <span className="text-text-muted text-xs ml-2">
              Experiential Intelligence · by Yourself Creative
            </span>
          </div>
          <div className="text-xs text-text-muted">
            © 2026 · Built for agencies, brand teams, and the venues that hold
            them.
          </div>
        </div>
      </footer>
    </div>
  );
}

function Metric({ value, label, delta }: { value: string; label: string; delta: string }) {
  return (
    <div>
      <div className="text-3xl font-semibold tabular tracking-tight">{value}</div>
      <div className="mt-1 text-xs text-text-muted">{label}</div>
      <div className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-accent-green tabular">
        ▲ {delta}
      </div>
    </div>
  );
}

function LoopStep({
  i,
  icon,
  title,
  text,
}: {
  i: number;
  icon: React.ReactNode;
  title: string;
  text: string;
}) {
  return (
    <div className="panel p-6 relative group hover:border-border-subtle transition-colors">
      <div className="absolute top-5 right-5 text-[10px] tabular text-text-faint">
        0{i}
      </div>
      <div className="w-9 h-9 rounded-lg bg-bg-elevated border border-border-subtle flex items-center justify-center text-accent-blue">
        {icon}
      </div>
      <h3 className="mt-4 text-lg font-semibold tracking-tight">{title}</h3>
      <p className="mt-2 text-sm text-text-secondary leading-relaxed">{text}</p>
    </div>
  );
}

function Pillar({
  icon,
  tag,
  title,
  body,
  cta,
  accent,
}: {
  icon: React.ReactNode;
  tag: string;
  title: string;
  body: string;
  cta: { href: string; label: string };
  accent?: boolean;
}) {
  return (
    <div
      className={`panel p-7 flex flex-col gap-4 group hover:border-border-subtle transition-all ${
        accent ? "ring-1 ring-accent-blue/30 shadow-[var(--glow-blue)]" : ""
      }`}
    >
      <div className="flex items-center gap-2.5">
        <div className="w-9 h-9 rounded-lg bg-bg-elevated border border-border-subtle flex items-center justify-center text-accent-blue">
          {icon}
        </div>
        <span className="text-[10px] uppercase tracking-[0.18em] text-text-secondary">
          {tag}
        </span>
      </div>
      <h3 className="text-2xl font-semibold tracking-tight leading-tight">
        {title}
      </h3>
      <p className="text-sm text-text-secondary leading-relaxed flex-1">{body}</p>
      <Link
        href={cta.href}
        className="text-sm text-accent-blue inline-flex items-center gap-1.5 group-hover:text-accent-blue-bright transition-colors"
      >
        {cta.label}
        <ChevronRight
          size={14}
          className="transition-transform group-hover:translate-x-0.5"
        />
      </Link>
    </div>
  );
}

function PrivacyRow({ children, ok, ko }: { children: React.ReactNode; ok?: boolean; ko?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <span
        className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold ${
          ok
            ? "bg-accent-green/15 text-accent-green border border-accent-green/30"
            : "bg-accent-red/15 text-accent-red border border-accent-red/30"
        }`}
      >
        {ok ? "✓" : "✕"}
      </span>
      <span className={ko ? "text-text-muted" : "text-text-primary"}>
        {children}
      </span>
    </div>
  );
}

function PriceTier({
  name,
  price,
  unit,
  features,
  featured,
}: {
  name: string;
  price: string;
  unit: string;
  features: string[];
  featured?: boolean;
}) {
  return (
    <div
      className={`panel p-7 flex flex-col gap-5 ${
        featured
          ? "ring-1 ring-accent-blue/40 shadow-[var(--glow-blue)] relative"
          : ""
      }`}
    >
      {featured && (
        <Pill variant="info" className="absolute -top-3 left-7">
          Most popular
        </Pill>
      )}
      <div>
        <div className="text-xs uppercase tracking-[0.18em] text-text-secondary">
          {name}
        </div>
        <div className="mt-3 flex items-baseline gap-1.5">
          <span className="text-4xl font-semibold tabular tracking-tight">
            {price}
          </span>
        </div>
        <div className="text-xs text-text-muted mt-1">{unit}</div>
      </div>
      <ul className="text-sm text-text-secondary space-y-2.5 flex-1">
        {features.map((f) => (
          <li key={f} className="flex gap-2.5">
            <span className="text-accent-green mt-0.5 shrink-0">
              <Zap size={13} strokeWidth={2.5} />
            </span>
            {f}
          </li>
        ))}
      </ul>
      <Button variant={featured ? "primary" : "secondary"} fullWidth>
        Book a pilot
      </Button>
    </div>
  );
}

/** A static-but-alive looking miniature of the dashboard for the hero. */
function HeroPreview() {
  return (
    <div className="relative">
      <div className="absolute -inset-6 bg-gradient-to-br from-accent-blue/20 via-transparent to-accent-cyan/10 blur-3xl -z-10" />
      <div className="panel-elevated overflow-hidden">
        <div className="flex items-center justify-between px-4 h-9 border-b border-border-hairline bg-bg-base/50">
          <div className="flex items-center gap-2">
            <span className="live-dot" />
            <span className="text-[11px] tabular text-text-secondary">LIVE · 21:14:08</span>
          </div>
          <div className="flex gap-1">
            <span className="w-2 h-2 rounded-full bg-border-subtle" />
            <span className="w-2 h-2 rounded-full bg-border-subtle" />
            <span className="w-2 h-2 rounded-full bg-border-subtle" />
          </div>
        </div>
        <div className="grid grid-cols-3 gap-px bg-border-hairline">
          <PreviewTile label="People now" value="14" />
          <PreviewTile label="Avg dwell" value="4m 23s" />
          <PreviewTile label="Triggers" value="2,186" accent />
        </div>
        <div className="relative h-56 bg-bg-canvas map-grid overflow-hidden">
          <div className="absolute inset-0 flex items-center justify-center text-text-muted text-[10px] uppercase tracking-[0.2em]">
            digital twin · pavilion no. 7
          </div>
          {/* mock avatar dots */}
          {[
            ["20%", "30%", "#3e83f7"],
            ["45%", "35%", "#bf5af2"],
            ["62%", "20%", "#00d4ff"],
            ["55%", "65%", "#30d158"],
            ["75%", "70%", "#ffd60a"],
            ["32%", "55%", "#ff7eb6"],
          ].map(([x, y, c], i) => (
            <div
              key={i}
              className="absolute w-2.5 h-2.5 rounded-full"
              style={{
                left: x,
                top: y,
                background: c as string,
                boxShadow: `0 0 12px ${c}`,
              }}
            />
          ))}
          {/* zone outlines */}
          <div className="absolute left-[18%] top-[18%] w-[26%] h-[40%] border border-accent-violet/40 rounded-md" />
          <div className="absolute left-[58%] top-[12%] w-[28%] h-[30%] border border-accent-cyan/40 rounded-md" />
          <div className="absolute left-[28%] top-[60%] w-[36%] h-[30%] border border-accent-green/40 rounded-md" />
        </div>
        <div className="p-3 border-t border-border-hairline text-[11px] font-mono text-text-secondary flex items-center gap-3 truncate">
          <span className="text-accent-cyan">›</span>
          P-216 looked at Bottle Wall · 7s
        </div>
      </div>
    </div>
  );
}

function PreviewTile({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  return (
    <div className="bg-bg-panel p-4">
      <div className="text-[10px] uppercase tracking-[0.16em] text-text-muted">
        {label}
      </div>
      <div
        className={`text-2xl font-semibold tabular tracking-tight mt-1 ${
          accent ? "text-accent-cyan" : "text-text-primary"
        }`}
      >
        {value}
      </div>
    </div>
  );
}
