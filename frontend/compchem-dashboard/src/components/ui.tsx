import styles from "./ui.module.css";
import type React from "react";

export function PageHeader({
  title,
  eyebrow,
  actions
}: {
  title: string;
  eyebrow?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className={styles.header}>
      <div>
        {eyebrow ? <p className={styles.eyebrow}>{eyebrow}</p> : null}
        <h1>{title}</h1>
      </div>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </div>
  );
}

export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <section className={`${styles.card} ${className}`}>{children}</section>;
}

export function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className={styles.stat}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function StatusBadge({ status }: { status?: string | null }) {
  const normalized = (status || "unknown").toLowerCase();
  const cls =
    normalized.includes("fail") || normalized.includes("crash")
      ? styles.fail
      : normalized.includes("warn") || normalized.includes("flag")
        ? styles.warn
        : normalized.includes("complete") || normalized.includes("pass") || normalized.includes("normal")
          ? styles.pass
          : styles.neutral;
  return <span className={`${styles.badge} ${cls}`}>{status || "unknown"}</span>;
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className={styles.empty}>{children}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  return <div className={styles.error}>{error instanceof Error ? error.message : String(error)}</div>;
}

export function fmtDate(value?: string | null) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

export function fmtNumber(value?: number | null, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}
