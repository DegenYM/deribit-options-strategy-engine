import { INVESTOR, INVESTOR_ZH, i18n, resolveApiUrl } from "../shared/context.js";
import {
  ACTIVITY_PAGE_SIZE,
  BOOK_COLORS,
  CORE_BOOKS,
  FETCH_JSON_MAX_RETRIES,
  FETCH_JSON_RETRYABLE_STATUS,
  FETCH_JSON_RETRY_BASE_MS,
  FRONTEND_API_CONCURRENCY,
  FRONTEND_REFRESH_INTERVAL_MS,
  INVESTOR_OVERLAY_MAX_MS,
  INVESTOR_STATUS_TIMEOUT_MS,
  STRATEGIES,
  STRATEGY_BY_ID,
  USE_DASHBOARD_BUNDLE,
  fmt,
} from "../shared/config.js";
import { STATE } from "../shared/state.js";
import {
  daysUntilUtc,
  formatDateLocal,
  formatTimeLocal,
  parseIsoUtcMs,
} from "./date-time.js";
export function num(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export function fmtUsd(value, places = 2) {
  const n = num(value);
  if (n === null) return "—";
  return places === 0 ? fmt.usd0.format(n) : fmt.usd2.format(n);
}

export function fmtPct(value, decimals = 2) {
  const n = num(value);
  if (n === null) return "—";
  return decimals === 1 ? fmt.pct1.format(n) : fmt.pct2.format(n);
}

export function resolvedPortfolio() {
  if (STATE.status?.portfolio) {
    return {
      portfolio: STATE.status.portfolio,
      source: "live",
      freshnessMs: STATE.dataFreshness.statusMs ?? 0,
    };
  }
  const snap = STATE.portfolioSnapshot?.portfolio;
  if (snap && Object.keys(snap).length > 0) {
    return {
      portfolio: snap,
      source: "snapshot",
      freshnessMs: num(STATE.portfolioSnapshot?.freshness_ms),
    };
  }
  return { portfolio: null, source: null, freshnessMs: null };
}

export function fmtFreshnessMinutes(ms) {
  const n = num(ms);
  if (n === null || n < 0) return null;
  const mins = Math.max(1, Math.round(n / 60_000));
  return mins;
}

export function dataFreshnessBadgeHtml() {
  const resolved = resolvedPortfolio();
  if (resolved.source === "live") {
    const age = num(STATE.dataFreshness.statusMs);
    if (age !== null && age < 30_000) {
      return `<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-emerald-500/40 bg-emerald-500/10 text-emerald-200">${i18n("Live", "即時")}</span>`;
    }
  }
  if (resolved.source === "snapshot") {
    const mins = fmtFreshnessMinutes(resolved.freshnessMs);
    const label =
      mins !== null
        ? i18n(`Snapshot · ~${mins}m ago`, `快照 · 約 ${mins} 分鐘前`)
        : i18n("Snapshot", "快照");
    return `<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-200">${label}</span>`;
  }
  return `<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-800/60 text-slate-400">${i18n("Loading…", "載入中…")}</span>`;
}

export function renderDataFreshnessBadge() {
  if (!INVESTOR) return;
  const host = document.getElementById("data-freshness-slot");
  if (!host) return;
  host.innerHTML = dataFreshnessBadgeHtml();
}

export function setInvestorProgressBar(active, { indeterminate = false } = {}) {
  const bar = document.getElementById("investor-progress-bar");
  if (!bar) return;
  bar.classList.toggle("hidden", !active);
  bar.classList.toggle("investor-progress-bar--indeterminate", active && indeterminate);
}

export function aggregateSkeletonHtml() {
  const cell = `<div class="skeleton-block h-16 rounded-lg"></div>`;
  const desktop = `<div class="overview-metrics-grid">${cell.repeat(8)}</div>`;
  if (!INVESTOR) return desktop;
  const mobile = `<div class="inv-dashboard">
      <div class="inv-panel skeleton-block" style="height:5.5rem"></div>
      <div class="inv-panel skeleton-block" style="height:4rem"></div>
      <div class="inv-panel skeleton-block" style="height:7rem"></div>
    </div>`;
  return `<div class="investor-view-desktop">${desktop}</div><div class="investor-view-mobile">${mobile}</div>`;
}

export function overviewMetricsGridHtml(ctx) {
  const {
    totalEquity,
    dayStart,
    dayPnl,
    dayDrawdown,
    openCredit,
    creditByStrategy,
    summary,
    winRate,
    avgHolding,
    sinceLine,
    lifetimePnl,
    lifetimeNativeByBook,
    closedCount,
    windowLabelDays,
    windowPnl,
    windowNativeByBook,
    lifetimeApr,
    windowApr,
    equityNativeByBook,
    equityUsdByBook,
  } = ctx;
  return `
    <div class="overview-metrics-grid">
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${i18n("Total equity", "總權益")}</div>
        <div class="text-2xl font-mono">${fmtUsd(totalEquity)}</div>
        <div class="text-[11px] text-slate-500">${i18n("USDC equivalent (all books)", "USDC 約當（全帳本合計）")}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${fmtBookEquityDualBreakdown(equityNativeByBook, equityUsdByBook)}</div>
          <div class="overview-metric-line">${i18n("day-start", "日初")} ${fmtUsd(dayStart)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${i18n("Day P&L", "本日損益")}</div>
        <div class="text-2xl font-mono ${pnlClass(dayPnl)}">${fmtUsd(dayPnl)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${i18n("drawdown", "回撤")} ${fmtPct(dayDrawdown)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${i18n("Open credit", "未實現權利金（進場收斂）")}</div>
        <div class="text-2xl font-mono">${fmtUsd(openCredit)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${fmtOpenCreditStrategyBreakdown(creditByStrategy)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${i18n("Win rate · avg holding", "勝率 · 平均持有")}</div>
        <div class="text-2xl font-mono">${summary ? `${fmtPct(winRate, 1)} · ${fmtNum(avgHolding, 2)}${INVESTOR_ZH ? " 天" : "d"}` : "—"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${summary ? sinceLine : i18n("Loading performance…", "績效摘要載入中…")}</div>
        </div>
      </div>

      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${i18n("Total profit (lifetime)", "累計已實現損益")}</div>
        <div class="text-2xl font-mono ${pnlClass(lifetimePnl)}">${summary ? fmtUsd(lifetimePnl) : "—"}</div>
        <div class="overview-metric-meta">
          ${summary ? `<div class="overview-metric-line">${fmtLifetimeRealizedNativeBreakdown(lifetimeNativeByBook)}</div>` : ""}
          <div class="overview-metric-line">${summary ? `${closedCount ?? 0} ${i18n("closed groups", "筆已平倉部位")}` : ""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${rollingWindowProfitLabel(windowLabelDays)}</div>
        <div class="text-2xl font-mono ${pnlClass(windowPnl)}">${summary ? fmtUsd(windowPnl) : "—"}</div>
        <div class="overview-metric-meta">
          ${summary ? `<div class="overview-metric-line">${fmtLifetimeRealizedNativeBreakdown(windowNativeByBook)}</div>` : ""}
          <div class="overview-metric-line">${summary ? rollingWindowPnlHint(windowLabelDays) : ""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${i18n("Realized APR (lifetime)", "已實現年化（存續期）")}</div>
        <div class="text-2xl font-mono">${summary ? fmtPct(lifetimeApr) : "—"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${summary ? i18n("annualized on actual span", "依實際區間年化") : ""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${rollingWindowAprLabel(windowLabelDays)}</div>
        <div class="text-2xl font-mono">${summary ? fmtPct(windowApr) : "—"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line overview-metric-line--hint">${summary ? rollingWindowAprHint(windowLabelDays) : ""}</div>
        </div>
      </div>
    </div>`;
}

export function investorNativeChipsHtml(byBook, { pnl = false, places = { BTC: 5, ETH: 4, USDC: 2 } } = {}) {
  const symbols = { BTC: "₿", ETH: "◆", USDC: "$" };
  return ["BTC", "ETH", "USDC"]
    .map((book) => {
      const n = num(byBook[book]);
      const text = n === null ? "—" : fmtNum(n, places[book] ?? 4);
      const tone = pnl ? pnlClass(byBook[book]) : "";
      return `<span class="inv-chip ${tone}"><span class="inv-chip-sym">${symbols[book]}</span><span class="inv-chip-val font-mono tabular-nums">${text}</span></span>`;
    })
    .join("");
}

export function investorOpenCreditMiniHtml(byStrategy) {
  return strategyOrder(new Set(STRATEGIES.map((s) => s.id)))
    .map((id) => {
      const short = escapeHtml(strategyInfo(id).short);
      const n = num(byStrategy[id]);
      const text = n === null ? "—" : fmtUsd(n);
      return `<div class="inv-mini-row"><span class="inv-mini-label">${short}</span><span class="inv-mini-value font-mono tabular-nums">${text}</span></div>`;
    })
    .join("");
}

export function investorOverviewHtml(ctx) {
  const {
    totalEquity,
    dayStart,
    dayPnl,
    dayDrawdown,
    openCredit,
    creditByStrategy,
    summary,
    winRate,
    avgHolding,
    sinceLine,
    lifetimePnl,
    lifetimeNativeByBook,
    closedCount,
    windowLabelDays,
    windowPnl,
    windowNativeByBook,
    lifetimeApr,
    windowApr,
    equityNativeByBook,
    equityUsdByBook,
  } = ctx;
  const winHold =
    summary !== null && summary !== undefined
      ? `${fmtPct(winRate, 1)} · ${fmtNum(avgHolding, 2)}${INVESTOR_ZH ? " 天" : "d"}`
      : "—";
  const winSub = summary
    ? sinceLine
    : i18n("Loading performance…", "績效摘要載入中…");
  return `<div class="inv-dashboard">
    <section class="inv-panel inv-panel--hero" aria-label="${i18n("Account snapshot", "帳戶快照")}">
      <div class="inv-split">
        <div class="inv-kpi">
          <span class="inv-kpi-label">${i18n("Total equity", "總權益")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${fmtUsd(totalEquity)}</span>
          <span class="inv-kpi-foot">${i18n("USDC equivalent", "USDC 約當")} · ${i18n("day-start", "日初")} ${fmtUsd(dayStart)}</span>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${i18n("Day P&L", "本日損益")}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${pnlClass(dayPnl)}">${fmtUsd(dayPnl)}</span>
          <span class="inv-kpi-foot">${i18n("drawdown", "回撤")} ${fmtPct(dayDrawdown)}</span>
        </div>
      </div>
      <div class="inv-equity-dual">${fmtBookEquityDualBreakdown(equityNativeByBook, equityUsdByBook)}</div>
    </section>

    <section class="inv-panel" aria-label="${i18n("Open risk", "未平倉風險")}">
      <div class="inv-split">
        <div class="inv-kpi">
          <span class="inv-kpi-label">${i18n("Open credit", "未實現權利金")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${fmtUsd(openCredit)}</span>
          <div class="inv-mini-list">${investorOpenCreditMiniHtml(creditByStrategy)}</div>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${i18n("Win rate · hold", "勝率 · 持有")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${winHold}</span>
          <span class="inv-kpi-foot">${winSub}</span>
        </div>
      </div>
    </section>

    <section class="inv-panel" aria-label="${i18n("Realized performance", "已實現績效")}">
      <h3 class="inv-panel-title">${i18n("Realized P&L", "已實現損益")}</h3>
      <div class="inv-compare">
        <div class="inv-compare-col">
          <span class="inv-compare-tag">${i18n("Lifetime", "存續")}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${pnlClass(lifetimePnl)}">${summary ? fmtUsd(lifetimePnl) : "—"}</span>
          <div class="inv-chips-row inv-chips-row--compact">${summary ? investorNativeChipsHtml(lifetimeNativeByBook, { pnl: true }) : ""}</div>
          <span class="inv-kpi-foot">${summary ? `${closedCount ?? 0} ${i18n("closed", "筆平倉")}` : ""}</span>
        </div>
        <div class="inv-compare-col">
          <span class="inv-compare-tag">${i18n("Last", "近")} ${windowLabelDays}${INVESTOR_ZH ? " 日" : "d"}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${pnlClass(windowPnl)}">${summary ? fmtUsd(windowPnl) : "—"}</span>
          <div class="inv-chips-row inv-chips-row--compact">${summary ? investorNativeChipsHtml(windowNativeByBook, { pnl: true }) : ""}</div>
          <span class="inv-kpi-foot">${summary ? rollingWindowPnlHint(windowLabelDays) : ""}</span>
        </div>
      </div>
      <div class="inv-split inv-split--apr">
        <div class="inv-kpi inv-kpi--compact">
          <span class="inv-kpi-label">${i18n("APR lifetime", "年化·存續")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${summary ? fmtPct(lifetimeApr) : "—"}</span>
        </div>
        <div class="inv-kpi inv-kpi--compact">
          <span class="inv-kpi-label">${i18n("APR", "年化")} ${windowLabelDays}${INVESTOR_ZH ? " 日" : "d"}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${summary ? fmtPct(windowApr) : "—"}</span>
          <span class="inv-kpi-foot">${summary ? rollingWindowAprHint(windowLabelDays) : ""}</span>
        </div>
      </div>
    </section>
  </div>`;
}

/** Deribit-style: ($) for USDC, ₿ / ♦ for coin (option premium / mark). */
export function fmtDeribitPriceCell(value, collateralCurrency) {
  const n = num(value);
  if (n === null) return "—";
  const c = String(collateralCurrency || "").toUpperCase();
  const sp = '<span class="text-slate-500">';
  const ep = "</span>";
  if (c === "USDC") return `${sp}($)${ep}\u00A0${fmtNum(n, 4)}`;
  if (c === "BTC") return `${sp}₿${ep}\u00A0${fmtNum(n, 5)}`;
  if (c === "ETH") return `${sp}♦${ep}\u00A0${fmtNum(n, 5)}`;
  return fmtNum(n, 4);
}

/**
 * Deribit：選擇權用 ``size``（標的幣名目）；``size_currency`` 僅文件上給期貨用。
 * 若對選擇權誤用 ``size_currency``，MTM 會縮錯數量級。
 */
export function openRowPositionSignedSizeForDisplay(p) {
  if (!p) return null;
  const kind = String(p.kind || "").toLowerCase();
  const sell = String(p.direction || "").toLowerCase() === "sell";
  if (kind === "option") {
    const sz = num(p.size);
    if (sz === null || sz === 0) return null;
    // Deribit：空頭契約數常為正；與網頁 Amount 一致一律用 direction 定號。
    return sell ? -Math.abs(sz) : Math.abs(sz);
  }
  const sc = num(p.size_currency);
  if (sc !== null && sc !== 0) {
    if (sell && sc > 0) return -Math.abs(sc);
    return sc;
  }
  const sz = num(p.size);
  if (sz === null || sz === 0) return null;
  if (sell && sz > 0) return -Math.abs(sz);
  return sz;
}

/** Short option amount: negative qty (Deribit Amount column). */
export function fmtShortAmountDisplay(g, status, groups = null) {
  const ctx = groups ?? STATE.groups;
  if (countOpenGroupsSharingLeg(status, ctx, g, "short") > 1) {
    const groupSize = openRowLegGroupSignedSize(g, "short");
    if (groupSize !== null) return fmtNum(groupSize, 4);
  }
  const p = openRowPosition(status, g);
  const signed = openRowPositionSignedSizeForDisplay(p);
  if (signed !== null) return fmtNum(signed, 4);
  const q = num(g.quantity);
  if (q === null) return "—";
  const qSigned = q > 0 ? -Math.abs(q) : q;
  return fmtNum(qSigned, 4);
}

export function openRowLegInstrumentName(g, role) {
  return role === "long" ? String(g?.long_instrument_name || "") : String(g?.short_instrument_name || "");
}

export function openRowLegGroupSignedSize(g, role) {
  const q = num(g.quantity);
  if (q === null) return null;
  return role === "short" ? -Math.abs(q) : Math.abs(q);
}

/** Open groups on the same account sharing one exchange instrument (aggregated position). */
export function countOpenGroupsSharingLeg(status, groups, g, role) {
  const instrument = openRowLegInstrumentName(g, role);
  if (!instrument) return 0;
  const account = String(g?.account_name || "");
  const seen = new Set();
  let count = 0;
  for (const src of [status?.trade_groups || [], groups?.open || []]) {
    for (const row of src) {
      if (!isOpenTradeGroup(row)) continue;
      const key = tradeGroupKey(row);
      if (seen.has(key)) continue;
      seen.add(key);
      if (openRowLegInstrumentName(row, role) !== instrument) continue;
      if (account && String(row?.account_name || "") !== account) continue;
      count++;
    }
  }
  return count;
}

export function openRowLegPosition(status, g, role) {
  const instrument = openRowLegInstrumentName(g, role);
  if (!instrument) return null;
  const rows = status?.positions || [];
  const account = String(g?.account_name || "");
  if (account) {
    const exact = rows.find(
      (x) => x.instrument_name === instrument && String(x.account_name || "") === account
    );
    if (exact) return exact;
  }
  return rows.find((x) => x.instrument_name === instrument) || null;
}

export function openRowPosition(status, g) {
  return openRowLegPosition(status, g, "short");
}

export function enrichOpenGroupRow(status, g, groups = null) {
  const ctx = groups ?? STATE.groups;
  let avg = g.short_average_price;
  let mrk = g.short_mark_price;
  let fpl = g.short_floating_profit_loss;
  let hasFpl = g.short_has_floating_profit_loss;
  let fplUsd = g.short_floating_profit_loss_usd;
  let hasFplUsd = g.short_has_floating_profit_loss_usd;
  const missingAvg = avg === null || avg === undefined || avg === "";
  const missingMrk = mrk === null || mrk === undefined || mrk === "";
  const missingFpl = fpl === null || fpl === undefined || fpl === "";
  const missingFplUsd = fplUsd === null || fplUsd === undefined || fplUsd === "";
  const sharedShort = countOpenGroupsSharingLeg(status, ctx, g, "short") > 1;
  if (
    (missingAvg ||
      missingMrk ||
      missingFpl ||
      missingFplUsd ||
      hasFpl === undefined ||
      hasFplUsd === undefined) &&
    status?.positions?.length
  ) {
    const p = openRowPosition(status, g);
    if (p) {
      if (missingAvg) avg = p.average_price;
      if (missingMrk) mrk = p.mark_price;
      if (!sharedShort) {
        if (missingFpl) fpl = p.floating_profit_loss;
        if (hasFpl === undefined) hasFpl = p.has_floating_profit_loss;
        if (missingFplUsd) fplUsd = p.floating_profit_loss_usd;
        if (hasFplUsd === undefined) hasFplUsd = p.has_floating_profit_loss_usd;
      }
    }
  }
  return {
    ...g,
    short_average_price: avg,
    short_mark_price: mrk,
    short_floating_profit_loss: fpl,
    short_has_floating_profit_loss: hasFpl,
    short_floating_profit_loss_usd: fplUsd,
    short_has_floating_profit_loss_usd: hasFplUsd,
  };
}

/** Expiry ms: int / digit-string / ISO from `expiry` field. */
export function parseExpiryMsUtc(g) {
  const raw = g.expiration_timestamp_ms;
  if (raw !== null && raw !== undefined && raw !== "") {
    if (typeof raw === "number" && Number.isFinite(raw)) return Math.round(raw);
    if (typeof raw === "bigint") return Number(raw);
    const s = String(raw).trim();
    if (/^\d+$/.test(s)) {
      const n = Number(s);
      return Number.isFinite(n) ? n : null;
    }
  }
  if (g.expiry) {
    const ms = parseIsoUtcMs(String(g.expiry));
    if (ms !== null) return ms;
  }
  return null;
}

export function openRowDteDays(g) {
  const fromApi = num(g.dte_days) ?? num(g.dte);
  if (fromApi !== null) return fromApi;
  const ms = parseExpiryMsUtc(g);
  if (ms === null) return null;
  return daysUntilUtc(ms);
}

export function optionPutCallLabel(g) {
  const t = String(g.option_type || "").toLowerCase();
  if (t === "call") return "Call";
  if (t === "put") return "Put";
  const n = String(g.short_instrument_name || "");
  if (/-C$/i.test(n) || n.endsWith("-C")) return "Call";
  return "Put";
}

export function updateUnderlyingIndexCache(status, groups) {
  for (const k of ["BTC", "ETH"]) {
    const sv = num(status?.underlying_index_usd?.[k]);
    const gv = num(groups?.underlying_index_usd?.[k]);
    const v = sv > 0 ? sv : gv > 0 ? gv : null;
    if (v !== null) STATE.lastUnderlyingIndexUsd[k] = v;
  }
}

/** Prefer ``/api/status`` (same refresh as portfolio), then ``/api/groups``, then cache. */
export function mergeUnderlyingIndexUsd(status, groups) {
  const out = {};
  for (const k of ["BTC", "ETH"]) {
    const sv = num(status?.underlying_index_usd?.[k]);
    const gv = num(groups?.underlying_index_usd?.[k]);
    const cv = num(STATE.lastUnderlyingIndexUsd[k]);
    const pick = sv > 0 ? sv : gv > 0 ? gv : cv > 0 ? cv : null;
    if (pick !== null) out[k] = pick;
  }
  return out;
}

export function indexUsdUnderlying(status, groups, currency) {
  const cur = String(currency || "").toUpperCase();
  const m = mergeUnderlyingIndexUsd(status, groups);
  return num(m[cur]);
}

/**
 * 帳本幣別（大寫）：持久化列有時缺 ``collateral_currency``，由代號推斷。
 * ``BTC_USDC-`` / ``ETH_USDC-`` 為 USDC 線性；``BTC-`` / ``ETH-`` 逆線為 BTC/ETH。
 */
export function openRowBookCollateralUpper(g) {
  let c = String(g.collateral_currency || "").toUpperCase();
  if (c === "BTC" || c === "ETH" || c === "USDC") return c;
  const n = String(g.short_instrument_name || "");
  if (n.includes("_USDC-")) return "USDC";
  if (n.startsWith("BTC-")) return "BTC";
  if (n.startsWith("ETH-")) return "ETH";
  return String(g.currency || "").toUpperCase() || "BTC";
}

/** Spot USD key for merged ``underlying_index_usd``（僅 BTC/ETH）。 */
export function underlyingIndexKeyForGroup(g) {
  const book = openRowBookCollateralUpper(g);
  if (book === "BTC" || book === "ETH") return book;
  return String(g.currency || "BTC").toUpperCase();
}

/** 同帳任選一筆部位列的 ``index_price``（逆線合併指數掛掉時的退路）。 */
export function openRowFallbackIndexFromPositions(status, g) {
  const book = openRowBookCollateralUpper(g);
  if (book !== "BTC" && book !== "ETH") return null;
  const prefix = book === "BTC" ? "BTC-" : "ETH-";
  const rows = status?.positions;
  if (!rows?.length) return null;
  const account = String(g?.account_name || "");
  for (const row of rows) {
    if (account && String(row.account_name || "") !== account) continue;
    const name = String(row.instrument_name || "");
    const kind = String(row.kind || "").toLowerCase();
    if (!name.startsWith(prefix)) continue;
    if (kind !== "option" && kind !== "future") continue;
    const ix = num(row.index_price);
    if (ix !== null && ix > 0) return ix;
  }
  return null;
}

/**
 * Spot：當前 BTC／ETH 的 USD 指數（與後端 ``underlying_index_usd``／Deribit ``get_index_price`` 同源）。
 * 優先 merged 指數 → 該腿 ``index_price`` → 同帳其他列 ``index_price``。
 */
export function openRowSpotIndexUsdForPnl(g, status, groups) {
  const key = underlyingIndexKeyForGroup(g);
  const polled = num(STATE.lastSpotUsd[key]);
  if (polled !== null && polled > 0) return polled;
  const merged = indexUsdUnderlying(status, groups, key);
  if (merged !== null && merged > 0) return merged;
  const p = openRowPosition(status, g);
  const leg = num(p?.index_price);
  if (leg !== null && leg > 0) return leg;
  const fb = openRowFallbackIndexFromPositions(status, g);
  if (fb !== null && fb > 0) return fb;
  return null;
}

/** USDC 帳本 spot 乘數為 1；逆線用 ``openRowSpotIndexUsdForPnl``。 */
export function openRowSpotUsdScalarForBook(g, status, groups) {
  const coll = openRowBookCollateralUpper(g);
  if (coll === "USDC") return 1;
  if (coll === "BTC" || coll === "ETH") {
    const s = openRowSpotIndexUsdForPnl(g, status, groups);
    return s !== null && s > 0 ? s : null;
  }
  return null;
}

/**
 * 與 Deribit Positions 一致：``(mark_price − average_price) × signed_size``（signed_size 與 Amount 欄同號，空頭為負）。
 * USD 欄優先使用 API ``floating_profit_loss_usd``；缺省時再乘 Spot。
 */
export function openRowPositionSignedSizeForPnl(p) {
  return openRowPositionSignedSizeForDisplay(p);
}

export function openRowLegSignedSizeForDisplay(g, status, role, groups = null) {
  const ctx = groups ?? STATE.groups;
  const groupSize = openRowLegGroupSignedSize(g, role);
  if (groupSize !== null && countOpenGroupsSharingLeg(status, ctx, g, role) > 1) {
    return groupSize;
  }
  const p = openRowLegPosition(status, g, role);
  const signed = openRowPositionSignedSizeForDisplay(p);
  if (signed !== null) return signed;
  return groupSize;
}

export function openRowLegFieldValue(g, status, role, fieldName) {
  if (role === "short" && hasOwn(g, `short_${fieldName}`)) {
    const v = g[`short_${fieldName}`];
    if (v !== null && v !== undefined && v !== "") return v;
  }
  const p = openRowLegPosition(status, g, role);
  return p?.[fieldName] ?? null;
}

export function openRowLegPremiumMtmNative(status, g, role, groups = null) {
  const avg = num(openRowLegFieldValue(g, status, role, "average_price"));
  const mrk = num(openRowLegFieldValue(g, status, role, "mark_price"));
  const sz = openRowLegSignedSizeForDisplay(g, status, role, groups);
  if (avg === null || mrk === null || sz === null) return null;
  return (mrk - avg) * sz;
}

export function openRowLegPnlUsd(status, g, groups, role) {
  const native = openRowLegPremiumMtmNative(status, g, role, groups);
  if (native === null) return null;
  const spot = openRowSpotUsdScalarForBook(g, status, groups);
  if (spot === null || spot <= 0) return null;
  return native * spot;
}

export function openRowPositionPremiumMtmNative(status, g, groups = null) {
  const ctx = groups ?? STATE.groups;
  if (countOpenGroupsSharingLeg(status, ctx, g, "short") > 1) {
    const p = openRowPosition(status, g);
    if (!p) return null;
    const avg = num(p.average_price);
    const mrk = num(p.mark_price);
    const sz = openRowLegGroupSignedSize(g, "short");
    if (avg === null || mrk === null || sz === null) return null;
    return (mrk - avg) * sz;
  }
  const p = openRowPosition(status, g);
  if (!p) return null;
  const avg = num(p.average_price);
  const mrk = num(p.mark_price);
  const sz = openRowPositionSignedSizeForPnl(p);
  if (avg === null || mrk === null || sz === null) return null;
  return (mrk - avg) * sz;
}

/**
 * 結算幣未實現損益（與 Deribit Positions「PNL」同號）。
 * 有 mark/avg/size 時**優先手算** ``(mark−avg)×signed_size``：API 的 ``floating_profit_loss``
 * 與網頁欄位符號常不一致（空頭獲利時 API 可能為負）。僅手算缺資料時才用 API。
 */
export function openRowNativeUnrealizedDisplayValue(g, status) {
  const calc = openRowPositionPremiumMtmNative(status, g);
  if (calc !== null) return calc;
  if (g.short_has_floating_profit_loss) {
    const v = num(g.short_floating_profit_loss);
    if (v !== null) return v;
  }
  return null;
}

export function openRowPositionPnlUsd(status, g, groups) {
  const ctx = groups ?? STATE.groups;
  if (countOpenGroupsSharingLeg(status, ctx, g, "short") > 1) {
    const p = openRowPosition(status, g);
    if (!p) return null;
    const avg = num(p.average_price);
    const mrk = num(p.mark_price);
    const sz = openRowLegGroupSignedSize(g, "short");
    if (avg === null || mrk === null || sz === null) return null;
    const spot = openRowSpotUsdScalarForBook(g, status, groups);
    if (spot === null || spot <= 0) return null;
    return (mrk - avg) * sz * spot;
  }
  const p = openRowPosition(status, g);
  if (!p) return null;
  const avg = num(p.average_price);
  const mrk = num(p.mark_price);
  const sz = openRowPositionSignedSizeForPnl(p);
  if (avg === null || mrk === null || sz === null) return null;
  const spot = openRowSpotUsdScalarForBook(g, status, groups);
  if (spot === null || spot <= 0) return null;
  return (mrk - avg) * sz * spot;
}

/** USD 未實現：優先與網頁一致的手算 ``(mark−avg)×signed_size×spot``，再退回 ``floating_profit_loss_usd``。 */
export function openRowUnrealizedUsdPreferDeribit(g, status, groups) {
  const calc = openRowPositionPnlUsd(status, g, groups);
  if (calc !== null) return calc;
  if (g.short_has_floating_profit_loss_usd) {
    const v = num(g.short_floating_profit_loss_usd);
    if (v !== null) return v;
  }
  return null;
}

/** Engine USDC unrealized: `entry_credit − current_debit`, 2dp + Deribit `($)` style. */
export function fmtUsdcUnrealizedDeribit(usdEstimate) {
  const v = num(usdEstimate);
  if (v === null) return "—";
  const body = new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  }).format(v);
  const sp = '<span class="text-slate-500">';
  const ep = "</span>";
  return `${sp}($)${ep}\u00A0${body}`;
}

