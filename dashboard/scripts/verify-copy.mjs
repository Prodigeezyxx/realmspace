import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const read = (path) => readFileSync(resolve(root, path), "utf8");
const files = [
  "src/app/page.tsx",
  "src/app/(app)/live/page.tsx",
  "src/app/(app)/twin/page.tsx",
  "src/app/(app)/ask/page.tsx",
  "src/app/(app)/agents/page.tsx",
  "src/app/(app)/report/page.tsx",
  "src/components/report/ReportLive.tsx",
  "src/components/auth/LoginForm.tsx",
];
const text = files.map((f) => `${f}\n${read(f)}`).join("\n");
const banned = [
  ["internal phase labels", /Phase\s+[0-9]/i],
  ["vendor/model names", /Hermes LLM|Claude or GPT|laguna-xs/i],
  ["marketing absolutes", /real answers|every number|every minute|writes itself|exactly what happens/i],
  ["staged report headline", /room\s+(?:talked|talks)\s+back/i],
  ["unverified recommendation projection", /projected\s+[+−-]?\d/i],
  ["generic AI voice", /seamless|effortless|revolutionary|game-changing|unlock|supercharge/i],
];
const checks = [];
for (const [name, regex] of banned) checks.push({ name, ok: !regex.test(text) });
const home = read("src/app/page.tsx");
const twin = read("src/app/(app)/twin/page.tsx");
checks.push({ name: "public theme control", ok: home.includes("<ThemeToggle") });
checks.push({ name: "twin has explicit data-source labels", ok: twin.includes("Recorded events") && twin.includes("Demo dataset") });
checks.push({ name: "twin does not silently replace missing recordings", ok: !twin.includes("showing demo tracks") });
for (const check of checks) console.log(`${check.ok ? "PASS" : "FAIL"}  ${check.name}`);
const failed = checks.filter((c) => !c.ok);
console.log(`\n${checks.length - failed.length}/${checks.length} checks passed`);
if (failed.length) process.exit(1);
