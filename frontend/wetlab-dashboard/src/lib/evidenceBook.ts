/**
 * Download helpers for the campaign export endpoints.
 *
 * The server route is `GET /api/v1/campaigns/{id}/export/evidence-book`
 * with an optional `?format=` query that selects which artifact to bundle:
 *
 *   format=evidence-book  (default) — VDR-style provenance pack
 *   format=batch-record              — pharmaceutical Batch Manufacturing
 *                                     Record (wet lab only; 400 otherwise)
 *
 * Both downloads use fetch+blob so the calling button can show a loading
 * spinner and surface non-2xx errors as strings rather than a browser-
 * default navigation.
 */

async function downloadZip(
  campaignId: string,
  orgId: string,
  format: "evidence-book" | "batch-record",
  fallbackPrefix: string,
): Promise<string> {
  const url =
    `/api/v1/campaigns/${encodeURIComponent(campaignId)}` +
    `/export/evidence-book` +
    `?org_id=${encodeURIComponent(orgId)}` +
    `&format=${encodeURIComponent(format)}`;

  const r = await fetch(url, { method: "GET" });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`Export failed (${r.status}): ${text || r.statusText}`);
  }

  const blob = await r.blob();
  const cd = r.headers.get("Content-Disposition") || "";
  const match = /filename="([^"]+)"/.exec(cd);
  const filename = match?.[1] ?? `${fallbackPrefix}-${campaignId}.zip`;

  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  return filename;
}

export function downloadEvidenceBook(
  campaignId: string,
  orgId: string,
): Promise<string> {
  return downloadZip(campaignId, orgId, "evidence-book", "evidence-book");
}

export function downloadBatchRecord(
  campaignId: string,
  orgId: string,
): Promise<string> {
  return downloadZip(campaignId, orgId, "batch-record", "batch-record");
}
