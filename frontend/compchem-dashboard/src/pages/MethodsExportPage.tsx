import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, MethodsExport } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import { Card, EmptyState, ErrorBox, fmtDate, PageHeader } from "../components/ui";
import styles from "./pages.module.css";

function safeFilename(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "") || "campaign";
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export default function MethodsExportPage() {
  const { id = "" } = useParams();
  const { orgId } = useOrgId();
  const [methods, setMethods] = useState<MethodsExport | null>(null);
  const [text, setText] = useState("");
  const [isEditing, setIsEditing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.campaignMethods(id, orgId)
      .then((response) => {
        setMethods(response);
        setText(response.full_text || "");
      })
      .catch(setError);
  }, [id, orgId]);

  const filename = useMemo(() => {
    const date = new Date().toISOString().slice(0, 10);
    return `${safeFilename(methods?.campaign_name || `campaign_${id}`)}_methods_${date}.txt`;
  }, [id, methods?.campaign_name]);

  const copyText = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  if (error) return <ErrorBox error={error} />;
  if (!methods) return <EmptyState>Loading generated methods...</EmptyState>;

  const softwareRows = Object.entries(methods.software_versions || {});

  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow={methods.campaign_name}
        title="Auto-Generated Methods Section"
        actions={<Link className={styles.secondaryButton} to={withOrg(`/campaigns/${id}`, orgId)}>Back to campaign</Link>}
      />
      <p className={styles.methodsSubtitle}>Ready to paste into your manuscript or IND filing</p>

      <Card className={styles.methodsEditorCard}>
        <div className={styles.methodsActions}>
          <button type="button" className={styles.button} onClick={copyText}>
            {copied ? "Copied!" : "Copy to clipboard"}
          </button>
          <button type="button" className={styles.secondaryButton} onClick={() => downloadText(filename, text)}>
            Download .txt
          </button>
          <button
            type="button"
            className={`${styles.secondaryButton} ${isEditing ? styles.toggleActive : ""}`}
            onClick={() => setIsEditing((current) => !current)}
          >
            {isEditing ? "Done editing" : "Edit"}
          </button>
        </div>
        <textarea
          className={`${styles.methodsTextarea} ${isEditing ? styles.methodsTextareaEditing : ""}`}
          value={text}
          readOnly={!isEditing}
          onChange={(event) => setText(event.target.value)}
          spellCheck={false}
        />
      </Card>

      {methods.missing_fields.length > 0 ? (
        <div className={styles.methodsWarning}>
          <strong>
            The following fields were not recorded and appear as [not recorded] in the text above.
            Add them in your run configurations to auto-fill next time:
          </strong>
          <ul>
            {methods.missing_fields.map((field) => <li key={field}>{field}</li>)}
          </ul>
        </div>
      ) : null}

      <Card>
        <h2>Software Versions Used</h2>
        {softwareRows.length === 0 ? <EmptyState>No software versions recorded.</EmptyState> : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Software</th>
                  <th>Versions Detected</th>
                </tr>
              </thead>
              <tbody>
                {softwareRows.map(([software, versions]) => {
                  const hasMultipleVersions = versions.length > 1;
                  return (
                    <tr className={hasMultipleVersions ? styles.versionWarningRow : ""} key={software}>
                      <td>{software}</td>
                      <td>
                        {versions.join(", ")}
                        {hasMultipleVersions ? (
                          <span className={styles.versionWarningNote}>
                            Multiple versions detected — verify consistency.
                          </span>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className={styles.muted}>Generated {fmtDate(methods.generated_at)}</p>
      </Card>
    </div>
  );
}
