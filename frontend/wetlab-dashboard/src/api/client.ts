// Wet lab dashboard API client.
//
// This dashboard is the bioprocess/wet lab vertical of LabLink. Comp-chem
// types and endpoints live on the comp-chem branch in a separate dashboard
// and are intentionally not duplicated here.

export type WetlabCampaign = {
  id: string;
  org_id: string;
  name: string;
  description?: string | null;
  domain: string;
  extra_params?: Record<string, unknown> | null;
  created_at?: string | null;
  batch_count: number;
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
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  campaigns: (orgId: string) =>
    request<WetlabCampaign[]>(`/api/v1/campaigns?${orgParam(orgId)}&domain=wetlab`),
  campaign: (id: string, orgId: string) =>
    request<WetlabCampaign>(`/api/v1/campaigns/${id}?${orgParam(orgId)}`),
  campaignBatches: (id: string, orgId: string) =>
    request<WetlabBatch[]>(`/api/v1/campaigns/${id}/batches?${orgParam(orgId)}`),
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
      `/api/v1/campaigns/${id}/batches?${orgParam(orgId)}&include_metrics=true`,
    ),
  campaignMethods: (id: string, orgId: string) =>
    request<{
      campaign_id: string;
      generated_at: string;
      paragraphs: Record<string, string>;
      full_text: string;
      missing_fields: string[];
      instrument_summary: Record<string, unknown>;
    }>(`/api/v1/campaigns/${id}/methods?${orgParam(orgId)}`),
};
