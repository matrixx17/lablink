// Wet lab dashboard API client.
//
// This dashboard is the bioprocess/wet lab vertical of LabLink. Comp-chem
// types and endpoints live on the comp-chem branch in a separate dashboard
// and are intentionally not duplicated here.

import { demoAuthHeaders, storeDemoSession } from "../lib/demoSession";

export type WetlabCampaign = {
  id: string;
  org_id: string;
  name: string;
  description?: string | null;
  domain: string;
  extra_params?: Record<string, unknown> | null;
  created_at?: string | null;
  batch_count: number;
  approvals: CampaignApproval[];
  is_approved: boolean;
  approval_count: number;
};

export type CampaignApproval = {
  id: string;
  campaign_id: string;
  approved_by_user_id: string;
  approved_by_name: string;
  approval_meaning: "author" | "reviewer" | "approver";
  comments?: string | null;
  created_at: string;
};

export type DemoEntry = {
  status: string;
  domain: "compchem" | "wetlab";
  org_id: string;
  campaign_id: string;
  redirect_url: string;
  session_token: string;
  session_expires_at: string;
};

export type DemoShare = {
  url: string;
  expires: string;
  qr_code: string;
  short_code?: string | null;
};

export type OrgInfo = {
  org_id: string;
  name?: string | null;
  demo_mode: boolean;
};

export type WetlabBatch = {
  id: string;
  campaign_id: string;
  batch_number?: string | null;
  bioreactor_model?: string | null;
  volume_liters?: number | null;
  cell_line?: string | null;
  media?: string | null;
  inoculation_date?: string | null;
  harvest_date?: string | null;
  status: string;
  extra_params?: Record<string, unknown> | null;
  // Present only when fetched via campaignBatchesWithMetrics (?include_metrics=true).
  summary_metrics?: WetlabBatchSummaryMetrics | null;
};

export type WetlabTimeseries = {
  id: string;
  batch_id: string;
  parameter_name: string;
  unit?: string | null;
  timestamps: number[];
  values: number[];
  source_instrument?: string | null;
  inoculation_unix?: number | null;
};

export type WetlabSample = {
  id: string;
  batch_id: string;
  sample_time_hours?: number | null;
  sample_time_absolute?: string | null;
  measurement_name: string;
  value?: number | null;
  unit?: string | null;
  instrument?: string | null;
  qc_status: string;
};

export type WetlabQcResult = {
  check_name: string;
  status: "pass" | "warn" | "fail";
  message: string;
  numeric_value?: number | null;
  timepoint_h?: number | null;
  parameter?: string | null;
};

export type WetlabAuditEvent = {
  id: number;
  timestamp?: string | null;
  org_id: string;
  action: string;
  entity_type: string;
  entity_id: string;
  actor: string;
  details?: Record<string, unknown> | null;
  previous_hash?: string | null;
  record_hash: string;
};

export type WetlabBatchSummaryMetrics = {
  peak_vcd?: number | null;
  final_titer?: number | null;
  min_viability?: number | null;
  run_duration_days?: number | null;
  lead_condition?: boolean;
};

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

function orgParam(orgId: string) {
  return `org_id=${encodeURIComponent(orgId)}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...demoAuthHeaders(),
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  org: (orgId: string) => request<OrgInfo>(`/api/v1/orgs/${encodeURIComponent(orgId)}?${orgParam(orgId)}`),
  resetAndEnterDemo: (domain: "compchem" | "wetlab") =>
    request<DemoEntry>(`/demo/reset-and-enter?domain=${encodeURIComponent(domain)}`, { method: "POST" })
      .then((entry) => {
        storeDemoSession({
          token: entry.session_token,
          domain: entry.domain,
          expiresAt: entry.session_expires_at,
        });
        return entry;
      }),
  shareDemo: (domain: "compchem" | "wetlab" | "both", label?: string) => {
    const params = new URLSearchParams({ domain });
    if (label) params.set("label", label);
    return request<DemoShare>(`/demo/share?${params.toString()}`);
  },
  recordDemoShareOpened: (shortCode: string) =>
    request<{ short_code: string; recorded: boolean }>(
      `/demo/share/${encodeURIComponent(shortCode)}/opened`,
      { method: "POST" }
    ),
  campaigns: (orgId: string) =>
    request<WetlabCampaign[]>(`/api/v1/wetlab/campaigns?${orgParam(orgId)}&domain=wetlab`),
  campaign: (id: string, orgId: string) =>
    request<WetlabCampaign>(`/api/v1/wetlab/campaigns/${id}?${orgParam(orgId)}`),
  campaignBatches: (id: string, orgId: string) =>
    request<WetlabBatch[]>(`/api/v1/wetlab/campaigns/${id}/batches?${orgParam(orgId)}`),
  batch: (id: string, orgId: string) =>
    request<WetlabBatch>(`/api/v1/batches/${id}?${orgParam(orgId)}`),
  batchTimeseries: (id: string, orgId: string) =>
    request<WetlabTimeseries[]>(`/api/v1/batches/${id}/timeseries?${orgParam(orgId)}`),
  batchSamples: (id: string, orgId: string) =>
    request<WetlabSample[]>(`/api/v1/batches/${id}/samples?${orgParam(orgId)}`),
  batchQc: (id: string, orgId: string, refresh = false) =>
    request<WetlabQcResult[]>(
      `/api/v1/batches/${id}/qc?${orgParam(orgId)}${refresh ? "&refresh=true" : ""}`,
    ),
  campaignBatchesWithMetrics: (id: string, orgId: string) =>
    request<(WetlabBatch & { summary_metrics?: WetlabBatchSummaryMetrics })[]>(
      `/api/v1/wetlab/campaigns/${id}/batches?${orgParam(orgId)}&include_metrics=true`,
    ),
  campaignMethods: (id: string, orgId: string) =>
    request<{
      campaign_id: string;
      campaign_name: string;
      generated_at: string;
      domain?: string;
      paragraphs: Record<string, string>;
      full_text: string;
      missing_fields: string[];
      software_versions: Record<string, string[]>;
      run_counts: Record<string, number>;
    }>(`/api/v1/wetlab/campaigns/${id}/methods?${orgParam(orgId)}`),
  auditLogs: (orgId: string) =>
    request<WetlabAuditEvent[]>(`/api/v1/audit?${orgParam(orgId)}&limit=1000`),
  approveCampaign: (
    id: string,
    orgId: string,
    body: { approval_meaning: "author" | "reviewer" | "approver"; comments?: string },
  ) =>
    request<CampaignApproval>(`/api/v1/wetlab/campaigns/${id}/approve?${orgParam(orgId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};