export function openRowUnrealizedUsd(g) {
  const u = num(g.unrealized_usdc_estimate);
  if (u !== null) return u;
  const ec = num(g.entry_credit);
  const cd = num(g.current_debit);
  if (ec !== null && cd !== null) return ec - cd;
  return null;
}

export function openRowSpreadLegPnlUsd(status, g, groups) {
  const shortPnl = openRowLegPnlUsd(status, g, groups, "short");
  const longPnl = openRowLegPnlUsd(status, g, groups, "long");
  if (shortPnl === null && longPnl === null) return null;
  return (shortPnl || 0) + (longPnl || 0);
}

/** Bull put: leg PNL 加總（與卡片上兩腿一致）；兩腿缺一則 null，避免與單腿加 0 混淆。 */
export function openRowSpreadLegMtmUsdSumStrict(status, g, groups) {
  const shortPnl = openRowLegPnlUsd(status, g, groups, "short");
  const longPnl = openRowLegPnlUsd(status, g, groups, "long");
  if (shortPnl === null || longPnl === null) return null;
  return shortPnl + longPnl;
}

export function openRowDisplayUnrealizedUsd(g, status, groups) {
  if (strategyId(g) === "bull_put_spread") {
    return (
      openRowSpreadLegMtmUsdSumStrict(status, g, groups) ??
      openRowUnrealizedUsd(g) ??
      openRowSpreadLegPnlUsd(status, g, groups) ??
      openRowUnrealizedUsdPreferDeribit(g, status, groups)
    );
  }
  return openRowUnrealizedUsdPreferDeribit(g, status, groups) ?? openRowUnrealizedUsd(g);
}

