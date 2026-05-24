import { demoAuthHeaders } from "./demoSession";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

function orgParam(orgId: string) {
  return `org_id=${encodeURIComponent(orgId)}`;
}

function filenameFromContentDisposition(header: string | null) {
  if (!header) return null;
  const match = header.match(/filename="([^"]+)"/);
  return match?.[1] || null;
}

export async function downloadBcoExport(campaignId: string | number, orgId: string) {
  const response = await fetch(
    `${API_BASE}/api/v1/campaigns/${campaignId}/export/bco?${orgParam(orgId)}&download=true`,
    { headers: demoAuthHeaders() },
  );
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }

  const blob = await response.blob();
  const filename =
    filenameFromContentDisposition(response.headers.get("content-disposition")) ||
    `campaign_${campaignId}_BCO.json`;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
