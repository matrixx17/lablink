// Usage: node shoot-reskin.mjs <compchem|wetlab> <outTag>
// Resets a demo, seeds the session, screenshots key pages + an active tour step + finish modal.
import { chromium } from "@playwright/test";
import * as fs from "node:fs";

const BASE = process.env.DEMO_BASE_URL || "http://localhost:3000";
const domain = process.argv[2] || "compchem";
const tag = process.argv[3] || "after";
const OUT = `reskin-shots/${domain}-${tag}`;
fs.mkdirSync(OUT, { recursive: true });
const prefix = domain === "wetlab" ? "/wetlab" : "";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const r = await ctx.request.post(`${BASE}/demo/reset-and-enter?domain=${domain}`);
if (!r.ok()) throw new Error(`reset failed ${r.status()}`);
const entry = await r.json();

await page.goto(`${BASE}/demo`);
await page.evaluate((e) => {
  sessionStorage.setItem("lablink_demo_session", e.session_token);
  sessionStorage.setItem("lablink_demo_expires_at", e.session_expires_at);
  sessionStorage.setItem("lablink_demo_domain", e.domain);
  const skipped = JSON.stringify({ status: "skipped", stepIndex: 0 });
  sessionStorage.setItem("lablink.compchem.guidedTour", skipped);
  sessionStorage.setItem("lablink.wetlab.guidedTour", skipped);
}, entry);

async function shoot(name, path) {
  await page.goto(`${BASE}${path}${path.includes("?") ? "&" : "?"}org=${entry.org_id}`);
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
}

await shoot("01-list", `${prefix}/campaigns`);
await shoot("02-detail", `${prefix}/campaigns/${entry.campaign_id}`);
if (domain === "compchem") {
  await shoot("03-sar", `/campaigns/${entry.campaign_id}/sar`);
  await shoot("04-audit", `/campaigns/${entry.campaign_id}/audit`);
} else {
  await shoot("03-audit", `/wetlab/campaigns/${entry.campaign_id}/audit`);
  await shoot("04-methods", `/wetlab/campaigns/${entry.campaign_id}/methods`);
}
await browser.close();
console.log("done", domain, tag, "->", OUT);