export function openRowEntryCreditUsd(g, status, groups) {
  const credit = num(g.entry_credit);
  // Persisted TradeGroup.entry_credit is already USDC-equivalent for both
  // linear USDC and inverse BTC/ETH options.
  return credit;
}

/** PNL 欄：優先 Deribit ``floating_profit_loss``，否則 ``(mark−avg)×signed_size``（結算幣，不乘 spot）。 */
export function fmtUnrealizedCoinNativeDisplay(g, status) {
  const coll = openRowBookCollateralUpper(g);
  const mtm = openRowNativeUnrealizedDisplayValue(g, status);
  return fmtNativeUnrealizedDisplay(mtm, coll);
}

export function fmtNativeUnrealizedDisplay(mtm, collateralCurrency) {
  const coll = String(collateralCurrency || "").toUpperCase();
  if (coll === "USDC") {
    if (mtm === null) return "—";
    return fmtUsdcUnrealizedDeribit(mtm);
  }
  if (mtm === null) return "—";
  if (coll === "BTC") return `<span class="text-slate-500">₿</span>\u00A0${fmtNum(mtm, 8)}`;
  if (coll === "ETH") return `<span class="text-slate-500">♦</span>\u00A0${fmtNum(mtm, 8)}`;
  return fmtNum(mtm, 8);
}

