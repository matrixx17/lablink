import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api, WetlabAuditEvent, WetlabCampaign } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import {
  ActionBar,
  DataTable,
  EmptyState,
  ErrorBox,
  fmtDate,
  HeroHeader,
  KpiStrip,
  SecondaryButton,
  SectionRule,
  StatusBadge,
} from "../components/ui";
import styles from "./pages.module.css";

function humanize(value: string) {
  return value.replace(/_/g, " ");
}

function detailValue(event: WetlabAuditEvent, key: string) {
  const value = event.details?.[key];
  return typeof value === "string" ? value : "";
}

function description(event: WetlabAuditEvent) {
  return (
    detailValue(event, "message") ||
    detailValue(event, "event") ||
    `${humanize(event.action)} recorded for ${event.entity_type} ${event.entity_id}`
  );
}

function eventBadgeClass(event: WetlabAuditEvent) {
  const eventName = detailValue(event, "event");
  if (eventName === "qc_acknowledgment") return styles.auditBadgeGold;
  if (eventName === "lead_nominated") return styles.auditBadgeGreen;
  if (eventName === "cro_delivery") return styles.auditBadgeBlue;
  if (event.action.includes("approved")) return styles.auditBadgeGreen;
  return styles.auditBadgeNeutral;
}

function hashShort(value?: string | null) {
  return value ? `${value.slice(0, 10)}…${value.slice(-8)}` : "—";
}

export default function AuditPage() {
  const { campaignId = "" } = useParams();
  const { orgId } = useOrgId();
  const [campaign, setCampaign] = useState<WetlabCampaign | null>(null);
  const [events, setEvents] = useState<WetlabAuditEvent[]>([]);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    Promise.all([api.campaign(campaignId, orgId), api.auditLogs(orgId)])
      .then(([campaignResponse, auditResponse]) => {
        setCampaign(campaignResponse);
        setEvents(auditResponse);
      })
      .catch(setError);
  }, [campaignId, orgId]);

  const campaignEvents = useMemo(() => {
    return events
      .filter((event) => {
        const detailCampaign = detailValue(event, "campaign_id");
        return event.entity_id === campaignId || detailCampaign === campaignId;
      })
      .sort((a, b) => {
        const timeDiff =
          new Date(a.timestamp || 0).getTime() - new Date(b.timestamp || 0).getTime();
        return timeDiff || a.id - b.id;
      });
  }, [campaignId, events]);

  const acknowledgment = campaignEvents.find((event) => detailValue(event, "event") === "qc_acknowledgment");
  const firstEvent = campaignEvents[0];
  const lastEvent = campaignEvents[campaignEvents.length - 1];
  const actorCount = new Set(campaignEvents.map((event) => event.actor).filter(Boolean)).size;

  if (error) return <ErrorBox error={error} />;
  if (!campaign && events.length === 0) {
    return <div className={styles.centerMessage}>Loading audit trail…</div>;
  }

  return (
    <div className={styles.grid}>
      <HeroHeader
        eyebrow={campaign?.name || `Campaign ${campaignId}`}
        title="Audit trail."
        context={
          <p>
            Wet lab delivery, QC review, lead-condition nomination, and approvals are recorded in the same permanent hash chain used for batch-record export.
          </p>
        }
        status={<StatusBadge status="Chain integrity recorded" />}
        actions={
          <ActionBar>
            <SecondaryButton as="a" href={withOrg(`/campaigns/${campaignId}`, orgId)}>
              Back to campaign
            </SecondaryButton>
          </ActionBar>
        }
      />

      <KpiStrip
        items={[
          { label: "Events", value: campaignEvents.length },
          { label: "Human sign-off", value: acknowledgment ? "Recorded" : "Pending", tone: acknowledgment ? "good" : "warn" },
          { label: "Actors", value: actorCount || "—" },
          { label: "Tip hash", value: hashShort(lastEvent?.record_hash) },
        ]}
      />

      <SectionRule eyebrow="Hash chain" title="Campaign anchors" />
      <div className={styles.hashGrid}>
        <div>
          <span>Root hash</span>
          <code>{firstEvent?.record_hash || "—"}</code>
        </div>
        <div>
          <span>Tip hash</span>
          <code>{lastEvent?.record_hash || "—"}</code>
        </div>
        <div>
          <span>Last recorded</span>
          <code>{fmtDate(lastEvent?.timestamp)}</code>
        </div>
      </div>

      <SectionRule eyebrow="Log" title={`Audit events (${campaignEvents.length})`} />
      {campaignEvents.length === 0 ? (
        <EmptyState>No campaign audit events found.</EmptyState>
      ) : (
        <DataTable>
          <thead>
            <tr>
              <th>#</th>
              <th>Timestamp</th>
              <th>Event</th>
              <th>Actor</th>
              <th>Description</th>
              <th>Hash</th>
            </tr>
          </thead>
          <tbody>
            {campaignEvents.map((event, index) => {
              const isAcknowledgment = detailValue(event, "event") === "qc_acknowledgment";
              return (
                <tr key={event.id} data-tour={isAcknowledgment ? "wetlab-audit-ack" : undefined}>
                  <td className="num">{index + 1}</td>
                  <td className="num">{fmtDate(event.timestamp)}</td>
                  <td>
                    <span className={`${styles.auditEventBadge} ${eventBadgeClass(event)}`}>
                      {humanize(detailValue(event, "event") || event.action)}
                    </span>
                  </td>
                  <td>{event.actor || "—"}</td>
                  <td>{description(event)}</td>
                  <td className={styles.hashShort}>{hashShort(event.record_hash)}</td>
                </tr>
              );
            })}
          </tbody>
        </DataTable>
      )}
    </div>
  );
}
