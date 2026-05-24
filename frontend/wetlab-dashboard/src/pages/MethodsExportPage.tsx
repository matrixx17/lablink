import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import {
  ActionBar,
  ErrorBox,
  HeroHeader,
  PrimaryButton,
  SecondaryButton,
  SectionRule,
  StatusBadge,
} from "../components/ui";
import styles from "./pages.module.css";

type MethodsResponse = {
  campaign_id: string;
  campaign_name: string;
  generated_at: string;
  domain?: string;
  paragraphs: Record<string, string>;
  full_text: string;
  missing_fields: string[];
  software_versions: Record<string, string[]>;
  run_counts: Record<string, number>;
};

function safeFilename(s: string) {
  return s
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "") || "campaign";
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export default function MethodsExportPage() {
  const { campaignId = "" } = useParams();
  const { orgId } = useOrgId();
  const [methods, setMethods] = useState<MethodsResponse | null>(null);
  const [campaignName, setCampaignName] = useState<string>("");
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    Promise.all([
      api.campaign(campaignId, orgId),
      api.campaignMethods(campaignId, orgId),
    ])
      .then(([camp, m]) => {
        setCampaignName(camp.name);
        setMethods(m as MethodsResponse);
      })
      .catch(setError);
  }, [campaignId, orgId]);

  const filename = useMemo(() => {
    const date = new Date().toISOString().slice(0, 10);
    return `${safeFilename(campaignName || `campaign_${campaignId}`)}_methods_${date}.txt`;
  }, [campaignId, campaignName]);

  const copy = async () => {
    if (!methods) return;
    try {
      await navigator.clipboard.writeText(methods.full_text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard access denied — fall back to no-op; user can still download */
    }
  };

  if (error) return <ErrorBox error={error} />;
  if (!methods) return <div className={styles.centerMessage}>Generating methods…</div>;

  const orderedParagraphs: Array<[string, string]> = [
    ["Bioreactor", methods.paragraphs.bioreactor || ""],
    ["Cell analysis", methods.paragraphs.cell_analysis || ""],
    ["Titer", methods.paragraphs.titer || ""],
    ["Metabolites", methods.paragraphs.metabolites || ""],
    ["Chromatography", methods.paragraphs.chromatography || ""],
  ];
  const equipmentRows = Object.entries(methods.software_versions || {});

  return (
    <div className={styles.grid}>
      <HeroHeader
        eyebrow="Methods export"
        title={
          <span className={styles.methodsTitleRow}>
            <span>{campaignName || "Campaign"} — methods.</span>
            <span className={`${styles.domainBadge} ${styles.domainBadgeWetlab}`}>WET LAB</span>
          </span>
        }
        context={
          <p>
            Publication-ready methods, auto-generated from this campaign's
            recorded instruments, setpoints, and offline analytics. Paragraphs
            with <code>[not recorded]</code> placeholders identify metadata you
            need to capture before submission.
          </p>
        }
        status={<StatusBadge status="Wet Lab Campaign" />}
        actions={
          <ActionBar>
            <PrimaryButton onClick={copy}>
              {copied ? "Copied ✓" : "Copy methods text"}
            </PrimaryButton>
            <SecondaryButton onClick={() => downloadText(filename, methods.full_text)}>
              Download as .txt
            </SecondaryButton>
            <SecondaryButton as="a" href={withOrg(`/campaigns/${campaignId}`, orgId)}>
              Back to campaign
            </SecondaryButton>
          </ActionBar>
        }
      />

      {methods.missing_fields.length > 0 ? (
        <div className={styles.warningBanner}>
          <strong>Missing metadata.</strong> The following fields rendered as
          placeholders: <code className="num">{methods.missing_fields.join(", ")}</code>.
          Capture them in batch records or instrument exports and regenerate.
        </div>
      ) : null}

      <div data-tour="wetlab-methods-export">
        {orderedParagraphs
          .filter(([, body]) => body && body.trim().length > 0)
          .map(([title, body]) => (
            <section key={title} style={{ marginBottom: 24 }}>
              <SectionRule eyebrow="Paragraph" title={title} />
              <p style={{ maxWidth: "76ch", color: "var(--ink)" }}>{body}</p>
            </section>
          ))}
      </div>

      {equipmentRows.length > 0 ? (
        <>
          <SectionRule eyebrow="Instruments" title="Instruments and equipment" />
          <pre>{JSON.stringify(methods.software_versions, null, 2)}</pre>
        </>
      ) : null}

      <SectionRule eyebrow="Plain text" title="Combined methods" />
      <pre>{methods.full_text || "—"}</pre>
    </div>
  );
}
