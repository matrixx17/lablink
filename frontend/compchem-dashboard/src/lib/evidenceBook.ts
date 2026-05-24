import { demoAuthHeaders } from "./demoSession";

type DownloadResult = {
  filename: string;
  fileCount: number;
};

function countZipEntries(bytes: Uint8Array): number {
  let count = 0;
  for (let i = 0; i < bytes.length - 3; i++) {
    if (
      bytes[i] === 0x50 &&
      bytes[i + 1] === 0x4b &&
      bytes[i + 2] === 0x03 &&
      bytes[i + 3] === 0x04
    ) {
      count += 1;
    }
  }
  return count;
}

export async function downloadEvidenceBook(
  campaignId: string,
  orgId: string,
): Promise<DownloadResult> {
  const url =
    `/api/v1/campaigns/${encodeURIComponent(campaignId)}` +
    `/export/evidence-book` +
    `?org_id=${encodeURIComponent(orgId)}`;

  const response = await fetch(url, { method: "GET", headers: demoAuthHeaders() });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`Export failed (${response.status}): ${text || response.statusText}`);
  }

  const blob = await response.blob();
  const fileCount = countZipEntries(new Uint8Array(await blob.arrayBuffer()));
  const contentDisposition = response.headers.get("Content-Disposition") || "";
  const match = /filename="([^"]+)"/.exec(contentDisposition);
  const filename = match?.[1] ?? `evidence-book-${campaignId}.zip`;

  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0);

  return { filename, fileCount };
}