export function openRowDisplayNativeUnrealizedValue(g, status, groups) {
  if (strategyId(g) !== "bull_put_spread") return openRowNativeUnrealizedDisplayValue(g, status);
  const native = num(g.unrealized_coin_native);
  if (native !== null) return native;
  const usd = openRowDisplayUnrealizedUsd(g, status, groups);
  const spot = openRowSpotUsdScalarForBook(g, status, groups);
  if (usd === null || spot === null || spot <= 0) return null;
  return usd / spot;
}

export function fmtNum(value, places = 4) {
  const n = num(value);
  if (n === null) return "—";
  return (places >= 8 ? fmt.num8 : fmt.num4).format(n);
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

export function hasOwn(obj, key) {
  return Object.prototype.hasOwnProperty.call(obj || {}, key);
}

export function bookEquityUsdForDisplay(book, status) {
  const b = String(book || "").toUpperCase();
  const portfolio = status?.portfolio || {};
  const fromPortfolio = num(portfolio?.equity_by_book?.[b]);
  if (fromPortfolio !== null) return fromPortfolio;
  const native = num(status?.accounts?.[b]?.equity);
  if (native === null) return null;
  if (b === "USDC") return native;
  const spot = num(status?.underlying_index_usd?.[b]) ?? num(STATE.lastSpotUsd?.[b]);
  if (spot === null || spot <= 0) return null;
  return native * spot;
}

/** Per-book equity in USDC equivalent (from ``equity_by_book`` or native × index). */
export function bookEquityUsdByBook(status) {
  const out = {};
  for (const book of CORE_BOOKS) {
    out[book] = bookEquityUsdForDisplay(book, status);
  }
  return out;
}

// Match USDC equity / day-start: include spot MTM (and exclude external flows).
// ``day_pnl_usdc_ex_flow_ex_spot`` is for native-book risk gates only.
export function portfolioDayPnlUsdForDisplay(portfolio, totalEquity, dayStart) {
  const netFlow = num(portfolio?.day_net_flow_usdc);
  return (
    num(portfolio?.day_pnl_usdc_ex_flow) ??
    num(portfolio?.day_pnl_usdc_ex_flow_ex_spot) ??
    (totalEquity !== null && dayStart !== null
      ? totalEquity - dayStart - (netFlow ?? 0)
      : null)
  );
}

export function bookDayPnlUsdForDisplay(book, status, equityUsdc, dayStartUsdc) {
  const b = String(book || "").toUpperCase();
  const portfolio = status?.portfolio || {};
  const netFlow = num(portfolio?.day_net_flow_usdc_by_book?.[b]);
  return (
    num(portfolio?.day_pnl_usdc_ex_flow_by_book?.[b]) ??
    num(portfolio?.day_pnl_usdc_ex_flow_ex_spot_by_book?.[b]) ??
    (equityUsdc !== null && dayStartUsdc !== null
      ? equityUsdc - dayStartUsdc - (netFlow ?? 0)
      : null)
  );
}

export function pnlClass(value) {
  const n = num(value);
  if (n === null || n === 0) return "";
  return n > 0 ? "pnl-pos" : "pnl-neg";
}

export function fmtTime(msOrIso) {
  return formatTimeLocal(msOrIso);
}

export function fmtDate(msOrIso) {
  return formatDateLocal(msOrIso);
}

/** Report window: rolling close filter + fixed N-day APR denominator (see realized_summary). */
export function rollingWindowProfitLabel(days) {
  const n = Math.round(days ?? 30);
  return i18n(`Total profit (rolling ${n}d)`, `已實現損益（滾動 ${n} 日視窗）`);
}

export function rollingWindowAprLabel(days) {
  const n = Math.round(days ?? 30);
  return i18n(`Realized APR (rolling ${n}d)`, `已實現年化（滾動 ${n} 日視窗）`);
}

export function rollingWindowPnlHint(days) {
  const n = Math.round(days ?? 30);
  return i18n(`Closes in last ${n}d only`, `僅計最近 ${n} 日內平倉`);
}

export function rollingWindowAprHint(days) {
  const n = Math.round(days ?? 30);
  return i18n(
    `Last ${n}d closes ÷ ledger total equity`,
    `近 ${n} 日平倉 ÷ 當日總權益`
  );
}

/** Live portfolio equity for rolling APR denominator (matches engine effective capital). */
export function aprEffectiveCapitalUsdc() {
  const eq = num(STATE.status?.portfolio?.total_equity_usdc);
  return eq !== null && eq > 0 ? eq : null;
}

export function closedTimestampMs(g) {
  const ms = num(g.closed_timestamp_ms);
  if (ms !== null) return ms;
  if (g.closed_timestamp) {
    const ms = parseIsoUtcMs(String(g.closed_timestamp));
    if (ms !== null) return ms;
  }
  return null;
}

export function openPositionTitle(g) {
  const ccy = String(g.currency || "").toUpperCase() || "Option";
  const id = strategyId(g);
  if (id === "bull_put_spread") {
    return INVESTOR_ZH ? `${ccy} 賣權價差` : `${ccy} put spread`;
  }
  const side = optionPutCallLabel(g);
  if (INVESTOR_ZH) {
    const sideZh = side.toLowerCase() === "call" ? "買權" : "賣權";
    return `${ccy} 賣出${sideZh}`;
  }
  return `${ccy} short ${side.toLowerCase()}`;
}

export function fmtNativeBookBreakdown(byBook, { places = { BTC: 5, ETH: 4, USDC: 2 }, pnl = false } = {}) {
  const symbols = { BTC: "₿", ETH: "♦", USDC: "($)" };
  const items = ["BTC", "ETH", "USDC"].map((book) => {
    const n = num(byBook[book]);
    const text = n === null ? "—" : fmtNum(n, places[book] ?? 4);
    const cls = pnl ? ` ${pnlClass(byBook[book])}` : "";
    return `<span class="native-book-item"><span class="native-book-symbol text-slate-500">${symbols[book]}</span> <span class="font-mono tabular-nums${cls}">${text}</span></span>`;
  });
  return `<span class="native-book-breakdown">${items.join("")}</span>`;
}

export function fmtLifetimeRealizedNativeBreakdown(byBook) {
  return fmtNativeBookBreakdown(byBook, { pnl: true });
}

export function fmtBookEquityNativeBreakdown(byBook) {
  return fmtNativeBookBreakdown(byBook);
}

/** Total-equity KPI: per-book native amount and USDC equivalent (matches book cards). */
export function fmtBookEquityDualBreakdown(nativeByBook, usdByBook) {
  const symbols = { BTC: "₿", ETH: "♦", USDC: "($)" };
  const places = { BTC: 5, ETH: 4, USDC: 2 };
  const items = ["BTC", "ETH", "USDC"]
    .map((book) => {
      const native = num(nativeByBook?.[book]);
      const usd = num(usdByBook?.[book]);
      if (native === null && usd === null) return null;
      if (book === "USDC") {
        const v = usd ?? native;
        if (v === null) return null;
        return `<div class="book-equity-dual-row">
          <span class="native-book-symbol text-slate-500">${symbols[book]}</span>
          <span class="font-mono tabular-nums">${fmtUsd(v)}</span>
        </div>`;
      }
      const nativeStr = native === null ? "—" : fmtNum(native, places[book]);
      const usdStr = usd === null ? "—" : fmtUsd(usd);
      return `<div class="book-equity-dual-row">
        <span class="native-book-symbol text-slate-500">${symbols[book]}</span>
        <span class="font-mono tabular-nums">${nativeStr}</span>
        <span class="book-equity-dual-sep text-slate-600" aria-hidden="true">·</span>
        <span class="font-mono tabular-nums text-slate-400">${usdStr}</span>
      </div>`;
    })
    .filter(Boolean);
  if (!items.length) return `<span class="text-slate-500">—</span>`;
  return `<div class="book-equity-dual-breakdown">${items.join("")}</div>`;
}

export function fmtOpenCreditStrategyBreakdown(byStrategy) {
  const rows = strategyOrder(new Set(STRATEGIES.map((s) => s.id))).map((id) => {
    const short = escapeHtml(strategyInfo(id).short);
    const n = num(byStrategy[id]);
    const text = n === null ? "—" : fmtUsd(n);
    return `<div class="open-credit-row"><span class="open-credit-label text-slate-500">${short}</span><span class="open-credit-value font-mono tabular-nums text-slate-300">${text}</span></div>`;
  });
  return `<div class="open-credit-breakdown">${rows.join("")}</div>`;
}

export function realizedSummaryUrl(days = 30) {
  let url = `/api/realized_summary?days=${days}`;
  const cap = aprEffectiveCapitalUsdc();
  if (cap !== null) {
    url += `&effective_capital_usdc=${encodeURIComponent(String(cap))}`;
  }
  return url;
}

export function dashboardBundleUrl(days = 30, { sections = null } = {}) {
  let url = `/api/dashboard_bundle?days=${days}`;
  if (sections) {
    url += `&sections=${encodeURIComponent(sections)}`;
  }
  const cap = aprEffectiveCapitalUsdc();
  if (cap !== null) {
    url += `&effective_capital_usdc=${encodeURIComponent(String(cap))}`;
  }
  return url;
}

export function applyDashboardBundlePayload(d) {
  if (d?.groups) STATE.groups = d.groups;
  if (d?.status) {
    STATE.status = d.status;
    STATE.statusErrorOnce = false;
    STATE.dataFreshness.source = "live";
    STATE.dataFreshness.live = true;
    STATE.dataFreshness.statusMs = 0;
  }
  if (d?.realized_summary) STATE.report = d.realized_summary;
}

/** Earliest entry among realized closed groups (lifetime APR sample start). */
export function lifetimePerformanceStartMs(report, groups) {
  let min = null;
  const consider = (g) => {
    if (!g || num(g.realized_pnl) === null) return;
    if (!isDisplayableClosedTradeGroup(g, STATE.status, groups)) return;
    const entry = entryTimestampMs(g);
    if (entry === null || entry <= 0) return;
    if (min === null || entry < min) min = entry;
  };
  for (const g of groups?.closed || []) consider(g);
  for (const g of report?.recent_closed_trades || []) consider(g);
  return min;
}

export function looksLikeCoveredCallRow(g) {
  if (!g || optionPutCallLabel(g).toLowerCase() !== "call") return false;
  const covered = num(g.covered_underlying_quantity);
  if (covered !== null && covered > 0) return true;
  if (String(g.short_label || "").startsWith("covered_call-")) return true;
  if (String(g.account_name || "") === "covered_call") return true;
  return String(g.account_env_file || "").includes(".env.covered_call");
}

export function normalizeStrategyId(raw) {
  const normalized = String(raw || "").trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
  if (!normalized) return "";
  const aliases = {
    naked: "naked_short",
    naked_put: "naked_short",
    naked_call: "naked_short",
    short_put: "naked_short",
    short_call: "naked_short",
    shortput: "naked_short",
    shortcall: "naked_short",
    naked_short_put: "naked_short",
    naked_short_call: "naked_short",
    put_spread: "bull_put_spread",
    short_put_spread: "bull_put_spread",
    bullputspread: "bull_put_spread",
    bull_put: "bull_put_spread",
    coveredcall: "covered_call",
  };
  return aliases[normalized] || normalized;
}

export function strategyId(g) {
  const raw = normalizeStrategyId(g?.strategy);
  const hasLongLeg = String(g?.long_instrument_name || "").trim();
  if ((raw === "" || raw === "naked_short") && hasLongLeg && optionPutCallLabel(g).toLowerCase() === "put") {
    return "bull_put_spread";
  }
  if (raw === "naked_short" && looksLikeCoveredCallRow(g)) return "covered_call";
  if (raw) return raw;
  const side = optionPutCallLabel(g).toLowerCase();
  if (side === "call" && looksLikeCoveredCallRow(g)) return "covered_call";
  return "naked_short";
}

export function strategyInfo(id) {
  const key = normalizeStrategyId(id);
  if (STRATEGY_BY_ID[key]) {
    const base = STRATEGY_BY_ID[key];
    if (!INVESTOR || !INVESTOR_ZH) return base;
    return {
      ...base,
      title: base.titleZh || base.title,
      short: base.shortZh || base.short,
      chipShort: base.chipShortZh || base.chipShort || base.shortZh || base.short,
      description: base.descriptionZh || base.description,
    };
  }
  const label = key ? key.replaceAll("_", " ") : "—";
  return {
    id: key || "",
    title: label,
    short: label,
    chipShort: label,
    accentClass: "border-slate-700",
    description: "",
  };
}

export function strategyTitle(id) {
  return strategyInfo(id).title;
}

export function strategyChipClass(id) {
  const key = normalizeStrategyId(id);
  if (key === "naked_short") return "chip-strategy-naked";
  if (key === "bull_put_spread") return "chip-strategy-spread";
  if (key === "covered_call") return "chip-strategy-covered";
  return "chip-strategy-unknown";
}

export function strategyChipHtml(id, { compact = false } = {}) {
  const info = strategyInfo(id);
  const cls = strategyChipClass(info.id || id);
  const label = compact ? info.chipShort || info.short : info.short;
  const compactClass = compact ? " chip--compact" : "";
  return `<span class="chip ${cls}${compactClass}">${escapeHtml(label)}</span>`;
}

export function tradeGroupKey(g) {
  return [
    String(g?.account_name || ""),
    String(g?.group_id || ""),
    String(g?.short_instrument_name || ""),
  ].join("\u0000");
}

const TRADE_GROUP_ENRICH_KEYS = [
  "realized_pnl_collateral_native",
  "short_entry_average_price",
  "short_close_average_price",
  "entry_index_usd",
  "close_index_usd",
  "realized_close_debit",
  "realized_close_fee",
  "entry_fee",
  "entry_credit",
  "collateral_currency",
  "strategy",
  "option_type",
  "covered_underlying_quantity",
  "realized_apr_on_equity",
  "close_book_equity",
  "quantity",
  "realized_pnl",
  "contract_size",
  "short_strike",
];

export function hasTradeGroupValue(v) {
  if (v === null || v === undefined || v === "") return false;
  if (typeof v === "number" && !Number.isFinite(v)) return false;
  return true;
}

/** Prefer ``groups.closed`` (a) enrich fields over ``report`` rows (b) when both exist. */
export function mergeTradeGroupRow(a, b) {
  const out = { ...b, ...a };
  for (const key of TRADE_GROUP_ENRICH_KEYS) {
    if (hasTradeGroupValue(a[key])) out[key] = a[key];
    else if (hasTradeGroupValue(b[key])) out[key] = b[key];
  }
  return out;
}

export function dedupeTradeGroups(rows) {
  const byKey = new Map();
  for (const g of rows || []) {
    const key = tradeGroupKey(g);
    const prev = byKey.get(key);
    byKey.set(key, prev ? mergeTradeGroupRow(prev, g) : g);
  }
  return [...byKey.values()];
}

export function isOpenTradeGroup(g) {
  const st = String(g?.status || "open").toLowerCase();
  return st !== "closed";
}

export function isClosedTradeGroup(g) {
  const st = String(g?.status || "").toLowerCase();
  if (st === "closed") return true;
  return closedTimestampMs(g) !== null;
}

/** ``reconciled_external`` glitch: same short leg still open within 5 minutes of entry. */
const PHANTOM_RECONCILE_MAX_HOLDING_MS = 300_000;

export function openShortInstrumentNames(status, groups) {
  const names = new Set();
  for (const g of currentOpenRows(status, groups)) {
    const name = String(g?.short_instrument_name || "").trim();
    if (name) names.add(name);
  }
  return names;
}

export function isPhantomReconcileClose(g, status, groups) {
  if (!isClosedTradeGroup(g)) return false;
  if (String(g?.close_reason || "").toLowerCase() !== "reconciled_external") return false;
  const entry = entryTimestampMs(g);
  const closed = closedTimestampMs(g);
  if (entry === null || closed === null || closed <= entry) return false;
  if (closed - entry > PHANTOM_RECONCILE_MAX_HOLDING_MS) return false;
  const short = String(g?.short_instrument_name || "").trim();
  if (!short) return false;
  return openShortInstrumentNames(status, groups).has(short);
}

export function isDisplayableClosedTradeGroup(g, status, groups) {
  return isClosedTradeGroup(g) && !isPhantomReconcileClose(g, status, groups);
}

export function currentOpenRows(status, groups) {
  const out = [];
  const seen = new Set();
  for (const g of status?.trade_groups || []) {
    if (!isOpenTradeGroup(g)) continue;
    const key = tradeGroupKey(g);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(g);
  }
  for (const g of groups?.open || []) {
    if (!isOpenTradeGroup(g)) continue;
    const key = tradeGroupKey(g);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(g);
  }
  return out.map((g) => enrichOpenGroupRow(status, g, groups));
}

export function mergedClosedRows(report, groups, limit = 20, status = null) {
  const st = status ?? STATE.status;
  const rows = dedupeTradeGroups([
    ...(groups?.closed || []),
    ...(report?.recent_closed_trades || []),
  ]).filter((g) => isDisplayableClosedTradeGroup(g, st, groups));
  rows.sort((a, b) => (closedTimestampMs(b) || 0) - (closedTimestampMs(a) || 0));
  return rows.slice(0, limit);
}

export function closedRowsForStrategyStats(report, groups) {
  return mergedClosedRows(report, groups, 500);
}

export function strategyOrder(ids) {
  const known = STRATEGIES.map((s) => s.id);
  const ordered = known.filter((id) => ids.has(id));
  const unknown = [...ids].filter((id) => !known.includes(id)).sort();
  return ordered.concat(unknown);
}

export function strikeFromInstrumentName(instrumentName) {
  const match = String(instrumentName || "").match(/-([0-9]+(?:\.[0-9]+)?)-[CP]$/i);
  if (!match) return null;
  return num(match[1]);
}

export function openRowLegStrike(g, role) {
  const explicit = role === "long" ? num(g?.long_strike) : num(g?.short_strike);
  if (explicit !== null) return explicit;
  return strikeFromInstrumentName(openRowLegInstrumentName(g, role));
}

export function fmtStrike(value) {
  const v = num(value);
  if (v === null) return "—";
  return fmtUsd(v, 0);
}

export function bullPutSpreadWidth(g) {
  const shortStrike = openRowLegStrike(g, "short");
  const longStrike = openRowLegStrike(g, "long");
  if (shortStrike === null || longStrike === null) return null;
  return shortStrike - longStrike;
}

export function openRowLegPriceGap(g, status, fieldName) {
  const shortPrice = num(openRowLegFieldValue(g, status, "short", fieldName));
  const longPrice = num(openRowLegFieldValue(g, status, "long", fieldName));
  if (shortPrice === null || longPrice === null) return null;
  return shortPrice - longPrice;
}

export function strategyLegDetail(g) {
  const longLeg = String(g?.long_instrument_name || "").trim();
  if (longLeg) return i18n(`Long ${longLeg}`, `買腿 ${longLeg}`);
  const covered = num(g?.covered_underlying_quantity);
  if (covered !== null && covered > 0) {
    return i18n(
      `Covered ${fmtNum(covered, 4)} ${String(g.currency || "").toUpperCase()}`,
      `備兌 ${fmtNum(covered, 4)} ${String(g.currency || "").toUpperCase()}`
    );
  }
  return i18n("Single short leg", "單邊賣出");
}

export function accountHint(g) {
  const account = String(g?.account_name || "").trim();
  return account ? `Account ${account}` : "";
}

export function groupHoldingDays(g) {
  const explicit = num(g?.holding_days);
  if (explicit !== null) return explicit;
  const closed = closedTimestampMs(g);
  const entry = entryTimestampMs(g);
  if (closed === null || entry === null || entry <= 0) return null;
  return Math.max(closed - entry, 0) / 86_400_000;
}

export function entryTimestampMs(g) {
  const ms = num(g?.entry_timestamp_ms);
  if (ms !== null) return ms;
  if (g?.entry_timestamp) {
    const ms = parseIsoUtcMs(String(g.entry_timestamp));
    if (ms !== null) return ms;
  }
  return null;
}

export function groupEntryDteDaysAtOpen(g) {
  const entry = entryTimestampMs(g);
  const exp = parseExpiryMsUtc(g);
  if (entry === null || exp === null || exp <= entry) return null;
  return (exp - entry) / 86_400_000;
}

export function bookEquityNative(status, book) {
  const b = String(book || "USDC").toUpperCase();
  const eq = num(status?.accounts?.[b]?.equity);
  if (eq === null || eq <= 0) return null;
  return eq;
}

/** APR 分母帳本：逆線 BTC/ETH、線性 USDC（與 ``openRowBookCollateralUpper`` 一致，非標的 ``currency``）。 */
export function tradeGroupAprBook(g) {
  return openRowBookCollateralUpper(g);
}

export function accountStatusRow(status, accountName) {
  const name = String(accountName || "").trim();
  if (!name) return null;
  return (status?.account_statuses || []).find((r) => String(r.name || "") === name) || null;
}

/**
 * 該策略子帳戶在對應帳本上的 equity（原生單位：BTC 為 BTC 數量、USDC 為美元）。
 * ``portfolio.equity_by_book`` 對 BTC/ETH 是 USDC 市值，不可作 APR 分母。
 */
export function strategyBookEquityNative(g, status) {
  const book = tradeGroupAprBook(g);
  const acct = accountStatusRow(status, g?.account_name);
  const fromAcctNative = num(acct?.accounts?.[book]?.equity);
  if (fromAcctNative !== null && fromAcctNative > 0) return fromAcctNative;
  if (book === "USDC") {
    const fromAcctUsd = num(acct?.portfolio?.equity_by_book?.[book]);
    if (fromAcctUsd !== null && fromAcctUsd > 0) return fromAcctUsd;
    const total = num(acct?.portfolio?.total_equity_usdc);
    if (total !== null && total > 0) return total;
  }
  if (!acct) {
    const fromAccounts = num(status?.accounts?.[book]?.equity);
    if (fromAccounts !== null && fromAccounts > 0) return fromAccounts;
  }
  if (book === "USDC") {
    const fromPortfolio = num(status?.portfolio?.equity_by_book?.[book]);
    if (fromPortfolio !== null && fromPortfolio > 0) return fromPortfolio;
  }
  return null;
}

export function collateralBookSpotUsd(g, status) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return null;
  return (
    num(status?.underlying_index_usd?.[book]) ??
    num(STATE.groups?.underlying_index_usd?.[book]) ??
    num(STATE.lastSpotUsd?.[book]) ??
    num(g?.close_index_usd)
  );
}

