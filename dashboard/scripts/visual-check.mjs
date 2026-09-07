import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const out = resolve(import.meta.dirname, "../.hermes/ui-refresh");
mkdirSync(out, { recursive: true });
const browser = await chromium.connectOverCDP("http://127.0.0.1:9224");
const failures = [];
const consoleErrors = [];
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text()); });
page.on("pageerror", (err) => consoleErrors.push(err.message));

for (const theme of ["dark", "light"]) {
  await page.addInitScript((t) => localStorage.setItem("realmspace-theme", t), theme);
  for (const route of ["/", "/sessions", "/live", "/twin", "/ask", "/agents", "/report", "/login", "/sessions/new"]) {
    const response = await page.goto(`http://127.0.0.1:3001${route}`, { waitUntil: "networkidle", timeout: 60000 });
    const status = response?.status() ?? 0;
    const bodyText = await page.locator("body").innerText();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    const design = await page.getAttribute("html", "data-design");
    const actualTheme = await page.getAttribute("html", "data-theme");
    const h1 = await page.locator("h1").first().textContent().catch(() => "");
    if (status !== 200 || !bodyText.trim() || overflow || design !== "signal-room-2026" || actualTheme !== theme) {
      failures.push({ theme, route, status, overflow, design, actualTheme, bodyChars: bodyText.length });
    }
    console.log(`${theme.padEnd(5)} ${route.padEnd(14)} HTTP ${status} overflow=${overflow} h1=${JSON.stringify(h1?.trim().slice(0, 60))}`);
    if (route === "/" || route === "/live") {
      const name = route === "/" ? "home" : "live";
      await page.screenshot({ path: resolve(out, `${name}-${theme}.png`), fullPage: true });
    }
  }
}

const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
mobile.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(`mobile: ${msg.text()}`); });
await mobile.addInitScript(() => localStorage.setItem("realmspace-theme", "dark"));
for (const route of ["/", "/sessions", "/live", "/ask", "/sessions/new"]) {
  const response = await mobile.goto(`http://127.0.0.1:3001${route}`, { waitUntil: "networkidle", timeout: 60000 });
  const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  const mobileNavVisible = route === "/" || route === "/sessions/new" ? true : await mobile.locator(".mobile-nav").isVisible();
  if ((response?.status() ?? 0) !== 200 || overflow || !mobileNavVisible) failures.push({ mobile: true, route, status: response?.status(), overflow, mobileNavVisible });
  console.log(`mobile ${route.padEnd(14)} HTTP ${response?.status()} overflow=${overflow} nav=${mobileNavVisible}`);
  if (route === "/" || route === "/live") await mobile.screenshot({ path: resolve(out, `${route === "/" ? "home" : "live"}-mobile.png`), fullPage: true });
}

await browser.close();
console.log(`\nScreenshots: ${out}`);
console.log(`Console errors: ${consoleErrors.length}`);
for (const e of [...new Set(consoleErrors)].slice(0, 20)) console.log(`  ERROR ${e}`);
if (failures.length) {
  console.error(`Visual checks failed: ${JSON.stringify(failures, null, 2)}`);
  process.exit(1);
}
if (consoleErrors.some((e) => !e.includes("favicon") && !e.includes("ERR_BLOCKED_BY_CLIENT"))) process.exit(2);
console.log("All visual route checks passed.");
