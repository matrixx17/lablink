/* Shared UI primitives for the LabLink Bioprocess Console.
   Themed via CSS variables in styles.css so they look industrial in this
   dashboard and editorial in the comp-chem one. */

import styles from "./ui.module.css";
import type React from "react";

// ---- Hero header -----------------------------------------------------------

export function HeroHeader({
  eyebrow,
  title,
  context,
  status,
  actions,
}: {
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  context?: React.ReactNode;
  status?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className={styles.hero}>
      <div className={styles.heroMain}>
        {eyebrow ? <p className={styles.heroEyebrow}>{eyebrow}</p> : null}
        <h1 className={styles.heroTitle}>{title}</h1>
        {context ? <div className={styles.heroContext}>{context}</div> : null}
        {status ? <div className={styles.heroStatus}>{status}</div> : null}
      </div>
      {actions ? <div className={styles.heroActions}>{actions}</div> : null}
    </header>
  );
}

// ---- KPI strip -------------------------------------------------------------

export type Kpi = {
  label: string;
  value: React.ReactNode;
  unit?: string;
  hint?: string;
  tone?: "neutral" | "good" | "warn" | "bad";
};

export function KpiStrip({ items }: { items: Kpi[] }) {
  return (
    <div className={styles.kpiStrip} role="list">
      {items.map((k, i) => (
        <div
          key={`${k.label}-${i}`}
          className={`${styles.kpi} ${k.tone ? styles[`kpi_${k.tone}`] : ""}`}
          role="listitem"
        >
          <div className={styles.kpiLabel}>{k.label}</div>
          <div className={styles.kpiValueRow}>
            <span className={`${styles.kpiValue} num`}>{k.value}</span>
            {k.unit ? <span className={styles.kpiUnit}>{k.unit}</span> : null}
          </div>
          {k.hint ? <div className={styles.kpiHint}>{k.hint}</div> : null}
        </div>
      ))}
    </div>
  );
}

// ---- Section rule ----------------------------------------------------------

export function SectionRule({
  eyebrow,
  title,
  actions,
}: {
  eyebrow?: string;
  title?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className={styles.sectionRule}>
      <div>
        {eyebrow ? <p className={styles.sectionEyebrow}>{eyebrow}</p> : null}
        {title ? <h2 className={styles.sectionTitle}>{title}</h2> : null}
      </div>
      {actions ? <div className={styles.sectionActions}>{actions}</div> : null}
    </div>
  );
}

// ---- Action bar / buttons --------------------------------------------------

export function ActionBar({ children }: { children: React.ReactNode }) {
  return <div className={styles.actionBar}>{children}</div>;
}

export function PrimaryButton({
  children,
  onClick,
  disabled,
  loading,
  as = "button",
  href,
  type,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  loading?: boolean;
  as?: "button" | "a";
  href?: string;
  type?: "button" | "submit";
}) {
  if (as === "a") {
    return (
      <a className={styles.primaryButton} href={href} aria-disabled={disabled || loading || undefined}>
        {children}
      </a>
    );
  }
  return (
    <button
      type={type || "button"}
      onClick={onClick}
      disabled={disabled || loading}
      className={styles.primaryButton}
      aria-busy={loading || undefined}
    >
      {loading ? <span className={styles.spinner} aria-hidden /> : null}
      {children}
    </button>
  );
}

export function SecondaryButton({
  children,
  onClick,
  disabled,
  as = "button",
  href,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  as?: "button" | "a";
  href?: string;
}) {
  if (as === "a") {
    return (
      <a className={styles.secondaryButton} href={href}>
        {children}
      </a>
    );
  }
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={styles.secondaryButton}>
      {children}
    </button>
  );
}

// ---- Card (kept for back-compat; minimal styling) --------------------------

export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <section className={`${styles.card} ${className}`}>{children}</section>;
}

// Legacy ad-hoc stat — kept so we don't have to touch every page in one shot.
export function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className={styles.stat}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

// Legacy page header — keep for any not-yet-redesigned page.
export function PageHeader({
  title,
  eyebrow,
  actions,
}: {
  title: string;
  eyebrow?: string;
  actions?: React.ReactNode;
}) {
  return (
    <HeroHeader eyebrow={eyebrow} title={title} actions={actions} />
  );
}

// ---- Status badge ----------------------------------------------------------

export function StatusBadge({ status }: { status?: string | null }) {
  const normalized = (status || "unknown").toLowerCase();
  const cls =
    normalized.includes("fail") || normalized.includes("crash") || normalized.includes("bad") || normalized === "off"
      ? styles.badge_fail
      : normalized.includes("warn") || normalized.includes("flag") || normalized.includes("excursion")
        ? styles.badge_warn
        : normalized.includes("complete") || normalized.includes("pass") || normalized.includes("normal")
              || normalized.includes("harvest") || normalized.includes("verified") || normalized === "good"
          ? styles.badge_pass
          : styles.badge_neutral;
  return <span className={`${styles.badge} ${cls}`}>{status || "unknown"}</span>;
}

// ---- Data table ------------------------------------------------------------

export function DataTable({ children }: { children: React.ReactNode }) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>{children}</table>
    </div>
  );
}

// ---- Empty / Error / formatting helpers -----------------------------------

export function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className={styles.empty}>{children}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  return (
    <div className={styles.error}>
      <strong>error</strong>
      <span>{error instanceof Error ? error.message : String(error)}</span>
    </div>
  );
}

export function fmtDate(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function fmtDateOnly(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
}

export function fmtNumber(value?: number | null, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}