/** 逆線期權：幣本位已實現損益 = entry_amount − exit_amount − fee（ETH/BTC）。 */
export function realizedPnlCoinNative(g, status) {
  const stored = num(g?.realized_pnl_collateral_native);
  if (stored !== null) return stored;
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return num(g?.realized_pnl);
  const qty = num(g?.quantity);
  if (qty === null || qty <= 0) return null;
  let idxEntry = num(g?.entry_index_usd);
  let idxClose = num(g?.close_index_usd) ?? idxEntry;
  const entryFee = num(g?.entry_fee) ?? 0;
  const closeFee = num(g?.realized_close_fee) ?? 0;
  let entryAmount = null;
  let exitAmount = null;
  const entryPx = num(g?.short_entry_average_price);
  const closePx = num(g?.short_close_average_price);
  const entryCredit = num(g?.entry_credit);
  let closeDebit = num(g?.realized_close_debit);
  if (entryPx !== null && entryPx > 0) {
    entryAmount = entryPx * qty;
    if ((idxEntry === null || idxEntry <= 0) && entryCredit !== null) {
      idxEntry = (entryCredit + entryFee) / (entryPx * qty);
    }
  } else if (entryCredit !== null && idxEntry !== null && idxEntry > 0) {
    entryAmount = (entryCredit + entryFee) / idxEntry;
  }
  if (closePx !== null && closePx > 0) {
    exitAmount = closePx * qty;
    if ((idxClose === null || idxClose <= 0) && closeDebit !== null) {
      idxClose = Math.max(0, closeDebit - closeFee) / (closePx * qty);
    }
  } else if (closeDebit !== null && idxClose !== null && idxClose > 0) {
    exitAmount = Math.max(0, closeDebit - closeFee) / idxClose;
  }
  if (entryAmount === null || exitAmount === null) return null;
  let fees = 0;
  if (entryFee > 0) {
    if (idxEntry === null || idxEntry <= 0) return null;
    fees += entryFee / idxEntry;
  }
  if (closeFee > 0) {
    if (idxClose === null || idxClose <= 0) return null;
    fees += closeFee / idxClose;
  }
  return entryAmount - exitAmount - fees;
}

