import { test, expect } from "@playwright/test";
import { resetDemo, seedSession, visitAndShoot, type Step } from "./helpers";

const STEPS: Step[] = [
  { id: "campaigns-list", selector: "[data-tour='wetlab-campaigns-list']", path: () => `/wetlab/campaigns` },
  { id: "overview", selector: "[data-tour='wetlab-campaign-overview']", path: (e) => `/wetlab/campaigns/${e.campaign_id}` },
  { id: "comparison", selector: "[data-tour='wetlab-batch-comparison']", path: (e) => `/wetlab/campaigns/${e.campaign_id}/compare` },
  { id: "timeline", selector: "[data-tour='wetlab-qc-flag']", path: (e, ctx) => ctx.leadBatchId ? `/wetlab/campaigns/${e.campaign_id}/batches/${ctx.leadBatchId}/timeline` : null },
  { id: "audit", selector: "[data-tour='wetlab-audit-ack']", path: (e) => `/wetlab/campaigns/${e.campaign_id}/audit` },
  { id: "methods-export", selector: "[data-tour='wetlab-methods-export']", path: (e) => `/wetlab/campaigns/${e.campaign_id}/methods` },
  { id: "export", selector: "[data-tour='wetlab-export']", path: (e) => `/wetlab/campaigns/${e.campaign_id}` },
];

test("wet-lab tour walks every anchor", async ({ page, request }) => {
  const entry = await resetDemo(request, "wetlab");
  await seedSession(page, entry);

  const headers = { Authorization: `Demo ${entry.session_token}` };
  const batches = await (
    await request.get(
      `/api/v1/wetlab/campaigns/${entry.campaign_id}/batches?org_id=${entry.org_id}&include_metrics=true`,
      { headers },
    )
  ).json();
  const lead =
    batches.find((b: any) => b.batch_number === "Batch_004C") ||
    batches.find((b: any) => b.summary_metrics?.lead_condition) ||
    batches[0];
  const leadBatchId = lead?.id ? String(lead.id) : "";

  const ctx = { leadBatchId };
  const outDir = "screenshots/wetlab";
  for (let i = 0; i < STEPS.length; i += 1) {
    await visitAndShoot(page, STEPS[i], entry, ctx, outDir, i);
  }
  expect(leadBatchId, "wet-lab campaign must have a lead batch").not.toBe("");
});
