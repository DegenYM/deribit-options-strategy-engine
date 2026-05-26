export const DASHBOARD_MODE =
  typeof window !== "undefined" && window.__DASHBOARD_MODE__ === "investor" ? "investor" : "ops";
export const INVESTOR = DASHBOARD_MODE === "investor";

export const INVESTOR_LOCALE = (() => {
  if (!INVESTOR) return "en";
  const raw = String(
    (typeof window !== "undefined" && window.__INVESTOR_LOCALE__) || "en"
  )
    .trim()
    .toLowerCase();
  if (
    raw === "zh-hant" ||
    raw === "zh_tw" ||
    raw === "zh-tw" ||
    raw === "zh-hk" ||
    raw === "zh"
  ) {
    return "zh";
  }
  return "en";
})();
export const INVESTOR_ZH = INVESTOR && INVESTOR_LOCALE === "zh";

export function i18n(en, zh) {
  if (!INVESTOR) return en;
  return INVESTOR_ZH ? zh : en;
}

function readApiBaseFromMeta() {
  try {
    const m = document.querySelector('meta[name="dashboard-api-base"]');
    return m?.getAttribute("content")?.trim() || "";
  } catch (_) {
    return "";
  }
}

/** Prefix relative `/api/...` URLs when static HTML is hosted away from the FastAPI dashboard. */
export function resolveApiUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  const fromWindow =
    typeof window !== "undefined" && window.__API_BASE__
      ? String(window.__API_BASE__).trim()
      : "";
  const base = (fromWindow || readApiBaseFromMeta()).replace(/\/$/, "");
  const p = path.startsWith("/") ? path : `/${path}`;
  return base ? `${base}${p}` : p;
}