export function isInverseCoinBookGroup(g) {
  const book = tradeGroupAprBook(g);
  return book === "BTC" || book === "ETH";
}

/** 逆線：USDC 標記 = 幣本位 × 現價（不用平倉指數）；USDC 帳本直接用 stored USDC。 */
export function realizedPnlDisplayUsdc(g, status) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return num(g?.realized_pnl);
  const native = realizedPnlCoinNative(g, status);
  const spot = collateralBookSpotUsd(g, status);
  if (native !== null && spot !== null && spot > 0) return native * spot;
  return null;
}

/** 已實現損益換成 APR 帳本原生單位（優先幣本位，legacy 才 ÷ 指數）。 */
export function realizedPnlInAprBookNative(g, status) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return num(g?.realized_pnl);
  const native = realizedPnlCoinNative(g, status);
  if (native !== null) return native;
  const pnlUsd = num(g?.realized_pnl);
  if (pnlUsd === null) return null;
  const idx =
    num(g?.close_index_usd) ??
    num(status?.underlying_index_usd?.[book]) ??
    num(STATE.groups?.underlying_index_usd?.[book]) ??
    num(STATE.lastSpotUsd?.[book]);
  if (idx === null || idx <= 0) return null;
  return pnlUsd / idx;
}

/** APR 分母：每張合約名目（與 trade_apr.opened_contract_amount_per_contract 一致）。 */
export function tradeGroupContractSize(g) {
  const cs = num(g?.contract_size);
  return cs !== null && cs > 0 ? cs : 1;
}

