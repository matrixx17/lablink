import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import Shepherd from "shepherd.js";
import "shepherd.js/dist/css/shepherd.css";
import { api, Campaign } from "../api/client";
import styles from "./Layout.module.css";
import { withOrg } from "./Layout";
import "./demoTour.css";

const STORAGE_KEY = "lablink.compchem.guidedTour";
const EARLY_ACCESS_URL =
  import.meta.env.VITE_LABLINK_EARLY_ACCESS_URL ||
  "https://form.typeform.com/to/lablink-demo";

type TourStatus = "idle" | "active" | "skipped" | "completed";

type TourState = {
  status: TourStatus;
  stepIndex: number;
  campaignId?: number;
  leadMoleculeId?: number;
  leadRunId?: number;
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
    selector: "[data-tour='compchem-campaigns-list']",
    title: "Your Campaigns",
    text: "Every drug-discovery program lives here. Each row is a complete computational story — runs, molecules, audit trail, and regulatory exports — one click away.",
    attachOn: "bottom",
    path: () => `/campaigns`,
  },
  {
    id: "delivery",
    selector: "[data-tour='compchem-delivery']",
    title: "CRO Delivery — Automatically Verified",
    text: "Bio Labs delivered 10 compounds via LabLink. File hashes were verified at the moment of delivery. No zip files, no manual imports.",
    attachOn: "bottom",
    path: (state) => state.campaignId ? `/campaigns/${state.campaignId}` : null,
  },
  {
    id: "lead",
    selector: "[data-tour='compchem-lead']",
    title: "Lead Candidate Selected",
    text: "AC-007 was nominated as the lead compound based on docking score, MD stability, and DFT electronic profile. Click the card to see how.",
    attachOn: "top",
    path: (state) => state.campaignId ? `/campaigns/${state.campaignId}` : null,
  },
  {
    id: "lineage",
    selector: "[data-tour='compchem-lineage']",
    title: "Complete Computational History",
    text: "Every run that informed this decision — docking by Bio Labs, MD and DFT by Acme's team — in one place. With parameters. With software versions.",
    attachOn: "bottom",
    path: (state) => state.leadMoleculeId ? `/molecules/${state.leadMoleculeId}` : null,
  },
  {
    id: "sar",
    selector: "[data-tour='compchem-sar-scatter']",
    title: "Structure-Activity Explorer",
    text: "Click any compound to inspect it. Change the axes to explore different property relationships across all 10 screened compounds.",
    attachOn: "left",
    path: (state) => state.campaignId ? `/campaigns/${state.campaignId}/sar` : null,
  },
  {
    id: "audit",
    selector: "[data-tour='compchem-audit-integrity']",
    title: "Tamper-Evident Audit Trail",
    text: "Every action — CRO delivery, run completion, lead nomination — is cryptographically chained. Altering any record breaks the chain.",
    attachOn: "bottom",
    path: (state) => state.campaignId ? `/campaigns/${state.campaignId}/audit` : null,
  },
  {
    id: "methods-export",
    selector: "[data-tour='compchem-methods-export']",
    title: "Methods Section — Auto-Drafted",
    text: "Force fields, basis sets, integration timesteps, software versions — all assembled into a publication-ready methods paragraph. Drop into your manuscript or IND filing.",
    attachOn: "right",
    path: (state) => state.campaignId ? `/campaigns/${state.campaignId}/methods-export` : null,
  },
  {
    id: "run-detail",
    selector: "[data-tour='compchem-run-detail']",
    title: "Every Run, Fully Inspectable",
    text: "Click into any run for its parameters, inputs, outputs, and QC status. This is the receipt behind every metric in the SAR plot.",
    attachOn: "bottom",
    path: (state) => state.leadRunId ? `/runs/${state.leadRunId}` : null,
  },
  {
    id: "exports",
    selector: "[data-tour='compchem-exports']",
    title: "One-Click Regulatory Export",
    text: "Export an IEEE 2791-2020 BioCompute Object for FDA submission, or a complete Evidence Book package for your data room. Everything verified, everything included.",
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

function campaignIdFromPath(pathname: string) {
  const match = pathname.match(/^\/campaigns\/(\d+)/);
  return match ? Number(match[1]) : undefined;
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
    let campaignId = current.campaignId || campaignIdFromPath(location.pathname);
    let campaign: Campaign | null = null;

    if (!campaignId) {
      const campaigns = await api.campaigns(orgId);
      campaign = campaigns.find((item) => item.has_cro_delivery) || campaigns[0] || null;
      campaignId = campaign?.id;
    }
    if (!campaignId) return current;

    campaign = campaign || await api.campaign(campaignId, orgId);
    let leadMoleculeId = current.leadMoleculeId || campaign.lead_molecule_id || undefined;
    if (!leadMoleculeId) {
      const molecules = await api.campaignMolecules(campaignId, orgId);
      leadMoleculeId =
        molecules.find((molecule) => molecule.external_id === "AC-007" || molecule.name === "AC-007")?.id ||
        molecules[0]?.id;
    }
    let leadRunId = current.leadRunId;
    if (!leadRunId) {
      try {
        const runs = await api.campaignRuns(campaignId, orgId);
        const ac007Run =
          runs.find((r) => (r.molecule_name === "AC-007" || r.molecule_external_id === "mol_001") && r.run_kind === "docking") ||
          runs.find((r) => r.run_kind === "docking") ||
          runs[0];
        leadRunId = ac007Run?.id;
      } catch {
        /* runs endpoint failures shouldn't block earlier tour steps */
      }
    }
    return { ...current, campaignId, leadMoleculeId, leadRunId };
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
    const campaignId = campaignIdFromPath(location.pathname);
    if (!campaignId || tourState.campaignId === campaignId) return;
    // Pre-resolve campaign context so the launcher is ready, but stay idle so
    // the "Start guided tour" button remains visible until the visitor clicks
    // it — the tour should not auto-start and hide its own launcher.
    resolveCampaign({ status: "idle", stepIndex: 0, campaignId })
      .then(persist)
      .catch(() => undefined);
  }, [location.pathname, orgId, tourState.status, tourState.campaignId]);

  useEffect(() => {
    if (tourState.status !== "active") return;
    let cancelled = false;

    (async () => {
      const resolved = await resolveCampaign(tourState);
      if (cancelled) return;
      if (
        resolved.campaignId !== tourState.campaignId ||
        resolved.leadMoleculeId !== tourState.leadMoleculeId ||
        resolved.leadRunId !== tourState.leadRunId
      ) {
        persist(resolved);
        return;
      }

      const step = STEPS[tourState.stepIndex] || STEPS[0];
      const targetPath = step.path(resolved);
      if (!targetPath) return;
      if (location.pathname !== targetPath) {
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
  }, [location.pathname, orgId, tourState.status, tourState.stepIndex, tourState.campaignId, tourState.leadMoleculeId, tourState.leadRunId]);

  const buttonsForStep = (stepIndex: number) => {
    if (stepIndex === 0) {
      return [
        { text: "Skip tour", classes: "shepherd-button-secondary", action: skipTour },
        { text: "Next →", action: () => goToStep(1) },
      ];
    }
    // Lead-candidate step (was index 1 in the old 6-step tour; now index 2).
    const leadIndex = STEPS.findIndex((s) => s.id === "lead");
    if (stepIndex === leadIndex) {
      return [
        { text: "← Back", classes: "shepherd-button-secondary", action: () => goToStep(stepIndex - 1) },
        { text: "View AC-007 →", action: () => goToStep(stepIndex + 1) },
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
      {finishOpen
        ? createPortal(
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
        </div>,
            document.body
          )
        : null}
    </>
  );
}
