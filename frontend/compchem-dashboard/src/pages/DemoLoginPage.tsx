import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import styles from "./pages.module.css";

const DEMO_EMAIL = "demo@lablink.io";
const DEMO_PASSWORD = "LabLinkDemo";

export default function DemoLoginPage() {
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  const enterDemo = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.demoLogin();
      navigate(`/campaigns?org=${encodeURIComponent(result.org_id)}`);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={styles.demoPage}>
      <section className={styles.demoLoginCard}>
        <div className={styles.demoMark}>LL</div>
        <p className={styles.demoEyebrow}>Public demo</p>
        <h1>Explore LabLink Comp-Chem</h1>
        <p className={styles.demoCopy}>
          Use a preloaded Demo Therapeutics workspace with docking grids, molecules,
          QC results, audit history, SAR charts, and methods generation.
        </p>

        <label>
          Email
          <input value={DEMO_EMAIL} readOnly />
        </label>
        <label>
          Password
          <input value={DEMO_PASSWORD} readOnly type="text" />
        </label>

        {error ? <div className={styles.demoError}>{error instanceof Error ? error.message : String(error)}</div> : null}

        <button type="button" className={styles.primaryButton} onClick={enterDemo} disabled={loading}>
          {loading ? "Loading demo..." : "Enter Demo"}
        </button>
        <p className={styles.demoFootnote}>
          No signup required. Demo data may reset at any time.
        </p>
      </section>
    </div>
  );
}
