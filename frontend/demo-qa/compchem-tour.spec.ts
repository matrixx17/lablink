import { test, expect } from "@playwright/test";
import { resetDemo, seedSession, visitAndShoot, type Step } from "./helpers";

const STEPS: Step[] = [
  { id: "campaigns-list", selector: "[data-tour='compchem-campaigns-list']", path: () => `/campaigns` },
  { id: "delivery", selector: "[data-tour='compchem-delivery']", path: (e) => `/campaigns/${e.campaign_id}` },
  { id: "lead", selector: "[data-tour='compchem-lead']", path: (e) => `/campaigns/${e.campaign_id}` },
  { id: "lineage", selector: "[data-tour='compchem-lineage']", path: (_e, ctx) => ctx.leadMoleculeId ? `/molecules/${ctx.leadMoleculeId}` : null },
  { id: "sar", selector: "[data-tour='compchem-sar-scatter']", path: (e) => `/campaigns/${e.campaign_id}/sar` },
  { id: "audit", selector: "[data-tour='compchem-audit-integrity']", path: (e) => `/campaigns/${e.campaign_id}/audit` },
  { id: "methods-export", selector: "[data-tour='compchem-methods-export']", path: (e) => `/campaigns/${e.campaign_id}/methods-export` },
  { id: "run-detail", selector: "[data-tour='compchem-run-detail']", path: (_e, ctx) => ctx.leadRunId ? `/runs/${ctx.leadRunId}` : null },
  { id: "exports", selector: "[data-tour='compchem-exports']", path: (e) => `/campaigns/${e.campaign_id}` },
];

test("comp-chem tour walks every anchor", async ({ page, request }) => {
  const entry = await resetDemo(request, "compchem");
  await seedSession(page, entry);

  // Resolve lead molecule + lead docking run via the API so we can navigate
  // to the lineage and run-detail steps.
  const headers = { Authorization: `Demo ${entry.session_token}` };
  const campaign = await (await request.get(`/api/v1/campaigns/${entry.campaign_id}?org_id=${entry.org_id}`, { headers })).json();
  const leadMoleculeId = campaign.lead_molecule_id ? String(campaign.lead_molecule_id) : "";
  const runs = await (await request.get(`/api/v1/campaigns/${entry.campaign_id}/runs?org_id=${entry.org_id}`, { headers })).json();
  const lead = runs.find((r: any) => (r.molecule_external_id === "mol_001" || r.molecule_name === "AC-007") && r.run_kind === "docking") || runs[0];
  const leadRunId = lead?.id ? String(lead.id) : "";

  const ctx = { leadMoleculeId, leadRunId };
  const outDir = "screenshots/compchem";
  for (let i = 0; i < STEPS.length; i += 1) {
    await visitAndShoot(page, STEPS[i], entry, ctx, outDir, i);
  }
  expect(leadMoleculeId, "comp-chem campaign must have a lead molecule").not.toBe("");
  expect(leadRunId, "comp-chem campaign must have a docking run").not.toBe("");
});
