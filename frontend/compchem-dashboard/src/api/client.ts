export type Campaign = {
  id: number;
  org_id: string;
  project_id: number;
  project_name: string;
  name: string;
  description?: string | null;
  campaign_type: string;
  status: string;
  target_metric?: string | null;
  target_metric_unit?: string | null;
  target_metric_threshold?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  run_count: number;
  molecule_count: number;
};

export type CampaignRun = {
  id: number;
  molecule_id?: number | null;
  molecule_name?: string | null;
  molecule_external_id?: string | null;
  run_kind: string;
  status: string;
  qc_status?: string | null;
  software_name?: string | null;
  software_version?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  wall_time_s?: number | null;
  metric_count: number;
};

export type MoleculeListItem = {
  id: number;
  inchi_key: string;
  canonical_smiles: string;
  name?: string | null;
  external_id?: string | null;
  molecular_weight?: number | null;
  formula?: string | null;
  run_count: number;
  top_metrics: Array<{ metric_name: string; best_value: number; unit: string; run_id: number }>;
};

export type MoleculeDetail = MoleculeListItem & {
  campaign_id: number;
  inchi?: string | null;
  properties: Record<string, { value?: number; unit?: string; source?: string }>;
  runs: Array<{
    id: number;
    run_kind: string;
    status: string;
    software_name?: string | null;
    software_version?: string | null;
    started_at?: string | null;
    completed_at?: string | null;
    wall_time_s?: number | null;
    metric_count: number;
  }>;
  assay_results: Array<{
    id: number;
    metric_name: string;
    value: number;
    unit: string;
    passes_threshold?: boolean | null;
    run_metric_id: number;
    created_at?: string | null;
  }>;
};

export type RunDetail = {
  id: number;
  campaign_id: number;
  molecule_id?: number | null;
  external_run_id?: string | null;
  name?: string | null;
  run_kind: string;
  status: string;
  software_name?: string | null;
  software_version?: string | null;
  forcefield?: string | null;
  config_hash?: string | null;
  cli_args?: string | null;
  compute_environment?: string | null;
  compute_details?: Record<string, unknown> | null;
  started_at?: string | null;
  completed_at?: string | null;
  wall_time_s?: number | null;
  error_message?: string | null;
  inputs: Artifact[];
  outputs: Artifact[];
  metrics: Array<{
    id: number;
    name: string;
    value: number;
    unit: string;
    confidence?: number | null;
    stderr?: number | null;
    metadata?: Record<string, unknown> | null;
  }>;
  qc?: Record<string, unknown> | null;
  client_qc?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
  audit_events: AuditEvent[];
};

export type Artifact = {
  id: number;
  input_kind?: string;
  output_kind?: string;
  filename: string;
  s3_key?: string | null;
  file_hash?: string | null;
  file_size_bytes?: number | null;
};

export type SarResponse = {
  metric_names: string[];
  points: SarPoint[];
};

export type SarPoint = {
  molecule_id: number;
  molecule_name?: string | null;
  molecule_external_id?: string | null;
  canonical_smiles: string;
  run_id: number;
  run_status: string;
  qc_status?: string | null;
  x: number;
  y: number;
  x_metric: string;
  y_metric: string;
  x_unit: string;
  y_unit: string;
};

export type AuditEvent = {
  id: number;
  timestamp?: string | null;
  action: string;
  entity_type?: string;
  entity_id?: string;
  actor?: string;
  details?: Record<string, unknown> | null;
  previous_hash?: string | null;
  record_hash?: string;
};

export type VerifyResult = {
  valid: boolean;
  status: string;
  record_count?: number;
  campaign_event_count?: number;
  errors?: Array<Record<string, unknown>>;
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
  campaigns: (orgId: string) => request<Campaign[]>(`/api/v1/campaigns?${orgParam(orgId)}`),
  campaign: (id: string | number, orgId: string) =>
    request<Campaign>(`/api/v1/campaigns/${id}?${orgParam(orgId)}`),
  campaignRuns: (id: string | number, orgId: string) =>
    request<CampaignRun[]>(`/api/v1/campaigns/${id}/runs?${orgParam(orgId)}`),
  campaignMolecules: (id: string | number, orgId: string) =>
    request<MoleculeListItem[]>(`/api/v1/campaigns/${id}/molecules?${orgParam(orgId)}`),
  campaignSar: (id: string | number, orgId: string, x?: string, y?: string) => {
    const params = new URLSearchParams({ org_id: orgId });
    if (x) params.set("x_metric", x);
    if (y) params.set("y_metric", y);
    return request<SarResponse>(`/api/v1/campaigns/${id}/sar?${params.toString()}`);
  },
  molecule: (id: string | number, orgId: string) =>
    request<MoleculeDetail>(`/api/v1/molecules/${id}?${orgParam(orgId)}`),
  run: (id: string | number, orgId: string) =>
    request<RunDetail>(`/api/v1/runs/${id}?${orgParam(orgId)}`),
  audit: (campaignId: string | number, orgId: string) =>
    request<AuditEvent[]>(`/api/v1/campaigns/${campaignId}/audit?${orgParam(orgId)}`),
  verifyAudit: (campaignId: string | number, orgId: string) =>
    request<VerifyResult>(`/api/v1/audit/verify/${campaignId}?${orgParam(orgId)}`, { method: "POST" }),
  artifactDownload: (kind: "input" | "output", id: number, orgId: string) =>
    request<{ url: string; filename: string; expires_in_seconds: number }>(
      `/api/v1/artifacts/${kind}/${id}/download?${orgParam(orgId)}`
    ),
  moleculeSvgUrl: (id: string | number, orgId: string) =>
    `${API_BASE}/api/v1/molecules/${id}/structure.svg?${orgParam(orgId)}`
};
