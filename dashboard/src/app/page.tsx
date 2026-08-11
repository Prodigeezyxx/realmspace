import {
  ArrowDownRight,
  ArrowUpRight,
  Brain,
  Cctv,
  ChevronRight,
  Eye,
  Gauge,
  Search,
  Zap,
} from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";

export default function LandingPage() {
  return (
    <div className="min-h-screen canvas-vignette text-text-primary">
      {/* ── Top nav — pill-grouped, Intellias style */}
      <nav className="sticky top-0 z-40 px-5 py-4">
        <div className="max-w-[1400px] mx-auto flex items-center justify-between gap-4">
          {/* Logo pill */}
          <Link
            href="/"
            className="bg-bg-raised border border-border-subtle h-12 rounded-full px-5 inline-flex items-center gap-2.5 hover:border-border-strong transition-colors shadow-[var(--shadow-sm)]"
          >
            <div className="relative w-5 h-5">
              <div className="absolute inset-0 rounded-full bg-accent shadow-[var(--glow-green)]" />
              <div className="absolute inset-[3px] rounded-full bg-bg-base" />
              <div className="absolute inset-[5px] rounded-full bg-accent" />
            </div>
            <span className="text-base font-semibold tracking-tight">
              RealmSpace
            </span>
          </Link>

          {/* Nav pill */}
          <div className="hidden md:flex bg-bg-raised border border-border-subtle h-12 rounded-full pl-2 pr-2 items-center shadow-[var(--shadow-sm)]">
            {[
              { href: "#capabilities", label: "Capabilities" },
              { href: "#loop", label: "The loop" },
              { href: "#privacy", label: "Privacy" },
              { href: "#pricing", label: "Pricing" },
              { href: "/live", label: "Demo" },
            ].map((item) => (
              <Link
                key={item.label}
                href={item.href}
                className="px-4 h-9 inline-flex items-center text-[13px] text-text-secondary hover:text-text-primary transition-colors rounded-full hover:bg-bg-elevated font-medium tracking-tight"
              >
                {item.label}
              </Link>
            ))}
            <button
              className="w-9 h-9 rounded-full inline-flex items-center justify-center text-text-secondary hover:text-text-primary hover:bg-bg-elevated transition-colors"
              aria-label="Search"
            >
              <Search size={15} strokeWidth={2.2} />
            </button>
          </div>

          {/* CTA pill */}
          <div className="flex items-center gap-2">
            <Link
              href="/login"
              className="hidden sm:inline-flex items-center h-12 px-5 rounded-full border border-border-subtle text-[13px] font-medium text-text-secondary hover:text-text-primary hover:border-border-strong transition-colors"
            >
              Sign in
            </Link>
            <Link
              href="/live"
              className="inline-flex items-center gap-2 bg-accent text-text-inverse h-12 pl-5 pr-2.5 rounded-full font-semibold text-[13px] hover:bg-accent-bright transition-colors shadow-[var(--glow-green)]"
            >
              Try RealmSpace
              <span className="w-9 h-9 rounded-full bg-text-inverse text-accent inline-flex items-center justify-center">
                <ArrowUpRight size={16} />
              </span>
            </Link>
          </div>
        </div>
      </nav>

      {/* ── Hero */}
      <section className="relative max-w-[1400px] mx-auto px-5 pt-16 md:pt-24 pb-24">
        <div className="grid lg:grid-cols-12 gap-12 lg:gap-16 items-start">
          {/* LHS — display headline */}
          <div className="lg:col-span-7">
            <h1 className="display-large tracking-[-0.04em]">
              <span className="block">
                <span className="ghost-text" data-text="Watch the">
                  Watch the
                </span>
              </span>
              <span className="block text-text-faint">room</span>
              <span className="block">
                think
                <span className="text-accent">.</span>
              </span>
            </h1>

            <p className="mt-10 text-lg md:text-xl text-on-surface-variant max-w-xl leading-relaxed body-large">
              Know exactly what happens in your activation — who stopped, how long they stayed, and what they touched. No guesswork, no hand-counted door tallies. Just a live graph you can query in plain English.
            </p>

            <div className="mt-10 flex flex-wrap items-center gap-3">
              <Link href="/live">
                <Button
                  variant="primary"
                  size="lg"
                  iconAfter={
                    <span className="w-9 h-9 -mr-3 rounded-full bg-text-inverse text-accent inline-flex items-center justify-center">
                      <ArrowUpRight size={16} />
                    </span>
                  }
                >
                  See your booth live
                </Button>
              </Link>
              <Link href="/report">
                <Button variant="secondary" size="lg">
                  View a sample report
                </Button>
              </Link>
            </div>

            <p className="text-xs text-text-muted mt-6 tabular">
              Real-time detection in your browser. See it live.
            </p>
          </div>

          {/* RHS — hero preview card */}
          <div className="lg:col-span-5">
            <HeroPreview />
          </div>
        </div>

        {/* Footer metrics strip */}
        <div className="mt-20 md:mt-24 pt-10 border-t border-border-hairline grid grid-cols-2 md:grid-cols-4 gap-8">
          <Metric value="1,287" label="Visitors today" delta="+18%" />
          <Metric value="6m 50s" label="Avg lounge dwell" delta="+12%" />
          <Metric value="62%" label="Mirror → RFID" delta="+24%" />
          <Metric value="4.2×" label="ROI per dollar" delta="+68%" accent />
        </div>
      </section>

      {/* ── Capabilities — three pillars */}
      <section
        id="capabilities"
        className="border-y border-outline-variant surface-container-low"
      >
        <div className="max-w-[1400px] mx-auto px-5 py-24">
          <div className="grid md:grid-cols-2 gap-12 items-end mb-14">
            <div>
              <h2 className="display-medium">
                Three surfaces.
                <span className="text-on-surface-variant">
                  {" "}One platform.
                </span>
              </h2>
            </div>
            <p className="body-large text-on-surface-variant max-w-xl">
              During the activation, the system captures zone entries, dwell
              times, and movement as structured events. Those events feed a live
              operator view, a 3D replay, and a client report — all from the
              same graph.
            </p>
          </div>

          <div className="grid lg:grid-cols-3 gap-5">
            <Pillar
              icon={<Gauge size={22} />}
              tag="Capture"
              title="The room as live telemetry."
              body="Real-time on-device detection in your browser today; OpenCV + YOLOv8 + ByteTrack in production. Faces never persist. Visitors are anonymous, session-scoped IDs."
              cta={{ href: "/live", label: "Live dashboard" }}
            />
            <Pillar
              icon={<Eye size={22} />}
              tag="Twin"
              title="A 3D replay of every minute."
              body="Anonymous avatars walking a scale model of the activation. Scrub the timeline. Fly the camera. Isolate a zone. The client sees what happened, not just numbers."
              cta={{ href: "/twin", label: "Open the twin" }}
              accent
            />
            <Pillar
              icon={<Brain size={22} />}
              tag="Ask"
              title="Plain-English. Real answers."
              body="Claude or GPT-4o translates the client's question into a Cypher query against the spatial graph. Answers come back in seconds, with charts and a highlighted subgraph. No analyst required."
              cta={{ href: "/ask", label: "Ask the room" }}
            />
          </div>
        </div>
      </section>

      {/* ── The Loop */}
      <section id="loop" className="max-w-[1400px] mx-auto px-5 py-28">
        <div className="max-w-3xl">
          <h2 className="display-sm text-5xl md:text-6xl">
            Capture, structure,
            <span className="text-text-faint">
              and query.
            </span>
          </h2>
          <p className="mt-6 text-text-secondary text-lg leading-relaxed max-w-2xl">
            Detection emits structured events during the activation. Events
            accumulate in a session-scoped spatial graph. Downstream consumers
            — the live view, the 3D twin, and the report — read from that graph
            independently and in real time.
          </p>
        </div>

        <div className="mt-14 grid md:grid-cols-4 gap-4">
          <LoopStep
            i={1}
            icon={<Cctv size={20} />}
            title="Capture"
            text="Zone entries, dwell events, and movement paths are emitted from the perception layer as structured events."
          />
          <LoopStep
            i={2}
            icon={<Zap size={20} />}
            title="Structure"
            text="Events are written to a session graph. Every person, zone, and interaction becomes a queryable node."
          />
          <LoopStep
            i={3}
            icon={<Eye size={20} />}
            title="Visualise"
            text="The graph drives a live operator dashboard and a scrubbable 3D replay. Both surfaces read from the same store."
          />
          <LoopStep
            i={4}
            icon={<Brain size={20} />}
            title="Query"
            text="Ask plain-English questions. The system translates them to graph queries and returns answers with charts."
          />
        </div>
      </section>

      {/* ── Privacy */}
      <section
        id="privacy"
        className="border-y border-border-hairline bg-bg-canvas/50"
      >
        <div className="max-w-[1400px] mx-auto px-5 py-24 grid lg:grid-cols-2 gap-16 items-center">
          <div>
            <h2 className="display-sm text-5xl md:text-6xl">
              No faces.
              <br />
              No re-identification.
              <br />
              <span className="text-text-faint">Local by default.</span>
            </h2>
            <p className="mt-7 text-text-secondary text-lg leading-relaxed max-w-xl">
              Frames are processed on-device. Raw video auto-purges in minutes;
              only the structured graph survives. Person IDs reset per session
              — we never link a visitor to themselves across days, cameras or
              activations.
            </p>
            <p className="mt-3 text-text-muted text-sm">
              GDPR / CCPA / UK DPA-aware by construction. Per-pixel masking
              available for sensitive surfaces.
            </p>
          </div>

          <div className="panel-elevated p-8 grid grid-cols-2 gap-x-6 gap-y-4 text-sm">
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
      <section id="pricing" className="max-w-[1400px] mx-auto px-5 py-28">
        <div className="max-w-2xl mb-14">
          <h2 className="display-sm text-5xl md:text-6xl">
            Per activation.
            <span className="text-text-faint">
              Built for agency calendars.
            </span>
          </h2>
          <p className="mt-6 text-text-secondary text-lg leading-relaxed">
            A live dashboard, a 3D replay, and a client report — delivered as a
            measurement line item on any activation.
          </p>
        </div>

        <div className="grid md:grid-cols-3 gap-5">
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
        <div className="max-w-[1400px] mx-auto px-5 py-12 grid md:grid-cols-2 gap-10">
          <div>
            <div className="display-sm text-5xl md:text-6xl tracking-tight">
              Let&apos;s talk
              <span className="text-accent">.</span>
            </div>
            <p className="mt-5 text-text-secondary max-w-md text-lg leading-relaxed">
              Whether you&apos;re planning your next activation or want
              measurement plugged into a season-long calendar, we&apos;re ready.
            </p>
            <div className="mt-7 flex flex-wrap gap-3">
              <div className="pill-group h-12 pl-4 pr-5 text-sm">
                <span className="text-text-muted">hello@realmspace.io</span>
              </div>
              <div className="pill-group h-12 pl-4 pr-5 text-sm">
                <span className="text-text-muted">+44 7000 000 000</span>
              </div>
            </div>
          </div>

          <div className="md:text-right flex flex-col md:items-end gap-4">
            <div className="flex items-center gap-2.5">
              <div className="relative w-6 h-6">
                <div className="absolute inset-0 rounded-full bg-accent" />
                <div className="absolute inset-[3px] rounded-full bg-bg-base" />
                <div className="absolute inset-[5px] rounded-full bg-accent" />
              </div>
              <span className="text-sm font-semibold">RealmSpace</span>
            </div>
            <div className="text-xs text-text-muted">
              by Floats XR
            </div>
            <div className="text-xs text-text-muted">
              © 2026 · Built for agencies, brand teams, and the venues that hold
              them.
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}

function Metric({
  value,
  label,
  delta,
  accent,
}: {
  value: string;
  label: string;
  delta: string;
  accent?: boolean;
}) {
  return (
    <div>
      <div
        className={`text-4xl font-semibold tabular tracking-[-0.02em] ${accent ? "text-accent" : "text-text-primary"}`}
      >
        {value}
      </div>
      <div className="mt-1.5 text-xs text-text-muted">{label}</div>
      <div className="mt-1 inline-flex items-center gap-1 text-[11px] text-accent tabular">
        <ArrowUpRight size={11} />
        {delta}
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
    <div className="panel p-7 relative group hover:border-border-subtle transition-colors">
      <div className="absolute top-6 right-6 text-[10px] tabular text-text-faint font-medium">
        0{i}
      </div>
      <div className="w-11 h-11 rounded-full bg-bg-elevated border border-border-subtle flex items-center justify-center text-accent">
        {icon}
      </div>
      <h3 className="text-xl font-semibold tracking-tight">{title}</h3>
      <p className="mt-2.5 text-sm text-text-secondary leading-relaxed">
        {text}
      </p>
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
      className={`m3-card p-7 flex flex-col gap-4 group ${
        accent ? "ring-1 ring-primary/40" : ""
      }`}
    >
      <div className="flex items-center gap-3">
        <div
          className={`w-11 h-11 rounded-full flex items-center justify-center ${
            accent
              ? "bg-accent text-text-inverse"
              : "bg-bg-elevated border border-border-subtle text-accent"
          }`}
        >
          {icon}
        </div>
        <span className="eyebrow">{tag}</span>
      </div>
      <h3 className="text-[26px] font-semibold tracking-tight leading-[1.05]">
        {title}
      </h3>
      <p className="text-[15px] text-text-secondary leading-relaxed flex-1">
        {body}
      </p>
      <Link
        href={cta.href}
        className="text-sm text-accent inline-flex items-center gap-1.5 group-hover:gap-2.5 transition-all font-medium"
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

function PrivacyRow({
  children,
  ok,
  ko,
}: {
  children: React.ReactNode;
  ok?: boolean;
  ko?: boolean;
}) {
  return (
    <div className="flex items-center gap-3">
      <span
        className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0 ${
          ok
            ? "bg-accent/15 text-accent border border-accent/30"
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
      className={`m3-card-elevated p-8 flex flex-col gap-6 relative ${
        featured ? "ring-1 ring-primary/40" : ""
      }`}
    >
      {featured && (
        <Pill variant="success" className="absolute -top-3 left-8">
          Most popular
        </Pill>
      )}
      <div>
        <div className="eyebrow">{name}</div>
        <div className="mt-4 flex items-baseline gap-2">
          <span className="text-5xl font-semibold tabular tracking-[-0.025em]">
            {price}
          </span>
        </div>
        <div className="text-sm text-text-muted mt-1.5">{unit}</div>
      </div>
      <ul className="text-[15px] text-text-secondary space-y-3 flex-1">
        {features.map((f) => (
          <li key={f} className="flex gap-3">
            <span className="text-accent mt-1 shrink-0">
              <Zap size={14} strokeWidth={2.5} />
            </span>
            {f}
          </li>
        ))}
      </ul>
      <Button variant={featured ? "primary" : "secondary"} fullWidth size="lg">
        Book a pilot
      </Button>
    </div>
  );
}

/** Hero preview — a polished card that hints at the live dashboard. */
function HeroPreview() {
  return (
    <div className="relative">
      <div className="absolute -inset-6 bg-gradient-to-br from-accent/15 via-transparent to-accent/5 blur-3xl -z-10" />
      <div className="panel-elevated overflow-hidden">
        {/* Header strip */}
        <div className="flex items-center justify-between px-5 h-12 border-b border-border-hairline bg-bg-elevated/60">
          <div className="flex items-center gap-2.5">
            <span className="live-dot" />
            <span className="text-[11px] tabular tracking-[0.16em] uppercase text-text-secondary font-medium">
              LIVE · 21:14:08
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-border-subtle" />
            <span className="w-2 h-2 rounded-full bg-border-subtle" />
            <span className="w-2 h-2 rounded-full bg-border-subtle" />
          </div>
        </div>

        {/* KPI strip */}
        <div className="grid grid-cols-3 gap-px bg-border-hairline">
          <PreviewTile label="People now" value="14" />
          <PreviewTile label="Avg dwell" value="4m 23s" />
          <PreviewTile label="Triggers" value="2,186" accent />
        </div>

        {/* Twin preview */}
        <div className="relative h-60 bg-bg-viewport map-grid overflow-hidden">
          <div className="absolute inset-0 flex items-center justify-center text-text-faint text-[10px] uppercase tracking-[0.18em] font-medium">
            digital twin · pavilion no. 7
          </div>
          {[
            ["20%", "30%", "var(--accent)"],
            ["45%", "35%", "var(--accent-violet)"],
            ["62%", "20%", "var(--accent-cyan)"],
            ["55%", "65%", "var(--accent)"],
            ["75%", "70%", "var(--accent-amber)"],
            ["32%", "55%", "var(--accent-blue)"],
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
          {/* Zone outlines */}
          <div className="absolute left-[18%] top-[18%] w-[26%] h-[40%] border border-accent-violet/40 rounded-lg" />
          <div className="absolute left-[58%] top-[12%] w-[28%] h-[30%] border border-accent-cyan/40 rounded-lg" />
          <div className="absolute left-[28%] top-[60%] w-[36%] h-[30%] border border-accent/40 rounded-lg" />
        </div>

        {/* Footer event log */}
        <div className="p-4 border-t border-border-hairline text-[11px] font-mono text-text-secondary flex items-center gap-3 truncate">
          <span className="text-accent">›</span>
          P-216 looked at Bottle Wall · 7s
          <span className="ml-auto text-text-faint inline-flex items-center gap-1">
            <ArrowDownRight size={11} />
            view full log
          </span>
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
    <div className="bg-bg-panel p-5">
      <div className="text-[10px] uppercase tracking-[0.16em] text-text-muted font-medium">
        {label}
      </div>
      <div
        className={`text-3xl font-semibold tabular tracking-[-0.02em] mt-1.5 ${
          accent ? "text-accent" : "text-text-primary"
        }`}
      >
        {value}
      </div>
    </div>
  );
}
