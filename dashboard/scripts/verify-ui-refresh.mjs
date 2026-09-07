import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const read = (path) => readFileSync(resolve(root, path), "utf8");
const checks = [];

function expect(name, condition) {
  checks.push({ name, ok: Boolean(condition) });
}

const css = read("src/app/globals.css");
const rootLayout = read("src/app/layout.tsx");
const appLayout = read("src/app/(app)/layout.tsx");
const nav = read("src/components/chrome/NavRail.tsx");
const live = read("src/app/(app)/live/page.tsx");
const landing = read("src/app/page.tsx");

expect("2026 design root marker", rootLayout.includes('data-design="signal-room-2026"'));
expect("signal-lime brand token", css.includes("--brand-signal: #b7f34a"));
expect("warm light-theme canvas", css.includes("--md-sys-color-surface: #f4f3ed"));
expect("fluid content spacing", css.includes("--page-gutter: clamp("));
expect("reduced-motion support", css.includes("@media (prefers-reduced-motion: reduce)"));
expect("mobile bottom navigation", nav.includes("mobile-nav") && nav.includes("md:hidden"));
expect("desktop expanded navigation", nav.includes("realm-nav-rail") && nav.includes("xl:w-[224px]"));
expect("lowercase brand in app navigation", nav.includes(">realmspace<"));
const themeToggle = read("src/components/theme/ThemeToggle.tsx");
expect("persistent theme preference", themeToggle.includes('localStorage.setItem("realmspace-theme"'));
expect("accessible theme toggle", themeToggle.includes("aria-label={`Use"));
expect("new app shell composition", appLayout.includes("realm-app-shell") && appLayout.includes("realm-main"));
expect("live dashboard uses side column at laptop widths", live.includes("lg:col-span-8") && live.includes("lg:col-span-4"));
expect("live page has compact intent header", live.includes("Live intelligence") && live.includes("page-kicker"));
expect("landing uses authored spatial composition", landing.includes("spatial-hero") && landing.includes("signal-grid"));
expect("landing uses lowercase realmspace brand", landing.includes("realmspace") && !landing.includes(">RealmSpace<"));
expect("landing describes constrained SQL, not Cypher", landing.includes("constrained SQL") && !landing.includes("Cypher"));

const failed = checks.filter((check) => !check.ok);
for (const check of checks) {
  console.log(`${check.ok ? "PASS" : "FAIL"}  ${check.name}`);
}
console.log(`\n${checks.length - failed.length}/${checks.length} checks passed`);
if (failed.length) process.exit(1);
