import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api, AuditEvent, Campaign, VerifyResult } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import { ActionBar, Card, DataTable, EmptyState, ErrorBox, fmtDate, HeroHeader, KpiStrip, PrimaryButton, SecondaryButton, SectionRule } from "../components/ui";
import styles from "./pages.module.css";

function humanize(value: string) {
  return value.replace(/_/g, " ");
}

function eventDescription(event: AuditEvent) {
  const details = event.details || {};
  if (typeof details.message === "string") return details.message;
  if (typeof details.description === "string") return details.description;
  return `${humanize(event.action)} recorded for ${event.entity_type || "entity"} ${event.entity_id || ""}`.trim();
}

function eventBadgeClass(action: string) {
  if (action === "cro_delivery") return styles.auditBadgePurple;
  if (action === "run_complete") return styles.auditBadgeGreen;
  if (action === "lead_nominated") return styles.auditBadgeGold;
  if (action === "file_received") return styles.auditBadgeBlue;
  if (action === "qc_flagged") return styles.auditBadgeRed;
  return styles.auditBadgeNeutral;
}

function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export default function AuditPage() {
  const { campaignId = "" } = useParams();
  const { orgId } = useOrgId();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [verifiedAt, setVerifiedAt] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    Promise.all([
      api.campaign(campaignId, orgId),
      api.audit(campaignId, orgId),
      api.verifyAudit(campaignId, orgId)
    ])
      .then(([campaignResponse, auditResponse, verifyResponse]) => {
        setCampaign(campaignResponse);
        setEvents(auditResponse);
        setVerify(verifyResponse);
        setVerifiedAt(new Date().toISOString());
      })
      .catch(setError);
  }, [campaignId, orgId]);

  const chronologicalEvents = useMemo(() => {
    return events
      .slice()
      .sort((a, b) => {
        const timeDiff = new Date(a.timestamp || 0).getTime() - new Date(b.timestamp || 0).getTime();
        return timeDiff || a.id - b.id;
      });
  }, [events]);

  const actors = useMemo(() => {
    return Array.from(new Set(chronologicalEvents.map((event) => event.actor).filter(Boolean) as string[])).sort();
  }, [chronologicalEvents]);

  const firstEvent = chronologicalEvents[0];
  const lastEvent = chronologicalEvents[chronologicalEvents.length - 1];
  const integrityOk = verify?.valid === true;
  const verificationText = verify
    ? integrityOk
      ? `Hash chain intact — ${verify.campaign_event_count ?? chronologicalEvents.length} events verified`
      : `Hash chain compromised — ${verify.errors?.length || 1} issue${(verify.errors?.length || 1) === 1 ? "" : "s"} found`
    : "Verifying hash chain...";

  const exportJson = () => {
    downloadJson(`campaign-${campaignId}-audit-trail.json`, {
      campaign,
      org_id: orgId,
      exported_at: new Date().toISOString(),
      verification: verify,
      audit_events: chronologicalEvents
    });
  };

  if (error) return <ErrorBox error={error} />;
  if (!campaign && !verify && events.length === 0) return <EmptyState>Loading audit trail...</EmptyState>;

  return (
    <div className={`${styles.grid} ${styles.reveal} ${styles.auditReport}`}>
      <HeroHeader
        eyebrow={campaign?.name || `Campaign ${campaignId}`}
        title="Audit trail"
        context={<p>Tamper-evident delivery and computation record suitable for regulatory review.</p>}
        status={
          <span className={`${styles.auditIntegrityBadge} ${integrityOk ? styles.auditIntegrityPass : styles.auditIntegrityFail}`}>
            {integrityOk ? "✓ Chain integrity verified" : "✗ Chain compromised"}
          </span>
        }
        actions={
          <ActionBar>
            <SecondaryButton onClick={() => window.print()}>Export PDF</SecondaryButton>
            <PrimaryButton onClick={exportJson}>Export JSON</PrimaryButton>
            <SecondaryButton as="a" href={withOrg(`/campaigns/${campaignId}`, orgId)}>Back</SecondaryButton>
          </ActionBar>
        }
      />

      <KpiStrip
        items={[
          { label: "Total events", value: chronologicalEvents.length },
          { label: "Date range", value: firstEvent && lastEvent ? `${fmtDate(firstEvent.timestamp)} → ${fmtDate(lastEvent.timestamp)}` : "—" },
          { label: "Verification", value: verificationText, tone: integrityOk ? "good" : "bad" },
          { label: "Unique actors", value: actors.length || "—" },
        ]}
      />

      <Card>
        <SectionRule title="Audit log" />
        {events.length === 0 ? <EmptyState>No campaign audit events found.</EmptyState> : (
          <DataTable>
            <thead>
              <tr>
                <th>#</th>
                <th>Timestamp</th>
                <th>Event type</th>
                <th>Actor</th>
                <th>Description</th>
                <th>Hash</th>
              </tr>
            </thead>
            <tbody>
              {chronologicalEvents.map((event, index) => (
                <tr key={event.id}>
                  <td>{index + 1}</td>
                  <td>{fmtDate(event.timestamp)}</td>
                  <td>
                    <span className={`${styles.auditEventBadge} ${eventBadgeClass(event.action)}`}>
                      {humanize(event.action)}
                    </span>
                  </td>
                  <td>{event.actor || "—"}</td>
                  <td>{eventDescription(event)}</td>
                  <td><code className={styles.hashShort}>{event.record_hash?.slice(0, 8) || "—"}</code></td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        )}
      </Card>

      <Card className={styles.verificationCard}>
        <SectionRule title="Verification" />
        <p>
          This audit trail uses SHA-256 hash chaining. Each event's hash is
          computed from its content plus the previous event's hash, forming a
          tamper-evident chain. Deleting or modifying any event breaks all
          subsequent hashes.
        </p>
        <div className={styles.hashGrid}>
          <div>
            <span>First hash</span>
            <code>{firstEvent?.record_hash || "-"}</code>
          </div>
          <div>
            <span>Last hash</span>
            <code>{lastEvent?.record_hash || "-"}</code>
          </div>
        </div>
        <div className={`${styles.verificationResult} ${integrityOk ? styles.auditIntegrityPass : styles.auditIntegrityFail}`}>
          <strong>{integrityOk ? "Verified" : "Failed"}</strong>
          <span>{verificationText}</span>
          <span>Checked at {fmtDate(verifiedAt)}</span>
        </div>
      </Card>
    </div>
  );
}