export function tradeGroupOpenedAmountPerContract(g, status) {
  const qty = num(g?.quantity);
  if (qty === null || qty <= 0) return null;
  const cs = tradeGroupContractSize(g);
  const strat = strategyId(g);
  const imColl = num(g?.estimated_im_collateral);
  if (strat === "bull_put_spread" && imColl !== null && imColl > 0) return imColl / qty;
  const book = tradeGroupAprBook(g);
  if (book === "USDC") {
    const opt = optionPutCallLabel(g).toLowerCase();
    if (opt === "call") {
      const idx =
        usdcLinearUnderlyingIndexUsd(g, status) ??
        entryIndexUsdForGroup(g, status) ??
        collateralBookSpotUsd(g, status) ??
        closeIndexUsdForGroup(g, status);
      if (idx !== null && idx > 0) return idx;
    } else {
      const strike = openRowLegStrike(g, "short");
      if (strike !== null && strike > 0) return strike;
    }
    return null;
  }
  return cs;
}

export function tradeGroupOpenedNotional(g, status) {
  const per = tradeGroupOpenedAmountPerContract(g, status);
  const qty = num(g?.quantity);
  if (per === null || qty === null || qty <= 0) return null;
  const strat = strategyId(g);
  if (strat === "covered_call") {
    const cover = num(g?.covered_underlying_quantity);
    return cover !== null && cover > 0 ? cover : qty;
  }
  const book = tradeGroupAprBook(g);
  if (book === "USDC" || strat === "bull_put_spread") return per * qty;
  return per * qty;
}

export function tradeGroupAprCapitalBase(g, status) {
  return tradeGroupOpenedNotional(g, status);
}

export function annualizedAprOnPositionCapital(g, status) {
  const pnlN = realizedPnlInAprBookNative(g, status);
  const holding = groupHoldingDays(g);
  const capital = tradeGroupAprCapitalBase(g, status);
  if (pnlN === null || capital === null || capital <= 0 || holding === null || holding <= 0) {
    return null;
  }
  return (pnlN / capital) * (365 / holding);
}

export function annualizedAprOnBookEquity(g, status, equityNative) {
  return annualizedAprOnPositionCapital(g, status);
}

/** 逆線：USDC 標記 = 幣本位 × 現價；USDC 帳本直接用 stored USDC。 */
export function fmtRealizedPnlDisplay(g, status) {
  const book = tradeGroupAprBook(g);
  if (!isInverseCoinBookGroup(g)) {
    const pnlUsd = num(g?.realized_pnl);
    return pnlUsd === null ? "—" : fmtUsd(pnlUsd);
  }
  const native = realizedPnlCoinNative(g, status);
  if (native === null) {
    const pnlUsd = num(g?.realized_pnl);
    return pnlUsd === null ? "—" : fmtUsd(pnlUsd);
  }
  const usdc = realizedPnlDisplayUsdc(g, status);
  const places = book === "BTC" ? 5 : 4;
  const nativeStr = `${fmtNum(native, places)} ${book}`;
  return INVESTOR_ZH
    ? `${fmtUsd(usdc)}（${nativeStr}）`
    : `${fmtUsd(usdc)} (${nativeStr})`;
}

export function fmtNativeBookAmount(native, book) {
  const n = num(native);
  if (n === null) return `— ${book || ""}`.trim();
  const body = new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 }).format(n);
  return `${body} ${book}`;
}

export function fmtUsdWithNativeBookAmount(usd, native, book) {
  const usdStr = fmtUsd(usd);
  if (native === null || !book || book === "USDC") return usdStr;
  const nativeStr = fmtNativeBookAmount(native, book);
  return INVESTOR_ZH ? `${usdStr}（${nativeStr}）` : `${usdStr} (${nativeStr})`;
}

/** Open-position KPI tiles: USDC on first row, native book amount on second. */
export function fmtUsdNativeBookStackHtml(usd, native, book) {
  const usdStr = fmtUsd(usd);
  if (native === null || !book || book === "USDC") return usdStr;
  const nativeStr = escapeHtml(fmtNativeBookAmount(native, book));
  return `<span class="open-position-value-stack"><span class="open-position-value-line">${usdStr}</span><span class="open-position-value-sub">${nativeStr}</span></span>`;
}

export function nativeFromUsdAtIndex(usd, indexUsd) {
  const v = num(usd);
  const idx = num(indexUsd);
  if (v === null || idx === null || idx <= 0) return null;
  return v / idx;
}

export function entryIndexUsdForGroup(g, status) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return null;
  return (
    num(g?.entry_index_usd) ??
    num(status?.underlying_index_usd?.[book]) ??
    num(STATE.groups?.underlying_index_usd?.[book]) ??
    num(STATE.lastSpotUsd?.[book])
  );
}

export function closeIndexUsdForGroup(g, status) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return null;
  return (
    num(g?.close_index_usd) ??
    num(status?.underlying_index_usd?.[book]) ??
    num(STATE.groups?.underlying_index_usd?.[book]) ??
    num(STATE.lastSpotUsd?.[book]) ??
    num(g?.entry_index_usd)
  );
}

/** USDC 線性期權：標的現价（BTC/ETH index），call APR 分母用。 */
export function usdcLinearUnderlyingIndexUsd(g, status) {
  const key = underlyingIndexKeyForGroup(g);
  if (key !== "BTC" && key !== "ETH") return null;
  const candidates = [
    num(g?.entry_index_usd),
    num(g?.close_index_usd),
    num(status?.underlying_index_usd?.[key]),
    num(STATE.groups?.underlying_index_usd?.[key]),
    num(STATE.lastSpotUsd?.[key]),
    openRowLegStrike(g, "short"),
  ];
  for (const value of candidates) {
    if (value !== null && value > 100) return value;
  }
  return null;
}

/** Entry net APR: persisted at open (per-contract round-trip), else estimate from fill premium. */
/**
 * Closed-trade APR: prefer backend-enriched ``realized_apr_on_equity`` (recomputed on
 * each /api/groups load); fall back to client recompute when missing.
 */
export function groupRealizedApr(g, status) {
  const holding = groupHoldingDays(g);
  if (holding === null || holding <= 0) return null;
  const stored = num(g?.realized_apr_on_equity) ?? num(g?.realized_annualized_return);
  if (stored !== null) return stored;
  return annualizedAprOnPositionCapital(g, status);
}

export function activityAmountDisplay(g, status, groups = null) {
  const ctx = groups ?? STATE.groups;
  const id = strategyId(g);
  if (id === "bull_put_spread") {
    const shortAmt = openRowLegSignedSizeForDisplay(g, status, "short", ctx);
    const longAmt = openRowLegSignedSizeForDisplay(g, status, "long", ctx);
    if (shortAmt === null && longAmt === null) {
      const q = num(g.quantity);
      if (q === null) return null;
      return `${fmtNum(-Math.abs(q), 4)} / ${fmtNum(Math.abs(q), 4)}`;
    }
    const parts = [];
    if (shortAmt !== null) parts.push(fmtNum(shortAmt, 4));
    if (longAmt !== null) parts.push(fmtNum(longAmt, 4));
    return parts.length ? parts.join(" / ") : null;
  }
  if (!isClosedTradeGroup(g)) {
    const shown = fmtShortAmountDisplay(g, status, ctx);
    return shown === "—" ? null : shown;
  }
  const q = num(g.quantity);
  if (q === null) return null;
  return fmtNum(-Math.abs(q), 4);
}

export function groupEntryNetCreditAtOpen(g, status) {
  const credit = num(g?.entry_credit);
  if (credit === null) return null;
  const fee = num(g?.entry_fee) ?? 0;
  const book = tradeGroupAprBook(g);
  const prem = num(g?.short_entry_average_price);
  const qty = num(g?.quantity);
  const idx = entryIndexUsdForGroup(g, status);
  let netUsdc = credit;
  if (fee > 0 && prem !== null && prem > 0 && qty !== null && qty > 0 && idx !== null && idx > 0) {
    const gross = prem * qty * idx;
    const tol = Math.max(0.01, Math.abs(gross) * 0.001);
    if (Math.abs(gross - credit) <= tol) netUsdc = credit - fee;
    else if (Math.abs(gross - (credit + fee)) <= tol) netUsdc = credit;
  }
  if (book === "USDC") return netUsdc;
  if (idx === null || idx <= 0) return null;
  return netUsdc / idx;
}

export function groupEntryNetApr(g, status) {
  const dte = groupEntryDteDaysAtOpen(g);
  const opened = tradeGroupOpenedNotional(g, status);
  const net = groupEntryNetCreditAtOpen(g, status);
  if (
    net === null ||
    net <= 0 ||
    dte === null ||
    dte <= 0 ||
    opened === null ||
    opened <= 0
  ) {
    return num(g?.entry_net_apr);
  }
  return (net / opened) * (365 / dte);
}

export function groupEntryFeeUsd(g) {
  return num(g?.entry_fee);
}

export function groupEntryFeeNative(g, status) {
  return nativeFromUsdAtIndex(groupEntryFeeUsd(g), entryIndexUsdForGroup(g, status));
}

export function groupCloseFeeUsd(g) {
  const openEst = num(g?.current_close_fee);
  if (openEst !== null && openEst > 0) return openEst;
  return num(g?.realized_close_fee);
}

export function groupCloseFeeNative(g, status) {
  const openEst = num(g?.current_close_fee);
  const index = openEst !== null && openEst > 0 ? collateralBookSpotUsd(g, status) : closeIndexUsdForGroup(g, status);
  return nativeFromUsdAtIndex(groupCloseFeeUsd(g), index);
}

export function groupEntryCreditUsd(g, status, groups) {
  return openRowEntryCreditUsd(g, status, groups);
}

export function groupEntryCreditNative(g, status) {
  return nativeFromUsdAtIndex(num(g?.entry_credit), entryIndexUsdForGroup(g, status));
}

