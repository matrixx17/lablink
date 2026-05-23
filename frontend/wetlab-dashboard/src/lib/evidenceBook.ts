/**
 * Fetch the Evidence Book ZIP for a campaign and trigger a browser
 * download. Returns the response's Content-Disposition filename if the
 * server provided one, otherwise generates a sensible fallback.
 *
 * Done via fetch + blob + object URL (rather than a plain anchor) so we
 * can show a loading spinner on the button while the backend assembles
 * the package, and surface HTTP errors as a string instead of a noisy
 * browser-default navigation.
 */
export async function downloadEvidenceBook(
  campaignId: string,
  orgId: string,
): Promise<string> {
  const url = `/api/v1/campaigns/${encodeURIComponent(
    campaignId,
  )}/export/evidence-book?org_id=${encodeURIComponent(orgId)}`;

  const r = await fetch(url, { method: "GET" });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(
      `Evidence Book export failed (${r.status}): ${text || r.statusText}`,
    );
  }

  const blob = await r.blob();
  const cd = r.headers.get("Content-Disposition") || "";
  const match = /filename="([^"]+)"/.exec(cd);
  const filename = match?.[1] ?? `evidence-book-${campaignId}.zip`;

  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Browsers need a tick before revoking; defer.
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  return filename;
}
