import type { Page, APIRequestContext } from "@playwright/test";
import * as path from "node:path";
import * as fs from "node:fs";

export type Domain = "compchem" | "wetlab";

export type DemoEntry = {
  status: string;
  domain: Domain;
  org_id: string;
  campaign_id: string;
  redirect_url: string;
  session_token: string;
  session_expires_at: string;
};

export async function resetDemo(request: APIRequestContext, domain: Domain): Promise<DemoEntry> {
  const resp = await request.post(`/demo/reset-and-enter?domain=${domain}`);
  if (!resp.ok()) {
    throw new Error(`reset-and-enter failed: ${resp.status()} ${await resp.text()}`);
  }
  return (await resp.json()) as DemoEntry;
}

export async function seedSession(page: Page, entry: DemoEntry) {
  // The dashboard reads its demo token from sessionStorage. We need to land
  // on the same origin first so storage is writable.
  await page.goto("/demo");
  await page.evaluate(({ token, expires, domain }) => {
    window.sessionStorage.setItem("lablink_demo_session", token);
    window.sessionStorage.setItem("lablink_demo_expires_at", expires);
    window.sessionStorage.setItem("lablink_demo_domain", domain);
    // Pre-seed both tour state keys as "skipped" so DemoTour doesn't
    // auto-start Shepherd, which would intercept clicks and visually
    // dominate every screenshot. We're verifying that the anchor exists
    // on each page, not the tooltip itself.
    const skipped = JSON.stringify({ status: "skipped", stepIndex: 0 });
    window.sessionStorage.setItem("lablink.compchem.guidedTour", skipped);
    window.sessionStorage.setItem("lablink.wetlab.guidedTour", skipped);
  }, { token: entry.session_token, expires: entry.session_expires_at, domain: entry.domain });
}

export type Step = {
  id: string;
  path: (entry: DemoEntry, ctx: Record<string, string>) => string | null;
  selector: string;
};

export async function visitAndShoot(
  page: Page,
  step: Step,
  entry: DemoEntry,
  ctx: Record<string, string>,
  outDir: string,
  index: number,
) {
  const targetPath = step.path(entry, ctx);
  if (!targetPath) throw new Error(`step ${step.id}: no path resolved`);
  await page.goto(`${targetPath}${targetPath.includes("?") ? "&" : "?"}org=${entry.org_id}`);
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
  // Wait for the anchor to attach — the actual proof the tour will land here.
  await page.waitForSelector(step.selector, { state: "visible", timeout: 15_000 });
  const fileName = path.join(outDir, `step-${String(index).padStart(2, "0")}-${step.id}.png`);
  fs.mkdirSync(outDir, { recursive: true });
  await page.screenshot({ path: fileName, fullPage: true });
}