export function allTradeGroupsForActivity(status, groups) {
  const rows = [];
  const seen = new Set();
  const add = (g) => {
    if (!g) return;
    const key = tradeGroupKey(g);
    if (seen.has(key)) return;
    seen.add(key);
    rows.push(g);
  };
  for (const g of status?.trade_groups || []) add(g);
  for (const g of groups?.open || []) add(g);
  for (const g of groups?.closed || []) add(g);
  return rows;
}

export function activityOpenRows(status, groups) {
  return dedupeTradeGroups(allTradeGroupsForActivity(status, groups))
    .filter((g) => isOpenTradeGroup(g))
    .sort((a, b) => (entryTimestampMs(b) || 0) - (entryTimestampMs(a) || 0));
}

export function activityClosedRows(status, report, groups) {
  return mergedClosedRows(report, groups, 500, status);
}

export function paginateRows(rows, page, pageSize) {
  const total = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const startIdx = (safePage - 1) * pageSize;
  return {
    rows: rows.slice(startIdx, startIdx + pageSize),
    page: safePage,
    totalPages,
    total,
    start: total ? startIdx + 1 : 0,
    end: Math.min(startIdx + pageSize, total),
  };
}

export function activityPaginationHtml(section, pageInfo) {
  const { page, totalPages, total, start, end } = pageInfo;
  if (total <= ACTIVITY_PAGE_SIZE) return "";
  const prevDisabled = page <= 1;
  const nextDisabled = page >= totalPages;
  const label = i18n(
    `${start}–${end} of ${total} · page ${page} of ${totalPages}`,
    `${start}–${end} / 共 ${total} 筆 · 第 ${page} / ${totalPages} 頁`
  );
  return `<div class="activity-pagination" data-activity-section="${escapeHtml(section)}">
      <button type="button" class="filter-chip activity-page-btn" data-activity-section="${escapeHtml(
        section
      )}" data-direction="prev"${prevDisabled ? " disabled" : ""}>${i18n("Prev", "上一頁")}</button>
      <span class="activity-pagination-label">${escapeHtml(label)}</span>
      <button type="button" class="filter-chip activity-page-btn" data-activity-section="${escapeHtml(
        section
      )}" data-direction="next"${nextDisabled ? " disabled" : ""}>${i18n("Next", "下一頁")}</button>
    </div>`;
}

export function tradeGroupActivityTitle(g) {
  const ccy = String(g?.currency || "").toUpperCase() || "Option";
  const ins = String(g?.short_instrument_name || "");
  if (ins) {
    const tail = ins.split("-").slice(-2).join(" ");
    return `${ccy} ${tail}`.trim();
  }
  try {
    return openPositionTitle(g);
  } catch (_) {
    return `${ccy} trade`;
  }
}

export function activityDetailLine(parts) {
  return parts
    .filter((p) => p)
    .map((p) => {
      if (typeof p === "string") return `<span>${escapeHtml(p)}</span>`;
      return `<span>${escapeHtml(p[0])} <strong>${escapeHtml(String(p[1]))}</strong></span>`;
    })
    .join("");
}

export function activityLifecycleCardHtml(g, status, groups) {
  const id = strategyId(g);
  const book = tradeGroupAprBook(g) || "—";
  const entryApr = groupEntryNetApr(g, status);
  const entryFee = groupEntryFeeUsd(g);
  const closeFee = groupCloseFeeUsd(g);
  const credit = num(g.entry_credit);
  const entryFeeNative = groupEntryFeeNative(g, status);
  const closeFeeNative = groupCloseFeeNative(g, status);
  const creditNative = groupEntryCreditNative(g, status);
  const entryMs = entryTimestampMs(g);
  const closed = isClosedTradeGroup(g);
  const pnl = realizedPnlDisplayUsdc(g, status);
  const holding = groupHoldingDays(g);
  const realizedApr = closed ? groupRealizedApr(g, status) : null;
  const amountLabel = activityAmountDisplay(g, status, groups);
  const title = tradeGroupActivityTitle(g);
  const entryCreditDisplay = credit === null ? "—" : fmtUsdWithNativeBookAmount(credit, creditNative, book);
  const entryAprDisplay = entryApr === null ? "—" : fmtPct(entryApr, 1);
  const entryFeeDisplay = entryFee === null ? null : fmtUsdWithNativeBookAmount(entryFee, entryFeeNative, book);
  const entryMetaSecondary = [
    [i18n("Opened", "開倉"), fmtTime(entryMs)],
    amountLabel !== null ? [i18n("Amount", "數量"), amountLabel] : null,
    entryFeeDisplay ? [i18n("Entry fee", "進場手續費"), entryFeeDisplay] : null,
  ].filter(Boolean);
  const entryInner = `<div class="activity-entry-metrics">
      <div class="activity-entry-metric">
        <span class="activity-entry-metric-label">${i18n("Credit", "收權利金")}</span>
        <span class="activity-entry-metric-value ${pnlClass(credit)}">${escapeHtml(entryCreditDisplay)}</span>
      </div>
      <div class="activity-entry-metric">
        <span class="activity-entry-metric-label">${i18n("Net APR", "淨年化報酬率")}</span>
        <span class="activity-entry-metric-value ${pnlClass(entryApr)}">${escapeHtml(entryAprDisplay)}</span>
      </div>
    </div>
    <div class="activity-phase-meta activity-phase-meta-secondary">
      ${activityDetailLine(entryMetaSecondary)}
    </div>`;
  let exitInner = "";
  if (closed) {
    const exitMetaSecondary = [
      [i18n("Closed", "平倉"), fmtTime(closedTimestampMs(g))],
      closeFee !== null
        ? [i18n("Close fee", "平倉手續費"), fmtUsdWithNativeBookAmount(closeFee, closeFeeNative, book)]
        : null,
      holding !== null
        ? [i18n("Held", "持有"), `${fmtNum(holding, 1)}${INVESTOR_ZH ? " 天" : "d"}`]
        : null,
    ].filter(Boolean);
    const pnlValue =
      pnl !== null
        ? `<span class="activity-closed-pnl-value ${pnlClass(pnl)}">${fmtRealizedPnlDisplay(g, status)}</span>`
        : `<span class="activity-closed-pnl-value activity-closed-pnl-value-missing">—</span>`;
    const aprValue =
      realizedApr !== null
        ? `<span class="activity-closed-pnl-value ${pnlClass(realizedApr)}">${fmtPct(realizedApr, 1)}</span>`
        : `<span class="activity-closed-pnl-value activity-closed-pnl-value-missing">—</span>`;
    const closedMetrics = `<div class="activity-closed-metrics">
        <div class="activity-closed-pnl">
          <span class="activity-closed-pnl-label">${i18n("Realized PnL", "已實現損益")}</span>
          ${pnlValue}
        </div>
        <div class="activity-closed-pnl">
          <span class="activity-closed-pnl-label">${i18n("Realized APR", "實現年化報酬")}</span>
          ${aprValue}
        </div>
      </div>`;
    exitInner = `${closedMetrics}<div class="activity-phase-meta activity-phase-meta-secondary">${activityDetailLine(
      exitMetaSecondary
    )}</div>`;
  } else {
    const exitMeta = [
      closeFee !== null
        ? [i18n("Est. close fee", "預估平倉費"), fmtUsdWithNativeBookAmount(closeFee, closeFeeNative, book)]
        : null,
    ].filter(Boolean);
    exitInner = `<div class="activity-phase-meta">
        <span class="activity-status-pill is-open">${i18n("Open", "持倉中")}</span>
        ${
          exitMeta.length
            ? activityDetailLine(exitMeta)
            : `<span>${i18n("Est. close fee", "預估平倉費")} <strong>—</strong></span>`
        }
      </div>`;
  }
  const acct = !INVESTOR && accountHint(g) ? accountHint(g) : "";
  return `
    <li class="activity-card">
      <div class="activity-card-head">
        ${strategyChipHtml(id)}
        <span class="activity-card-title">${escapeHtml(title)}</span>
        <span class="text-[11px] text-slate-500">${escapeHtml(book)}</span>
        ${acct ? `<span class="text-[11px] text-slate-500">${escapeHtml(acct)}</span>` : ""}
      </div>
      <div class="activity-card-instrument">${escapeHtml(g.short_instrument_name || "")}</div>
      <div class="activity-lifecycle">
        <div class="activity-phase activity-phase-entry">
          <div class="activity-phase-label">${i18n("Entry", "進場")}</div>
          ${entryInner}
        </div>
        <div class="activity-phase-divider" aria-hidden="true"></div>
        <div class="activity-phase activity-phase-exit">
          <div class="activity-phase-label">${i18n("Exit", "出場")}</div>
          ${exitInner}
        </div>
      </div>
    </li>`;
}

export function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

export function showToast(msg) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.add("hidden"), 5000);
}

export function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Bounded parallelism for refresh bursts — avoids stacking many heavy endpoints at once.
 */
export async function promisePool(factories, limit) {
  let next = 0;
  const cap = Math.max(1, Math.min(limit || 1, factories.length));
  async function worker() {
    while (true) {
      const i = next++;
      if (i >= factories.length) break;
      await factories[i]();
    }
  }
  await Promise.all(Array.from({ length: cap }, () => worker()));
}

export async function fetchJson(url, options = {}) {
  const targetUrl = resolveApiUrl(url);
  const maxAttempts = FETCH_JSON_MAX_RETRIES + 1;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    let res;
    try {
      res = await fetch(targetUrl, options);
    } catch (err) {
      if (attempt < maxAttempts - 1) {
        await delay(FETCH_JSON_RETRY_BASE_MS * (attempt + 1));
        continue;
      }
      throw err;
    }
    if (res.ok) return res.json();
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = `${res.status} ${body.detail}`;
    } catch (_) {}
    if (FETCH_JSON_RETRYABLE_STATUS.has(res.status) && attempt < maxAttempts - 1) {
      await delay(FETCH_JSON_RETRY_BASE_MS * (attempt + 1));
      continue;
    }
    throw new Error(detail);
  }
}
