import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import styles from "./pages.module.css";

type DemoDomain = "compchem" | "wetlab";
type DemoShareDomain = DemoDomain | "both";

const COPY_MESSAGE = "Link copied — anyone with this link can explore the demo.";

async function copyText(value: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textArea = document.createElement("textarea");
  textArea.value = value;
  textArea.setAttribute("readonly", "true");
  textArea.style.position = "fixed";
  textArea.style.opacity = "0";
  document.body.appendChild(textArea);
  textArea.select();
  document.execCommand("copy");
  document.body.removeChild(textArea);
}

const DEMOS: Array<{
  domain: DemoDomain;
  title: string;
  kicker: string;
  description: string;
  action: string;
}> = [
  {
    domain: "compchem",
    title: "Computational Chemistry Demo",
    kicker: "AC-007 lead story",
    description:
      "Follow AC-007, a lead EGFR inhibitor, from virtual screening through MD simulation and DFT with a complete audit trail and one-click FDA-ready export.",
    action: "Enter Comp Chem Demo",
  },
  {
    domain: "wetlab",
    title: "Wet Lab / Bioprocess Demo",
    kicker: "CHO fed-batch campaign",
    description:
      "Follow a CHO cell fed-batch campaign across three process conditions from CRO delivery through batch comparison and lead condition selection.",
    action: "Enter Wet Lab Demo",
  },
];

export default function DemoLandingPage() {
  const [params] = useSearchParams();
  const [loadingDomain, setLoadingDomain] = useState<DemoDomain | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);

  const sharedDomain = params.get("ref") === "shared" ? params.get("domain") : null;
  const shareCode = params.get("ref") === "shared" ? params.get("code") : null;
  const shareDomain: DemoShareDomain =
    sharedDomain === "compchem" || sharedDomain === "wetlab" || sharedDomain === "both"
      ? sharedDomain
      : "both";

  const enterDemo = async (domain: DemoDomain) => {
    setLoadingDomain(domain);
    setError(null);
    try {
      const result = await api.resetAndEnterDemo(domain);
      window.location.assign(result.redirect_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setLoadingDomain(null);
    }
  };

  useEffect(() => {
    if (shareCode) {
      api.recordDemoShareOpened(shareCode).catch(() => undefined);
    }
    if (sharedDomain === "compchem" || sharedDomain === "wetlab") {
      void enterDemo(sharedDomain);
    }
    // Shared links for domain=both intentionally leave the selector visible.
  }, []);

  const copyShareLink = async () => {
    setError(null);
    try {
      const result = await api.shareDemo(shareDomain);
      await copyText(result.url);
      setCopyMessage(COPY_MESSAGE);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setCopyMessage(null);
    }
  };

  return (
    <main className={styles.demoLandingPage}>
      <section className={styles.demoLandingShell} aria-labelledby="demo-title">
        <div className={styles.demoLandingBrand}>LabLink</div>
        <p className={styles.demoLandingTagline}>The provenance engine for drug discovery</p>
        <h1 id="demo-title">Explore a live demo</h1>
        <p className={styles.demoLandingSubtitle}>Explore a live demo - no signup required</p>
        <button type="button" className={styles.demoShareButton} onClick={copyShareLink}>
          Share this demo
        </button>
        {copyMessage ? <p className={styles.demoShareCopy}>{copyMessage}</p> : null}

        <div className={styles.demoChoiceGrid}>
          {DEMOS.map((demo) => (
            <article className={styles.demoChoiceCard} key={demo.domain}>
              <p className={styles.demoChoiceKicker}>{demo.kicker}</p>
              <h2>{demo.title}</h2>
              <p>{demo.description}</p>
              <button
                type="button"
                className={styles.demoChoiceButton}
                onClick={() => enterDemo(demo.domain)}
                disabled={loadingDomain !== null}
              >
                {loadingDomain === demo.domain ? "Preparing demo..." : `${demo.action} ->`}
              </button>
            </article>
          ))}
        </div>

        {error ? <div className={styles.demoError}>{error}</div> : null}

        <p className={styles.demoLandingFootnote}>
          Data resets every 30 minutes. Nothing you do here is saved permanently.
        </p>
      </section>
    </main>
  );
}
