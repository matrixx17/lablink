export const DEMO_SESSION_TOKEN_KEY = "lablink_demo_session";
export const DEMO_SESSION_EXPIRES_KEY = "lablink_demo_expires_at";
export const DEMO_SESSION_DOMAIN_KEY = "lablink_demo_domain";

export type DemoSessionRecord = {
  token: string;
  domain: "compchem" | "wetlab";
  expiresAt: string;
};

export function getDemoSession(): DemoSessionRecord | null {
  if (typeof window === "undefined") return null;
  const token = window.sessionStorage.getItem(DEMO_SESSION_TOKEN_KEY);
  const expiresAt = window.sessionStorage.getItem(DEMO_SESSION_EXPIRES_KEY);
  const domain = window.sessionStorage.getItem(DEMO_SESSION_DOMAIN_KEY);
  if (!token || !expiresAt || (domain !== "compchem" && domain !== "wetlab")) return null;
  if (Date.parse(expiresAt) <= Date.now()) {
    clearDemoSession();
    return null;
  }
  return { token, expiresAt, domain };
}

export function storeDemoSession(record: DemoSessionRecord) {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(DEMO_SESSION_TOKEN_KEY, record.token);
  window.sessionStorage.setItem(DEMO_SESSION_EXPIRES_KEY, record.expiresAt);
  window.sessionStorage.setItem(DEMO_SESSION_DOMAIN_KEY, record.domain);
}

export function clearDemoSession() {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(DEMO_SESSION_TOKEN_KEY);
  window.sessionStorage.removeItem(DEMO_SESSION_EXPIRES_KEY);
  window.sessionStorage.removeItem(DEMO_SESSION_DOMAIN_KEY);
}

export function demoAuthHeaders(): HeadersInit {
  const session = getDemoSession();
  return session ? { Authorization: `Demo ${session.token}` } : {};
}
