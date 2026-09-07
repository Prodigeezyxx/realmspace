import Link from "next/link";

import { ThemeToggle } from "@/components/theme/ThemeToggle";

const capabilities = [
  {
    n: "01",
    icon: "monitoring",
    label: "Observe",
    title: "See how people use the space.",
    body: "Track footfall, dwell and touchpoint use while the event is open. Video is processed on the edge and is not stored.",
    href: "/live",
  },
  {
    n: "02",
    icon: "view_in_ar",
    label: "Replay",
    title: "Review movement after the event.",
    body: "Replay anonymous tracks on the venue layout, move through the timeline and inspect one visitor or zone at a time.",
    href: "/twin",
  },
  {
    n: "03",
    icon: "forum",
    label: "Question",
    title: "Query the session in plain English.",
    body: "Ask about visitors, dwell, zones or interactions. Each answer maps to a constrained SQL query against recorded events.",
    href: "/ask",
  },
];

const loop = [
  ["Capture", "The camera detects people on the device and assigns temporary IDs for the session."],
  ["Record", "Zone entries, dwell and interactions are stored as timestamped events."],
  ["Review", "The dashboard, replay and report read the same session record."],
  ["Respond", "Rules can notify staff or call an approved webhook when a condition is met."],
];

function Mark() {
  return <span className="brand-mark" aria-hidden="true" />;
}

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-bg-base text-text-primary">
      <header className="sticky top-0 z-50 border-b border-border-hairline bg-bg-base/90 backdrop-blur-xl">
        <div className="max-w-[1520px] mx-auto h-[72px] px-4 md:px-8 flex items-center justify-between gap-4">
          <Link href="/" className="inline-flex items-center gap-2.5 font-bold tracking-[-.03em]">
            <Mark />
            <span>realmspace</span>
          </Link>
          <nav aria-label="Primary" className="hidden md:flex items-center gap-1 text-sm font-semibold text-text-secondary">
            <Link href="#system" className="px-4 py-3 rounded-[14px] hover:bg-bg-elevated hover:text-text-primary">System</Link>
            <Link href="#privacy" className="px-4 py-3 rounded-[14px] hover:bg-bg-elevated hover:text-text-primary">Privacy</Link>
            <Link href="#deploy" className="px-4 py-3 rounded-[14px] hover:bg-bg-elevated hover:text-text-primary">Deploy</Link>
          </nav>
          <div className="flex items-center gap-2">
            <ThemeToggle className="border border-border-hairline bg-bg-panel" />
            <Link href="/login" className="hidden sm:inline-flex min-h-11 items-center px-4 rounded-full text-sm font-semibold text-text-secondary hover:bg-bg-elevated">Sign in</Link>
            <Link href="/live" className="inline-flex min-h-11 items-center gap-2 rounded-full bg-accent pl-5 pr-3 font-bold text-sm text-[var(--md-sys-color-on-primary)] shadow-[var(--glow-green)]">
              Open live
              <span className="material-symbol material-symbol-sm">arrow_outward</span>
            </Link>
          </div>
        </div>
      </header>

      <main>
        <section className="spatial-hero min-h-[calc(100dvh-72px)] flex items-center border-b border-border-hairline">
          <div className="max-w-[1520px] mx-auto w-full px-4 md:px-8 py-16 md:py-24 grid lg:grid-cols-12 gap-12 lg:gap-16 items-center">
            <div className="lg:col-span-7">
              <div className="page-kicker">Spatial intelligence for physical experiences</div>
              <h1 className="display-large mt-6 max-w-[980px]">
                See what happened<br />
                <span className="text-text-faint">inside the space.</span>
              </h1>
              <p className="mt-8 md:mt-10 text-lg md:text-xl text-text-secondary max-w-2xl leading-relaxed text-pretty">
                realmspace records footfall, dwell and touchpoint activity for physical events. Use the same session data in the live dashboard, 3D replay and client report.
              </p>
              <div className="mt-9 flex flex-wrap items-center gap-3">
                <Link href="/live" className="inline-flex min-h-14 items-center gap-3 rounded-full bg-accent pl-7 pr-4 font-bold text-[var(--md-sys-color-on-primary)] shadow-[var(--glow-green)] hover:bg-accent-bright transition-colors">
                  Open the live dashboard <span className="material-symbol">arrow_outward</span>
                </Link>
                <Link href="/report" className="inline-flex min-h-14 items-center gap-3 rounded-full border border-border-strong bg-bg-panel px-7 font-semibold hover:bg-bg-elevated transition-colors">
                  View a sample report
                </Link>
              </div>
              <div className="mt-12 flex flex-wrap gap-x-8 gap-y-3 text-xs text-text-muted font-mono">
                <span className="inline-flex items-center gap-2"><i className="w-1.5 h-1.5 rounded-full bg-accent" />edge processed</span>
                <span>anonymous IDs</span><span>zero frames stored</span><span>queryable in seconds</span>
              </div>
            </div>

            <div className="lg:col-span-5">
              <LiveInstrument />
            </div>
          </div>
        </section>

        <section id="system" className="max-w-[1520px] mx-auto px-4 md:px-8 py-24 md:py-32">
          <div className="grid lg:grid-cols-12 gap-12 mb-16 items-end">
            <div className="lg:col-span-7">
              <span className="page-kicker">One signal, three ways in</span>
              <h2 className="display-medium mt-5">One session record.<br /><span className="text-text-faint">Three working views.</span></h2>
            </div>
            <p className="lg:col-span-5 text-lg text-text-secondary leading-relaxed max-w-xl">
              Operators watch the event as it runs. Teams replay it afterwards. Clients receive a report built from the same data.
            </p>
          </div>
          <div className="grid lg:grid-cols-3 border-y border-border-hairline">
            {capabilities.map((item, i) => (
              <Link key={item.n} href={item.href} className={`group py-8 lg:py-12 lg:px-8 ${i > 0 ? "border-t lg:border-t-0 lg:border-l border-border-hairline" : ""}`}>
                <div className="flex items-center justify-between text-text-muted font-mono text-xs">
                  <span>{item.n}</span><span className="material-symbol group-hover:text-accent transition-colors">{item.icon}</span>
                </div>
                <div className="mt-12 text-xs uppercase tracking-[.14em] font-bold text-accent">{item.label}</div>
                <h3 className="headline-medium mt-3 max-w-sm">{item.title}</h3>
                <p className="mt-4 text-text-secondary leading-relaxed max-w-sm">{item.body}</p>
                <div className="mt-8 inline-flex items-center gap-2 text-sm font-bold group-hover:text-accent transition-colors">Open surface <span className="material-symbol material-symbol-sm">arrow_forward</span></div>
              </Link>
            ))}
          </div>
        </section>

        <section className="border-y border-border-hairline bg-bg-panel">
          <div className="max-w-[1520px] mx-auto grid lg:grid-cols-2">
            <div className="px-4 md:px-8 py-20 md:py-28 lg:border-r border-border-hairline">
              <span className="page-kicker">The operating loop</span>
              <h2 className="display-small mt-5 max-w-xl">How a session<br /><span className="text-text-faint">becomes a report.</span></h2>
              <p className="text-text-secondary text-lg leading-relaxed mt-6 max-w-xl">Each view reads the same recorded events. Counts and timelines can be traced back to the session log.</p>
            </div>
            <ol className="divide-y divide-border-hairline border-t lg:border-t-0">
              {loop.map(([title, body], i) => (
                <li key={title} className="p-6 md:p-8 grid grid-cols-[44px_1fr] gap-5 hover:bg-bg-elevated transition-colors">
                  <span className="font-mono text-xs text-accent pt-1">0{i + 1}</span>
                  <div><h3 className="title-large">{title}</h3><p className="text-text-secondary mt-2 leading-relaxed max-w-lg">{body}</p></div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="privacy" className="max-w-[1520px] mx-auto px-4 md:px-8 py-24 md:py-32 grid lg:grid-cols-12 gap-12 items-center">
          <div className="lg:col-span-7">
            <span className="page-kicker">Privacy is architecture</span>
            <h2 className="display-medium mt-5">Store the event data,<br /><span className="text-text-faint">not the video.</span></h2>
            <p className="mt-7 text-lg text-text-secondary leading-relaxed max-w-2xl">The camera is processed on the edge. Visitor IDs expire with the session, and the stored record contains events rather than faces.</p>
          </div>
          <div className="lg:col-span-5 panel-elevated p-0 overflow-hidden">
            {[
              ["Edge processing", "No video sent to the cloud"],
              ["Anonymous tracks", "Session-scoped visitor IDs"],
              ["Explicit retention", "Controls built into every session"],
              ["Auditable answers", "Queries map to constrained templates"],
            ].map(([title, body]) => (
              <div key={title} className="p-5 flex gap-4 border-b last:border-b-0 border-border-hairline">
                <span className="material-symbol text-accent">verified_user</span>
                <div><div className="font-bold">{title}</div><div className="text-sm text-text-muted mt-1">{body}</div></div>
              </div>
            ))}
          </div>
        </section>

        <section id="deploy" className="bg-accent text-[var(--md-sys-color-on-primary)]">
          <div className="max-w-[1520px] mx-auto px-4 md:px-8 py-20 md:py-28 flex flex-col lg:flex-row lg:items-end justify-between gap-10">
            <div>
              <div className="text-xs font-bold uppercase tracking-[.14em]">Next activation</div>
              <h2 className="display-small mt-4 max-w-4xl">Set up the next event before the doors open.</h2>
            </div>
            <Link href="/sessions/new" className="inline-flex min-h-14 shrink-0 items-center gap-3 rounded-full bg-[var(--brand-ink)] text-white px-7 font-bold">Create a session <span className="material-symbol">arrow_outward</span></Link>
          </div>
        </section>
      </main>

      <footer className="border-t border-border-hairline bg-bg-base">
        <div className="max-w-[1520px] mx-auto px-4 md:px-8 py-8 flex flex-wrap items-center justify-between gap-4 text-sm text-text-muted">
          <div className="inline-flex items-center gap-2 font-bold text-text-primary"><Mark /> realmspace <span className="font-normal text-text-muted">by Floats</span></div>
          <div>© 2026 · Physical experiences, made legible.</div>
        </div>
      </footer>
    </div>
  );
}

function LiveInstrument() {
  return (
    <div className="rounded-[32px] border border-border-subtle bg-bg-panel p-3 shadow-[var(--shadow-lg)] rotate-[1deg] hover:rotate-0 transition-transform duration-500">
      <div className="rounded-[23px] overflow-hidden border border-border-hairline bg-bg-canvas">
        <div className="h-14 px-5 flex items-center justify-between border-b border-border-hairline">
          <div className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-[.12em]"><span className="live-dot" /> pavilion 07</div>
          <span className="font-mono text-xs text-text-muted">21:14:08</span>
        </div>
        <div className="grid grid-cols-3 bg-border-hairline gap-px">
          <Signal label="People" value="14" />
          <Signal label="Dwell" value="4:23" />
          <Signal label="ROI" value="4.2×" accent />
        </div>
        <div className="signal-grid relative h-[330px] bg-bg-viewport overflow-hidden">
          <div className="absolute inset-5 border border-border-subtle rounded-[18px] rotate-[-4deg]" />
          <div className="absolute left-[18%] top-[24%] w-[30%] h-[32%] border border-accent-violet/50 rounded-[24px_8px_24px_8px]" />
          <div className="absolute right-[12%] bottom-[16%] w-[38%] h-[34%] border border-accent/60 rounded-[10px_28px_10px_28px]" />
          {[[24,32],[42,46],[61,27],[68,61],[36,72],[80,42]].map(([x,y],i)=>(
            <span key={i} className="absolute w-3 h-3 rounded-[3px_8px_3px_8px] bg-accent shadow-[0_0_18px_var(--accent)]" style={{left:`${x}%`,top:`${y}%`}} />
          ))}
          <div className="absolute left-5 right-5 bottom-5 rounded-[16px] bg-bg-panel/90 border border-border-hairline p-4 backdrop-blur-md">
            <div className="font-mono text-xs text-text-secondary"><span className="text-accent mr-2">↳</span>P-216 entered Bottle Wall · dwell 47s</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Signal({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return <div className="bg-bg-panel p-4"><div className="text-[9px] uppercase tracking-[.14em] text-text-muted font-bold">{label}</div><div className={`data-value mt-2 text-2xl tabular ${accent ? "text-accent" : ""}`}>{value}</div></div>;
}
