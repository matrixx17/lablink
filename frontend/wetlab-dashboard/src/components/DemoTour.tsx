import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import Shepherd from "shepherd.js";
import "shepherd.js/dist/css/shepherd.css";
import { api, WetlabCampaign } from "../api/client";
import styles from "./Layout.module.css";
import { withOrg } from "./Layout";
import "./demoTour.css";

const STORAGE_KEY = "lablink.wetlab.guidedTour";
const EARLY_ACCESS_URL =
  import.meta.env.VITE_LABLINK_EARLY_ACCESS_URL ||
  "https://form.typeform.com/to/lablink-demo";

type TourStatus = "idle" | "active" | "skipped" | "completed";

type TourState = {
  status: TourStatus;
  stepIndex: number;
  campaignId?: string;
  batchId?: string;
};

type TourStep = {
  id: string;
  selector: string;
  title: string;
  text: string;
  attachOn?: "auto" | "top" | "bottom" | "left" | "right";
  path: (state: TourState) => string | null;
};

const STEPS: TourStep[] = [
  {
    id: "campaigns-list",
    selector: "[data-tour='wetlab-campaigns-list']",
    title: "Your Process Development Campaigns",
    text: "Every bioprocess campaign — strain build, process dev, scale-up, tech transfer — lives here. Each row tracks batches, QC, and the audit trail to manufacturing.",
    attachOn: "bottom",
    path: () => `/campaigns`,
  },
  {
    id: "overview",
    selector: "[data-tour='wetlab-campaign-overview']",
    title: "Three Conditions, One Campaign",
    text: "BioProcess Labs delivered three fed-batch conditions. LabLink verified the delivery and organized all three batches automatically.",
    attachOn: "bottom",
    path: (state) => state.campaignId ? `/campaigns/${state.campaignId}` : null,
  },
  {
    id: "comparison",
    selector: "[data-tour='wetlab-batch-comparison']",
    title: "Why We Picked This Condition",
    text: "Batch_004C produced 3x the titer of the baseline condition. This comparison is automatic — no spreadsheet required.",
    attachOn: "top",
    path: (state) => state.campaignId ? `/campaigns/${state.campaignId}/compare` : null,
  },
  {
    id: "timeline",
    selector: "[data-tour='wetlab-qc-flag']",
    title: "Automated QC Detection",
    text: "LabLink detected a dissolved oxygen excursion at hour 72. A scientist reviewed it, accepted it with a documented rationale, and that decision is permanently recorded.",
    attachOn: "top",
    path: (state) => state.campaignId && state.batchId ? `/campaigns/${state.campaignId}/batches/${state.batchId}/timeline` : null,
  },
  {
    id: "audit",
    selector: "[data-tour='wetlab-audit-ack']",
    title: "Human Sign-Off in the Audit Trail",
    text: "The scientist's decision is part of the permanent record — who reviewed it, when, and why they accepted it. This is what an FDA inspector sees.",
    attachOn: "bottom",
    path: (state) => state.campaignId ? `/campaigns/${state.campaignId}/audit` : null,
  },
  {
    id: "methods-export",
    selector: "[data-tour='wetlab-methods-export']",
    title: "Methods Section — Auto-Drafted",
    text: "Bioreactor model, setpoints, cell counter, titer analytics, feed thresholds — assembled into publication-ready process methods. Drop into your CMC section or paper.",
    attachOn: "right",
    path: (state) => state.campaignId ? `/campaigns/${state.campaignId}/methods` : null,
  },
  {
    id: "export",
    selector: "[data-tour='wetlab-export']",
    title: "Batch Record — One Click",
    text: "Export a complete GMP-style batch record with process parameters, offline measurements, QC results, and a verified audit trail. Ready for your CMC section.",
    attachOn: "bottom",
    path: (state) => state.campaignId ? `/campaigns/${state.campaignId}` : null,
  },
];

function loadState(): TourState {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? { status: "idle", stepIndex: 0, ...JSON.parse(raw) } : { status: "idle", stepIndex: 0 };
  } catch {
    return { status: "idle", stepIndex: 0 };
  }
}

