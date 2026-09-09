/** Set at build time via esbuild `define`; falls back to runtime window flag in dev. */
const buildInvestor =
  typeof __BUILD_INVESTOR__ !== "undefined" ? __BUILD_INVESTOR__ : undefined;

export const DASHBOARD_MODE =
  buildInvestor !== undefined
    ? buildInvestor
      ? "investor"
      : "ops"
    : typeof window !== "undefined" && window.__DASHBOARD_MODE__ === "investor"
      ? "investor"
      : "ops";
export const INVESTOR = DASHBOARD_MODE === "investor";

/** Ops dashboard iframed by `./bot admin` (`?embed=1` or nested frame). */
export const ADMIN_EMBED =
  !INVESTOR &&
  typeof window !== "undefined" &&
  (window.self !== window.top || /(?:\?|&)embed=1(?:&|$)/.test(String(window.location.search || "")));

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

export const DASHBOARD_TOKEN_HEADER = "X-Dashboard-Token";
export const DASHBOARD_TOKEN_STORAGE_KEY = "dashboard_api_token";

/**
 * Shared secret for `DASHBOARD_API_TOKEN`-gated servers. Resolution order:
 * `<meta name="dashboard-api-token">` (server-embedded, `DASHBOARD_API_TOKEN_EMBED=true`)
 * → `window.__DASHBOARD_TOKEN__` → `localStorage.dashboard_api_token`. Empty
 * string means the server runs without a gate and no header is sent.
 */
export function dashboardApiToken() {
  try {
    const m = document.querySelector('meta[name="dashboard-api-token"]');
    const fromMeta = m?.getAttribute("content")?.trim();
    if (fromMeta) return fromMeta;
  } catch (_) {
    /* no DOM */
  }
  if (typeof window !== "undefined" && window.__DASHBOARD_TOKEN__) {
    const fromWindow = String(window.__DASHBOARD_TOKEN__).trim();
    if (fromWindow) return fromWindow;
  }
  try {
    return String(globalThis.localStorage?.getItem(DASHBOARD_TOKEN_STORAGE_KEY) || "").trim();
  } catch (_) {
    return "";
  }
}

/** Merge the auth header into caller headers (Headers instance, array, or plain object). */
export function withApiAuthHeaders(headers) {
  const token = dashboardApiToken();
  if (!token) return headers;
  if (typeof Headers !== "undefined" && headers instanceof Headers) {
    const out = new Headers(headers);
    out.set(DASHBOARD_TOKEN_HEADER, token);
    return out;
  }
  if (Array.isArray(headers)) {
    return [...headers.filter(([k]) => String(k).toLowerCase() !== DASHBOARD_TOKEN_HEADER.toLowerCase()), [DASHBOARD_TOKEN_HEADER, token]];
  }
  return { ...(headers || {}), [DASHBOARD_TOKEN_HEADER]: token };
}

/** `fetch()` for dashboard endpoints: resolves the API base and attaches the token when configured. */
export function apiFetch(url, options = {}) {
  const target = resolveApiUrl(url);
  return fetch(target, { ...options, headers: withApiAuthHeaders(options.headers) });
}

/** Append `?token=` for the websocket handshake (browsers cannot set WS headers). */
export function withWsToken(url) {
  const token = dashboardApiToken();
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}
