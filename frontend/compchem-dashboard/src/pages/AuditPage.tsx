import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, AuditEvent, VerifyResult } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import { Card, EmptyState, ErrorBox, fmtDate, PageHeader, StatusBadge } from "../components/ui";
import styles from "./pages.module.css";

export default function AuditPage() {
  const { campaignId = "" } = useParams();
  const { orgId } = useOrgId();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.audit(campaignId, orgId).then(setEvents).catch(setError);
  }, [campaignId, orgId]);

  const verifyChain = () => {
    api.verifyAudit(campaignId, orgId).then(setVerify).catch(setError);
  };

  if (error) return <ErrorBox error={error} />;

  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow="Hash-chain audit"
        title={`Campaign ${campaignId} audit log`}
        actions={
          <>
            <button className={styles.button} onClick={verifyChain}>Verify integrity</button>
            <Link className={styles.secondaryButton} to={withOrg(`/campaigns/${campaignId}`, orgId)}>Back</Link>
          </>
        }
      />
      {verify ? (
        <Card>
          <StatusBadge status={verify.status} />
          <p>{verify.valid ? "Audit chain verified." : "Audit chain failed verification."}</p>
          <pre className={styles.json}>{JSON.stringify(verify, null, 2)}</pre>
        </Card>
      ) : null}
      <Card>
        {events.length === 0 ? <EmptyState>No campaign audit events found.</EmptyState> : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Action</th>
                  <th>Entity</th>
                  <th>Actor</th>
                  <th>Hash</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.id}>
                    <td>{fmtDate(event.timestamp)}</td>
                    <td>{event.action}</td>
                    <td>{event.entity_type}:{event.entity_id}</td>
                    <td>{event.actor || "-"}</td>
                    <td><span className={styles.muted}>{event.record_hash?.slice(0, 24)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
