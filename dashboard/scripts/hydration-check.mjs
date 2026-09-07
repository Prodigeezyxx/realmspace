import { chromium } from "playwright";

const browser = await chromium.launch({
  headless: true,
  executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
});
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const problems = [];
page.on("console", (msg) => {
  const text = msg.text();
  if (msg.type() === "error" || /hydration|uncaught/i.test(text)) problems.push(`console:${msg.type()}:${text}`);
});
page.on("pageerror", (err) => problems.push(`pageerror:${err.message}`));
await page.addInitScript(() => {
  localStorage.setItem("realmspace-theme", "dark");
  localStorage.setItem("realmspace.store.v1", JSON.stringify({
    sessions: [{
      id:"ses_hydration_test",name:"Hydration test",type:"brand_activation",status:"completed",
      startAt:"2026-09-01T10:00:00.000Z",endAt:"2026-09-01T12:00:00.000Z",
      createdAt:"2026-09-01T09:00:00.000Z",venue:"Test venue",timezone:"Europe/London",
      expectedDailyFootfall:10,cameras:[{id:"cam_1",name:"Camera 1"}],zones:[],touchpoints:[],
      privacy:{mode:"default",consentSignage:true,retentionDays:30},goals:{},isDemo:false
    }],
    activeId:"ses_hydration_test"
  }));
});
for (const route of ["/sessions", "/live", "/twin", "/ask", "/agents", "/report"]) {
  problems.length = 0;
  // Twin keeps a 3D scene and bus socket active, so network-idle never settles.
  const waitUntil = route === "/twin" ? "domcontentloaded" : "networkidle";
  const response = await page.goto(`http://localhost:3100${route}`, { waitUntil, timeout: 60000 });
  await page.waitForTimeout(1000);
  const title = await page.locator("h1,h2").first().textContent().catch(() => "");
  console.log(`${route.padEnd(12)} HTTP ${response?.status()} title=${JSON.stringify(title?.trim().slice(0,70))} problems=${problems.length}`);
  if (problems.length) for (const p of problems) console.log(`  ${p.slice(0,500)}`);
  const hydrationProblems = problems.filter((problem) => /Hydration failed|script tag while rendering/i.test(problem));
  if (response?.status() !== 200 || hydrationProblems.length) process.exitCode = 1;
}
await browser.close();
if (!process.exitCode) console.log("Hydration checks passed.");
