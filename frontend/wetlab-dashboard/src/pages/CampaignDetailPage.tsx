import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, WetlabBatch, WetlabCampaign } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import {
  Card,
  EmptyState,
  ErrorBox,
  fmtDate,
  fmtNumber,
  PageHeader,
  StatusBadge,
} from "../components/ui";
import styles from "./pages.module.css";

export default function CampaignDetailPage() {
  const { id = "" } = useParams();
  const { orgId } = useOrgId();
  const [campaign, setCampaign] = useState<WetlabCampaign | null>(null);
  const [batches, setBatches] = useState<WetlabBatch[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setCampaign(null);
    setBatches(null);
    setError(null);
    Promise.all([
      api.campaign(id, orgId),
      api.campaignBatches(id, orgId),
    ])
      .then(([c, b]) => {
        setCampaign(c);
        setBatches(b);
      })
      .catch(setError);
  }, [id, orgId]);

  if (error) return <ErrorBox error={error} />;
  if (!campaign || !batches) {
    return <EmptyState>Loading campaign...</EmptyState>;
  }

  const extra = (campaign.extra_params || {}) as {
    target?: string;
    process_type?: string;
    status?: string;
    cro_partner?: string;
    delivery_date?: string;
  };

  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow={extra.target || "Bioprocess campaign"}
        title={campaign.name}
        actions={
          <Link
            className={styles.secondaryButton}
            to={withOrg(`/campaigns/${campaign.id}/compare`, orgId)}
          >
            Batch Comparison →
          </Link>
        }
      />

      <div className={styles.stats}>
        <Card><StatusBadge status={extra.status || "active"} /></Card>
        <Card>Process: {extra.process_type || "-"}</Card>
        <Card>CRO: {extra.cro_partner || "-"}</Card>
        <Card>Delivered: {fmtDate(extra.delivery_date)}</Card>
        <Card>Batches: {campaign.batch_count}</Card>
      </div>

      {campaign.description ? (
        <Card>
          <h2>About this campaign</h2>
          <p>{campaign.description}</p>
        </Card>
      ) : null}

      <Card>
        <h2>Batches</h2>
        {batches.length === 0 ? (
          <EmptyState>No batches recorded for this campaign.</EmptyState>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Batch</th>
                  <th>Condition</th>
                  <th>Bioreactor</th>
                  <th>Volume</th>
                  <th>Inoculated</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {batches.map((b) => {
                  const ep = (b.extra_params || {}) as { condition_label?: string };
                  return (
                    <tr key={b.id}>
                      <td>
                        <Link
                          className={styles.link}
                          to={withOrg(
                            `/campaigns/${campaign.id}/batches/${b.id}/timeline`,
                            orgId
                          )}
                        >
                          {b.batch_number || b.id.slice(0, 8)}
                        </Link>
                      </td>
                      <td>{ep.condition_label || "-"}</td>
                      <td>{b.bioreactor_model || "-"}</td>
                      <td>
                        {b.volume_liters ? `${fmtNumber(b.volume_liters, 2)} L` : "-"}
                      </td>
                      <td>{fmtDate(b.inoculation_date)}</td>
                      <td><StatusBadge status={b.status} /></td>
                      <td>
                        <Link
                          className={styles.secondaryButton}
                          to={withOrg(
                            `/campaigns/${campaign.id}/batches/${b.id}/timeline`,
                            orgId
                          )}
                        >
                          Timeline →
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
