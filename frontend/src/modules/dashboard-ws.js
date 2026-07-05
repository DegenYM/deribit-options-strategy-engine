import { INVESTOR } from "../shared/context.js";
import { resolveApiUrl } from "../shared/context.js";
import {
  DASHBOARD_WS_CHANNELS,
  DASHBOARD_WS_ENABLED,
  DASHBOARD_WS_RECONNECT_BASE_MS,
  DASHBOARD_WS_RECONNECT_MAX_MS,
} from "../shared/config.js";
import { STATE } from "../shared/state.js";
import {
  applyDashboardBundlePayload,
  mergeStatusPayload,
  updateUnderlyingIndexCache,
} from "./domain.js";
import { applySpotPayload, updateHeaderSpotDom } from "./refresh.js";
import { renderRegime, renderTopBar, renderAggregate, renderStrategyGroups, renderRecentActivity, renderAccountCards, renderBookCards } from "./render.js";
import { saveInvestorCache } from "./investor-cache.js";

let ws = null;
let reconnectAttempt = 0;
let reconnectTimer = null;
let renderDashboardFn = null;

function buildWsUrl() {
  const path = `/ws/dashboard?channels=${encodeURIComponent(DASHBOARD_WS_CHANNELS)}`;
  const httpUrl = resolveApiUrl(path);
  if (/^https?:\/\//i.test(httpUrl)) {
    const u = new URL(httpUrl);
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    return u.toString();
  }
  const proto = typeof window !== "undefined" && window.location?.protocol === "https:" ? "wss:" : "ws:";
  const host = typeof window !== "undefined" ? window.location.host : "";
  const rel = httpUrl.startsWith("/") ? httpUrl : `/${httpUrl}`;
  return `${proto}//${host}${rel}`;
}

function scheduleReconnect() {
  if (!DASHBOARD_WS_ENABLED || reconnectTimer) return;
  const delay = Math.min(
    DASHBOARD_WS_RECONNECT_MAX_MS,
    DASHBOARD_WS_RECONNECT_BASE_MS * 2 ** reconnectAttempt
  );
  reconnectAttempt += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
}

function partialRender(channel) {
  const render = renderDashboardFn;
  if (!render) return;
  if (channel === "market") {
    updateHeaderSpotDom();
    if (!STATE.refreshInFlight) {
      renderAggregate(STATE.status, STATE.report);
      renderStrategyGroups(STATE.status, STATE.report, STATE.groups);
      renderRecentActivity(STATE.status, STATE.report, STATE.groups);
    }
    return;
  }
  if (channel === "portfolio" || channel === "groups") {
    updateUnderlyingIndexCache(STATE.status, STATE.groups);
    renderRegime(STATE.status);
    renderAggregate(STATE.status, STATE.report);
    renderStrategyGroups(STATE.status, STATE.report, STATE.groups);
    renderRecentActivity(STATE.status, STATE.report, STATE.groups);
    if (!INVESTOR) {
      renderAccountCards(STATE.health, STATE.status);
      renderBookCards(STATE.status);
    }
    if (INVESTOR && STATE.investorReady) {
      saveInvestorCache();
    }
    return;
  }
  if (channel === "health") {
    renderTopBar(STATE.health);
  }
}

function handleMessage(msg) {
  if (!msg || typeof msg !== "object") return;
  if (msg.type === "hello") {
    STATE.wsConnected = true;
    STATE.wsChannels = Array.isArray(msg.channels) ? msg.channels : [];
    return;
  }
  if (msg.type !== "update" || !msg.channel) return;
  const channel = String(msg.channel);
  const data = msg.data;
  const tsMs = Number(msg.ts_ms) || Date.now();
  if (channel === "market" && data) {
    applySpotPayload(data, { updateDom: false });
    STATE.wsLastMarketMs = tsMs;
    partialRender("market");
    updateHeaderSpotDom();
    return;
  }
  if (channel === "health" && data) {
    STATE.health = data;
    STATE.wsLastHealthMs = tsMs;
    partialRender("health");
    return;
  }
  if (channel === "portfolio" && data) {
    STATE.status = mergeStatusPayload(STATE.status, data);
    STATE.dataFreshness.source = "live";
    STATE.dataFreshness.live = true;
    STATE.dataFreshness.statusMs = tsMs;
    STATE.groupsLivePending = false;
    STATE.statusErrorOnce = false;
    STATE.wsLastPortfolioMs = tsMs;
    partialRender("portfolio");
    return;
  }
  if (channel === "groups" && data) {
    applyDashboardBundlePayload({ groups: data });
    STATE.wsLastGroupsMs = tsMs;
    partialRender("groups");
  }
}

function connect() {
  if (!DASHBOARD_WS_ENABLED || typeof WebSocket === "undefined") return;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  try {
    ws = new WebSocket(buildWsUrl());
  } catch (err) {
    console.warn("dashboard websocket connect failed", err);
    scheduleReconnect();
    return;
  }

  ws.addEventListener("open", () => {
    reconnectAttempt = 0;
    STATE.wsConnected = true;
  });

  ws.addEventListener("message", (ev) => {
    try {
      handleMessage(JSON.parse(ev.data));
    } catch (err) {
      console.warn("dashboard websocket message parse failed", err);
    }
  });

  ws.addEventListener("close", () => {
    STATE.wsConnected = false;
    ws = null;
    scheduleReconnect();
  });

  ws.addEventListener("error", () => {
    STATE.wsConnected = false;
  });
}

export function initDashboardWebSocket({ renderDashboard } = {}) {
  if (!DASHBOARD_WS_ENABLED) return;
  renderDashboardFn = renderDashboard ?? null;
  connect();
}

export function stopDashboardWebSocket() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    try {
      ws.close();
    } catch (_) {
      /* ignore */
    }
    ws = null;
  }
  STATE.wsConnected = false;
}

/** Skip REST /api/spot when a recent websocket market push arrived. */
export function wsMarketFresh(maxAgeMs = 15000) {
  if (!STATE.wsConnected || !STATE.wsLastMarketMs) return false;
  return Date.now() - STATE.wsLastMarketMs < maxAgeMs;
}
