import {
  ArrowDownRight,
  ArrowUpRight,
  Download,
  FileText,
  Quote,
  Share2,
  Sparkles,
  Users,
} from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Panel } from "@/components/ui/Panel";
import { Pill } from "@/components/ui/Pill";
import { Sparkline } from "@/components/ui/Sparkline";
import { Heatmap } from "@/components/viz/Heatmap";

export default function ReportPage() {
  return (
    <div className="max-w-[1100px] mx-auto p-6 md:p-10 space-y-10">
      {/* ── Cover */}
      <header className="space-y-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <Pill variant="info">
            <FileText size={11} />
            Client report · Day 1
          </Pill>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" icon={<Share2 size={14} />}>
              Share with client
            </Button>
            <Button variant="primary" size="sm" icon={<Download size={14} />}>
              Export PDF
            </Button>
          </div>
        </div>

        <div className="grid md:grid-cols-[1fr_auto] gap-6 items-end">
          <div>
            <p className="text-text-muted text-sm tabular">
              MAISON VIVIENNE · PAVILION No. 7 · LAGOS · 18 MAY 2026
            </p>
            <h1 className="text-5xl md:text-6xl font-semibold tracking-tight leading-[0.95] mt-3">
              The day the room
              <br />
              <span className="text-text-muted">talked back.</span>
            </h1>
            <p className="mt-4 text-text-secondary text-lg max-w-xl leading-relaxed">
              Day 1 outperformed the brief on every benchmark — and the data
              tells us why. The Mirror Room is the single most important asset
              you have. The Scent Quiz is the closer.
            </p>
          </div>
          <div className="panel-elevated p-5 min-w-[220px]">
            <div className="text-[10px] uppercase tracking-[0.18em] text-text-muted">
              ROI per dollar
            </div>
            <div className="text-5xl font-semibold tabular tracking-tight text-accent-green mt-1">
              4.2×
            </div>
            <div className="text-xs text-text-muted mt-1 tabular">
              vs. brief target of 2.5×
            </div>
          </div>
        </div>
      </header>

      {/* ── Hero numbers */}
      <section className="grid md:grid-cols-4 gap-3">
        <BigNumber
          label="Visitors"
          value="1,287"
          delta="+18% vs Yday"
          direction="up"
          spark={[12, 18, 22, 19, 27, 31, 28, 30]}
        />
        <BigNumber
          label="Avg dwell"
          value="6m 50s"
          delta="+12% vs Yday"
          direction="up"
          accent="cyan"
          spark={[120, 180, 220, 280, 330, 380, 410, 410]}
        />
        <BigNumber
          label="RFID captures"
          value="488"
          delta="+24% vs Yday"
          direction="up"
          accent="amber"
          spark={[20, 40, 65, 90, 120, 160, 200, 240]}
        />
        <BigNumber
          label="Cost per visit"
          value="$1.10"
          delta="−14% vs brief"
          direction="down"
          accent="green"
          spark={[1.6, 1.5, 1.4, 1.3, 1.2, 1.15, 1.12, 1.1]}
        />
      </section>

      {/* ── Headline */}
      <section className="panel-elevated p-7 relative overflow-hidden">
        <div className="absolute -top-12 -right-8 text-[200px] text-accent-blue/10 font-serif">
          <Quote />
        </div>
        <Pill variant="violet" className="mb-4">
          <Sparkles size={11} />
          Headline insight
        </Pill>
        <h2 className="text-3xl md:text-4xl font-semibold tracking-tight leading-tight max-w-3xl">
          Visitors who tried the Scent Quiz dwelled <span className="text-accent-cyan">2.4×</span> longer in the Lounge,
          and were <span className="text-accent-cyan">3.1×</span> more likely to capture a memory at the RFID Wall.
        </h2>
        <p className="mt-4 text-text-secondary max-w-2xl">
          The Scent Quiz is your conversion engine — not the Bottle Wall. We
          recommend surfacing it earlier in the funnel for Days 2–3 and
          reallocating ambient staff coverage toward the Lounge during the
          18:00–19:30 peak.
        </p>
      </section>

      {/* ── Funnel */}
      <section className="space-y-4">
        <h2 className="text-2xl font-semibold tracking-tight">
          The journey, in five steps.
        </h2>
        <Panel padded={false}>
          <div className="p-5 grid md:grid-cols-5 gap-px bg-border-hairline rounded-xl overflow-hidden">
            <FunnelStep step={1} name="Entry Arch" count={1287} share={100} />
            <FunnelStep step={2} name="Mirror Room" count={894} share={69} drop={31} />
            <FunnelStep step={3} name="Bottle Wall" count={712} share={55} drop={14} />
            <FunnelStep step={4} name="Lounge" count={488} share={38} drop={17} />
            <FunnelStep step={5} name="RFID Wall" count={488} share={38} drop={0} terminal />
          </div>
        </Panel>
      </section>

      {/* ── Heatmap */}
      <section className="grid md:grid-cols-2 gap-5">
        <Panel
          title="Attention map"
          subtitle="Where visitors spent the most aggregate time"
          padded={false}
        >
          <div className="p-3">
            <Heatmap height={300} />
          </div>
        </Panel>
        <Panel
          title="Top moments"
          subtitle="Notable spikes across the day"
        >
          <ul className="space-y-3 text-sm">
            <Moment
              time="18:42"
              title="Peak concurrent — 31 visitors"
              detail="Coincided with the first Scent Quiz leaderboard reveal."
            />
            <Moment
              time="14:12"
              title="P-126 dwelled 14m at Bottle Wall"
              detail="Highest single-person dwell of the day — captured 3 bottles."
            />
            <Moment
              time="16:33"
              title="Group of 6 formed in Lounge"
              detail="Largest organic group — lingered for 11m."
            />
            <Moment
              time="11:08"
              title="Mirror Engagement crossed 100 fires"
              detail="The Mirror is the single most-triggered surface in the pavilion."
            />
          </ul>
        </Panel>
      </section>

      {/* ── By zone */}
      <section className="space-y-4">
        <h2 className="text-2xl font-semibold tracking-tight">
          Zone by zone.
        </h2>
        <div className="grid md:grid-cols-2 gap-3">
          {[
            { zone: "Entry Arch", color: "#3e83f7", count: 1287, dwell: "0m 18s", conversion: 100 },
            { zone: "Mirror Room", color: "#bf5af2", count: 894, dwell: "2m 22s", conversion: 69 },
            { zone: "Bottle Wall", color: "#00d4ff", count: 712, dwell: "1m 11s", conversion: 55 },
            { zone: "Lounge", color: "#30d158", count: 488, dwell: "6m 50s", conversion: 38 },
            { zone: "Exit + RFID", color: "#ffd60a", count: 488, dwell: "0m 32s", conversion: 38 },
          ].map((z) => (
            <div key={z.zone} className="panel p-5 flex items-center justify-between gap-4">
              <div className="flex items-center gap-3 min-w-0">
                <span
                  className="w-2.5 h-12 rounded-full shrink-0"
                  style={{ background: z.color, boxShadow: `0 0 12px ${z.color}` }}
                />
                <div className="min-w-0">
                  <div className="text-base font-medium">{z.zone}</div>
                  <div className="text-xs text-text-muted tabular">
                    avg {z.dwell} dwell · {z.conversion}% capture
                  </div>
                </div>
              </div>
              <div className="text-right">
                <div className="text-2xl font-semibold tabular leading-none">
                  {z.count}
                </div>
                <div className="text-[10px] uppercase tracking-[0.15em] text-text-muted mt-1">
                  visitors
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Sponsor exposure */}
      <section>
        <Panel
          title="Sponsor exposure"
          subtitle="Aggregate seconds visitors spent looking at sponsored surfaces"
        >
          <ul className="space-y-3 text-sm">
            <SponsorRow name="Bottle Wall (LVMH)" seconds={48_312} share={100} />
            <SponsorRow name="AR Mirror (Sephora)" seconds={32_104} share={66} />
            <SponsorRow name="RFID Wall (Maison Vivienne)" seconds={18_240} share={38} />
            <SponsorRow name="Scent Diffuser (Diptyque)" seconds={9_120} share={19} />
          </ul>
        </Panel>
      </section>

      {/* ── Recommendations */}
      <section>
        <Panel
          title="Recommendations for Day 2"
          subtitle="Generated from observed behaviour"
        >
          <ol className="space-y-3 text-sm">
            <Rec
              n={1}
              text="Move the Scent Quiz one bay earlier so visitors hit it before the Bottle Wall — projected +14% RFID conversion."
            />
            <Rec
              n={2}
              text="Add a host at the Entry Arch between 17:30–19:30 to break the 30s drop-off pattern."
            />
            <Rec
              n={3}
              text="Re-light the Lounge's NW corner — it's a cold spot with 12% empty time."
            />
            <Rec
              n={4}
              text="Pre-stage 6 RFID bottles labelled 'Rose Nuit' near checkout — it's outperforming SKU forecasts 3:1."
            />
          </ol>
        </Panel>
      </section>

      {/* ── Footer */}
      <footer className="border-t border-border-hairline pt-6 flex items-center justify-between text-xs text-text-muted">
        <div>
          Generated by RealmSpace · Pavilion No. 7 · {new Date().toLocaleDateString()}
        </div>
        <div className="flex items-center gap-2">
          <Users size={11} />
          1,287 anonymous visitors · no faces stored
        </div>
      </footer>
    </div>
  );
}

function BigNumber({
  label,
  value,
  delta,
  direction,
  spark,
  accent,
}: {
  label: string;
  value: string;
  delta: string;
  direction: "up" | "down";
  spark: number[];
  accent?: "cyan" | "amber" | "green";
}) {
  const colorMap = {
    cyan: "var(--accent-cyan)",
    amber: "var(--accent-amber)",
    green: "var(--accent-green)",
  };
  return (
    <div className="panel-elevated p-5">
      <div className="text-[10px] uppercase tracking-[0.18em] text-text-secondary">
        {label}
      </div>
      <div
        className="text-4xl font-semibold tabular tracking-tight mt-2"
        style={{ color: accent ? colorMap[accent] : undefined }}
      >
        {value}
      </div>
      <div className="mt-2 flex items-center justify-between">
        <span
          className={`text-xs tabular inline-flex items-center gap-1 ${
            direction === "up" ? "text-accent-green" : "text-accent-green"
          }`}
        >
          {direction === "up" ? (
            <ArrowUpRight size={12} />
          ) : (
            <ArrowDownRight size={12} />
          )}
          {delta}
        </span>
        <Sparkline
          data={spark}
          width={70}
          height={24}
          stroke={accent ? colorMap[accent] : "var(--accent-blue)"}
        />
      </div>
    </div>
  );
}

function FunnelStep({
  step,
  name,
  count,
  share,
  drop,
  terminal,
}: {
  step: number;
  name: string;
  count: number;
  share: number;
  drop?: number;
  terminal?: boolean;
}) {
  return (
    <div className="bg-bg-panel p-4 relative">
      <div className="text-[10px] tabular text-text-faint">0{step}</div>
      <div className="text-sm font-medium mt-1">{name}</div>
      <div className="text-3xl font-semibold tabular tracking-tight mt-2">
        {count.toLocaleString()}
      </div>
      <div className="text-xs text-text-muted tabular mt-0.5">
        {share}% of entries
      </div>
      <div className="mt-3 h-1 rounded-full bg-bg-elevated overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-accent-blue to-accent-cyan"
          style={{ width: `${share}%` }}
        />
      </div>
      {!terminal && drop !== undefined && drop > 0 && (
        <div className="mt-2 text-[10px] text-accent-red tabular">
          −{drop}% drop
        </div>
      )}
    </div>
  );
}

function Moment({
  time,
  title,
  detail,
}: {
  time: string;
  title: string;
  detail: string;
}) {
  return (
    <li className="grid grid-cols-[56px_1fr] gap-3">
      <span className="text-text-muted tabular text-sm mt-0.5">{time}</span>
      <div>
        <div className="text-text-primary font-medium">{title}</div>
        <div className="text-text-muted text-xs mt-0.5">{detail}</div>
      </div>
    </li>
  );
}

function SponsorRow({
  name,
  seconds,
  share,
}: {
  name: string;
  seconds: number;
  share: number;
}) {
  return (
    <li className="space-y-1.5">
      <div className="flex items-center justify-between text-sm">
        <span>{name}</span>
        <span className="tabular text-text-secondary">
          {seconds.toLocaleString()}s
          <span className="text-text-muted ml-2">{share}%</span>
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-bg-elevated overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-accent-violet to-accent-blue"
          style={{ width: `${share}%` }}
        />
      </div>
    </li>
  );
}

function Rec({ n, text }: { n: number; text: string }) {
  return (
    <li className="flex gap-3">
      <span className="w-6 h-6 rounded-full bg-accent-blue/15 border border-accent-blue/40 text-accent-blue text-xs font-medium flex items-center justify-center shrink-0 mt-0.5 tabular">
        {n}
      </span>
      <span className="text-text-secondary leading-relaxed flex-1">{text}</span>
    </li>
  );
}