function saveState(state: TourState) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* sessionStorage can be unavailable in private contexts. */
  }
}

function waitForElement(selector: string, timeoutMs = 9000) {
  return new Promise<Element | null>((resolve) => {
    const existing = document.querySelector(selector);
    if (existing) {
      resolve(existing);
      return;
    }
    const observer = new MutationObserver(() => {
      const target = document.querySelector(selector);
      if (target) {
        observer.disconnect();
        window.clearTimeout(timer);
        resolve(target);
      }
    });
    const timer = window.setTimeout(() => {
      observer.disconnect();
      resolve(null);
    }, timeoutMs);
    observer.observe(document.body, { childList: true, subtree: true });
  });
}

function routeIds(pathname: string) {
  const campaign = pathname.match(/^(?:\/wetlab)?\/campaigns\/([^/]+)/)?.[1];
  const batch = pathname.match(/^(?:\/wetlab)?\/campaigns\/[^/]+\/batches\/([^/]+)\/timeline/)?.[1];
  return { campaignId: campaign, batchId: batch };
}

export default function DemoTour({ orgId }: { orgId: string }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [tourState, setTourState] = useState<TourState>(() => loadState());
  const [finishOpen, setFinishOpen] = useState(false);
  const activeTour = useRef<any>(null);
  const suppressCancel = useRef(false);

  const persist = (next: TourState) => {
    setTourState(next);
    saveState(next);
  };

  const closeTour = () => {
    suppressCancel.current = true;
    activeTour.current?.cancel();
    activeTour.current = null;
    suppressCancel.current = false;
  };

  const resolveCampaign = async (seed?: TourState): Promise<TourState> => {
    const current = seed || tourState;
    const route = routeIds(location.pathname);
    let campaignId = current.campaignId || route.campaignId;
    let campaign: WetlabCampaign | null = null;

    if (!campaignId) {
      const campaigns = await api.campaigns(orgId);
      campaign = campaigns[0] || null;
      campaignId = campaign?.id;
    }
    if (!campaignId) return current;

    campaign = campaign || await api.campaign(campaignId, orgId);
    let batchId = current.batchId || route.batchId;
    if (!batchId) {
      const batches = await api.campaignBatchesWithMetrics(campaign.id, orgId);
      const lead =
        batches.find((batch) => batch.batch_number === "Batch_004C") ||
        batches.find((batch) => batch.summary_metrics?.lead_condition) ||
        batches
          .slice()
          .sort((a, b) => (b.summary_metrics?.final_titer || 0) - (a.summary_metrics?.final_titer || 0))[0];
      batchId = lead?.id;
    }
    return { ...current, campaignId, batchId };
  };

  const startTour = async () => {
    closeTour();
    const resolved = await resolveCampaign({ status: "active", stepIndex: 0 });
    persist(resolved);
    if (resolved.campaignId) navigate(withOrg(`/campaigns/${resolved.campaignId}`, orgId));
  };

  const skipTour = () => {
    closeTour();
    persist({ ...tourState, status: "skipped" });
  };

  const goToStep = (stepIndex: number) => {
    closeTour();
    resolveCampaign({ ...tourState, status: "active", stepIndex })
      .then(persist)
      .catch(() => persist({ ...tourState, status: "active", stepIndex }));
  };

  const finishTour = () => {
    closeTour();
    persist({ ...tourState, status: "completed", stepIndex: STEPS.length - 1 });
    setFinishOpen(true);
  };

  useEffect(() => {
    if (tourState.status !== "idle" || orgId !== "demo-therapeutics") return;
    const { campaignId } = routeIds(location.pathname);
    if (!campaignId) return;
    resolveCampaign({ status: "active", stepIndex: 0, campaignId })
      .then(persist)
      .catch(() => undefined);
  }, [location.pathname, orgId, tourState.status]);

  useEffect(() => {
    if (tourState.status !== "active") return;
    let cancelled = false;

    (async () => {
      const resolved = await resolveCampaign(tourState);
      if (cancelled) return;
      if (resolved.campaignId !== tourState.campaignId || resolved.batchId !== tourState.batchId) {
        persist(resolved);
        return;
      }

      const step = STEPS[tourState.stepIndex] || STEPS[0];
      const targetPath = step.path(resolved);
      if (!targetPath) return;
      const targetPathWithPrefix = withOrg(targetPath, orgId).split("?")[0];
      if (location.pathname !== targetPathWithPrefix) {
        navigate(withOrg(targetPath, orgId));
        return;
      }

      const target = await waitForElement(step.selector);
      if (cancelled || !target) return;
      target.scrollIntoView({ block: "center", behavior: "smooth" });

      const tour = new Shepherd.Tour({
        useModalOverlay: true,
        keyboardNavigation: true,
        defaultStepOptions: {
          classes: "lablinkTourStep",
          cancelIcon: { enabled: true },
          scrollTo: false,
        },
      });
      activeTour.current = tour;
      tour.on("cancel", () => {
        if (!suppressCancel.current) skipTour();
      });
      tour.addStep({
        id: step.id,
        title: step.title,
        text: step.text,
        attachTo: { element: step.selector, on: step.attachOn || "auto" },
        buttons: buttonsForStep(tourState.stepIndex),
      });
      tour.start();
    })();

    return () => {
      cancelled = true;
      closeTour();
    };
  }, [location.pathname, orgId, tourState.status, tourState.stepIndex, tourState.campaignId, tourState.batchId]);

  const buttonsForStep = (stepIndex: number) => {
    if (stepIndex === 0) {
      return [
        { text: "Skip tour", classes: "shepherd-button-secondary", action: skipTour },
        { text: "Next →", action: () => goToStep(1) },
      ];
    }
    // "View Batch Timeline" sits on the batch-comparison step, regardless of
    // total tour length.
    const comparisonIndex = STEPS.findIndex((s) => s.id === "comparison");
    if (stepIndex === comparisonIndex) {
      return [
        { text: "← Back", classes: "shepherd-button-secondary", action: () => goToStep(stepIndex - 1) },
        { text: "View Batch Timeline →", action: () => goToStep(stepIndex + 1) },
      ];
    }
    if (stepIndex === STEPS.length - 1) {
      return [
        { text: "← Back", classes: "shepherd-button-secondary", action: () => goToStep(stepIndex - 1) },
        { text: "Finish tour", action: finishTour },
      ];
    }
    return [
      { text: "← Back", classes: "shepherd-button-secondary", action: () => goToStep(stepIndex - 1) },
      { text: "Next →", action: () => goToStep(stepIndex + 1) },
    ];
  };

  const showLauncher = tourState.status === "idle" || tourState.status === "skipped";
  const showRestart = tourState.status === "completed";

  return (
    <>
      {showRestart ? (
        <button type="button" className={styles.navTourButton} onClick={startTour}>
          Restart tour
        </button>
      ) : null}
      {showLauncher ? (
        <button type="button" className={styles.floatingTourButton} onClick={startTour}>
          Start guided tour
        </button>
      ) : null}
      {finishOpen ? (
        <div className={styles.tourModalBackdrop} role="presentation">
          <div className={styles.tourModalCard} role="dialog" aria-modal="true" aria-labelledby="lablink-tour-complete">
            <h2 id="lablink-tour-complete">That's LabLink</h2>
            <p>
              You just saw a complete drug discovery campaign — from CRO delivery to regulatory export — tracked automatically with zero manual documentation.
            </p>
            <div className={styles.tourModalActions}>
              <button type="button" className={styles.tourModalSecondary} onClick={() => setFinishOpen(false)}>
                ← Explore on your own
              </button>
              <a className={styles.tourModalPrimary} href="/demo">
                Try the other demo →
              </a>
            </div>
            <p className={styles.tourModalFootnote}>
              Want to use LabLink with your own data?{" "}
              <a href={EARLY_ACCESS_URL} target="_blank" rel="noreferrer">
                Request early access →
              </a>
            </p>
          </div>
        </div>
      ) : null}
    </>
  );
}
