import { INVESTOR, INVESTOR_ZH, i18n, resolveApiUrl } from "../shared/context.js";
import {
  ACTIVITY_PAGE_SIZE,
  BOOK_COLORS,
  CORE_BOOKS,
  FETCH_JSON_MAX_RETRIES,
  FETCH_JSON_NETWORK_MAX_RETRIES,
  FETCH_JSON_NETWORK_RETRY_BASE_MS,
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

/** Spot / premium-sweep execution price (BTC & ETH: whole dollars). */
export function fmtBookPriceUsd(book, value) {
  const n = num(value);
  if (n === null) return "—";
  const b = String(book || "").toUpperCase();
  if (b === "BTC" || b === "ETH") return fmt.usd0.format(n);
  return fmt.usd2.format(n);
}

export function fmtPct(value, decimals = 2) {
  const n = num(value);
  if (n === null) return "—";
  return decimals === 1 ? fmt.pct1.format(n) : fmt.pct2.format(n);
}

/** Display glyph for collateral book native amounts (Deribit-style where applicable). */
export function bookNativeSymbol(book) {
  const b = String(book || "").toUpperCase();
  if (b === "BTC") return "(₿)";
  if (b === "ETH") return "(♦)";
  if (b === "USDC") return "($)";
  if (b === "USDT") return "(₮)";
  return b;
}

/** Colored Deribit-style book glyph (use instead of bare ``bookNativeSymbol`` in HTML). */
export function bookNativeSymbolHtml(book, extraClass = "") {
  const b = String(book || "").toUpperCase();
  const extra = extraClass ? ` ${extraClass}` : "";
  return `<span class="native-book-symbol native-book-symbol--${b.toLowerCase()}${extra}">${bookNativeSymbol(b)}</span>`;
}

export function bookNativePlaces(book) {
  const b = String(book || "").toUpperCase();
  if (b === "BTC") return 5;
  if (b === "ETH") return 4;
  return 2;
}

function isKnownDeribitCollateral(currency) {
  const c = String(currency || "").toUpperCase();
  return c === "BTC" || c === "ETH" || c === "USDC" || c === "USDT";
}

function deribitPricePlaces(collateralCurrency) {
  const c = String(collateralCurrency || "").toUpperCase();
  if (c === "BTC" || c === "ETH") return 5;
  return 4;
}

function deribitSymbolHtml(collateralCurrency) {
  const c = String(collateralCurrency || "").toUpperCase();
  if (!isKnownDeribitCollateral(c)) {
    return `<span class="text-slate-500">${bookNativeSymbol(c)}</span>`;
  }
  return bookNativeSymbolHtml(c);
}

function portfolioHasEquity(portfolio) {
  return num(portfolio?.total_equity_usdc) !== null;
}

function equityByBookSumFromResolved(equityUsdByBook) {
  let sum = 0;
  let any = false;
  for (const book of CORE_BOOKS) {
    const v = num(equityUsdByBook?.[book]);
    if (v !== null) {
      sum += v;
      any = true;
    }
  }
  return any ? sum : null;
}

function equityByBookSum(portfolio, status = STATE.status) {
  const { equityUsdByBook } = overviewEquityBreakdown(portfolio, status);
  return equityByBookSumFromResolved(equityUsdByBook);
}

export function isPortfolioBreakdownConsistent(portfolio, status = STATE.status) {
  const total = num(portfolio?.total_equity_usdc);
  if (total === null) return false;
  const sum = equityByBookSum(portfolio, status);
  if (sum === null) return false;
  const tolerance = Math.max(2, Math.abs(total) * 0.05);
  return Math.abs(sum - total) <= tolerance;
}

export function isStatusBreakdownConsistent(status) {
  const total = num(status?.portfolio?.total_equity_usdc);
  if (total === null) return false;
  const usdByBook = bookEquityUsdByBook(status);
  let sum = 0;
  let any = false;
  for (const book of CORE_BOOKS) {
    const v = num(usdByBook[book]);
    if (v !== null) {
      sum += v;
      any = true;
    }
  }
  if (!any) return false;
  const tolerance = Math.max(2, Math.abs(total) * 0.05);
  return Math.abs(sum - total) <= tolerance;
}

function livePortfolioDisplayReady() {
  const live = STATE.status?.portfolio;
  if (!live || !portfolioHasEquity(live)) return false;
  return isPortfolioBreakdownConsistent(live) || isStatusBreakdownConsistent(STATE.status);
}

function investorSpotMarksReady() {
  return (
    num(STATE.status?.underlying_index_usd?.BTC) !== null ||
    num(STATE.lastSpotUsd?.BTC) !== null
  );
}

/** True when overview KPIs (especially realized P&L) can be shown without partial/stale math. */
export function isInvestorOverviewDisplayReady() {
  if (!INVESTOR) return true;
  if (!STATE.report?.summary) return false;
  const { portfolio } = resolvedPortfolio();
  if (!portfolio || !portfolioHasEquity(portfolio)) return false;
  const portfolioOk =
    isPortfolioBreakdownConsistent(portfolio) ||
    (STATE.status && isStatusBreakdownConsistent(STATE.status));
  if (!portfolioOk) return false;
  if (STATE.health?.has_private_creds !== false) {
    if (!STATE.groups || !Array.isArray(STATE.groups.closed)) return false;
    if (!investorSpotMarksReady()) return false;
  }
  return true;
}

function bookSpotUsd(status, book) {
  const b = String(book || "").toUpperCase();
  if (b === "USDC" || b === "USDT") return 1;
  return num(status?.underlying_index_usd?.[b]) ?? num(STATE.lastSpotUsd?.[b]);
}

/** Spot USD for a book (exported for profit composition / swap alignment). */
export function spotUsdForBook(status, book) {
  return bookSpotUsd(status, book);
}

const CRYPTO_NATIVE_HINT_MAX = { BTC: 10, ETH: 100 };

function bookEquityHintLooksLikeNative(hint, book) {
  const b = String(book || "").toUpperCase();
  if (b === "USDC" || b === "USDT") return false;
  const cap = CRYPTO_NATIVE_HINT_MAX[b];
  if (cap === undefined || hint === null || hint <= 0) return false;
  return hint < cap;
}

/** ``equity_by_book`` for BTC/ETH is USDC; ledger/cache rows may still hold native units. */
function isLikelyNativeMislabeledAsUsd(bookUsd, native, book) {
  const b = String(book || "").toUpperCase();
  if (b === "USDC" || b === "USDT") return false;
  if (bookUsd === null || native === null || native <= 0) return false;
  const rel = Math.abs(bookUsd - native) / Math.max(Math.abs(native), 1e-12);
  if (rel < 0.05) return true;
  return bookUsd < 500 && native < 500;
}

function cryptoUsdFromNative(native, spot) {
  if (native === null || spot === null || spot <= 0) return null;
  return native * spot;
}

function resolveBookEquityUsdNative(status, book, byBookUsdHint, totalEquityUsdc = null) {
  const b = String(book || "").toUpperCase();
  const nativeFromAcct = bookEquityNative(status, b);
  const spot = bookSpotUsd(status, b);
  const hint = num(byBookUsdHint);

  if (b === "USDC" || b === "USDT") {
    const usd = hint ?? nativeFromAcct;
    return { equityUsdByBook: usd, equityNativeByBook: usd, needsReconcile: false };
  }

  if (nativeFromAcct !== null && spot !== null && spot > 0) {
    return {
      equityUsdByBook: nativeFromAcct * spot,
      equityNativeByBook: nativeFromAcct,
      needsReconcile: false,
    };
  }

  if (hint === null) {
    const usd = cryptoUsdFromNative(nativeFromAcct, spot);
    return {
      equityUsdByBook: usd,
      equityNativeByBook: nativeFromAcct,
      needsReconcile: false,
    };
  }

  const hintLooksNative = bookEquityHintLooksLikeNative(hint, b);
  const hintMatchesAcctNative =
    nativeFromAcct !== null && isLikelyNativeMislabeledAsUsd(hint, nativeFromAcct, b);

  if ((hintLooksNative || hintMatchesAcctNative) && spot !== null && spot > 0) {
    const impliedUsd = hint * spot;
    if (impliedUsd <= num(totalEquityUsdc) * 0.35) {
      return {
        equityUsdByBook: impliedUsd,
        equityNativeByBook: hint,
        needsReconcile: false,
      };
    }
    return {
      equityUsdByBook: null,
      equityNativeByBook: nativeFromAcct ?? hint,
      needsReconcile: true,
    };
  }

  if (hintLooksNative || hintMatchesAcctNative) {
    return {
      equityUsdByBook: null,
      equityNativeByBook: nativeFromAcct ?? hint,
      needsReconcile: true,
    };
  }

  return {
    equityUsdByBook: hint,
    equityNativeByBook:
      spot !== null && spot > 0 ? hint / spot : nativeFromAcct,
    needsReconcile: false,
  };
}

function reconcileOverviewEquityBreakdown(equityUsdByBook, equityNativeByBook, reconcileBooks, portfolio, status) {
  if (!reconcileBooks.length) return;
  const total = num(portfolio?.total_equity_usdc);
  if (total === null) return;

  let stableUsd = 0;
  let resolvedCryptoUsd = 0;
  for (const book of CORE_BOOKS) {
    const usd = num(equityUsdByBook[book]);
    if (usd === null) continue;
    if (book === "USDC" || book === "USDT") stableUsd += usd;
    else if (!reconcileBooks.includes(book)) resolvedCryptoUsd += usd;
  }

  let residual = total - stableUsd - resolvedCryptoUsd;
  if (residual < 0) residual = 0;

  const hints = reconcileBooks.map((book) => {
    const hint = num(portfolio?.equity_by_book?.[book]);
    return hint !== null && hint > 0 ? hint : null;
  });
  const hintSum = hints.reduce((sum, h) => sum + (h ?? 0), 0);
  const weightFallback = reconcileBooks.length ? 1 / reconcileBooks.length : 0;

  reconcileBooks.forEach((book, idx) => {
    const spot = bookSpotUsd(status, book);
    const acctNative = bookEquityNative(status, book);
    const weight =
      hintSum > 0 && hints[idx] !== null ? hints[idx] / hintSum : weightFallback;
    const usd = residual * weight;
    equityUsdByBook[book] = usd;
    if (acctNative !== null) {
      equityNativeByBook[book] = acctNative;
    } else if (spot !== null && spot > 0 && usd > 0) {
      equityNativeByBook[book] = usd / spot;
    } else {
      equityNativeByBook[book] = hints[idx];
    }
  });
}

export function overviewEquityBreakdown(portfolio, status) {
  const byBook = portfolio?.equity_by_book;
  const hasByBook =
    byBook &&
    typeof byBook === "object" &&
    CORE_BOOKS.some((book) => num(byBook[book]) !== null);

  const equityUsdByBook = {};
  const equityNativeByBook = {};
  const reconcileBooks = [];

  const totalEquityUsdc = num(portfolio?.total_equity_usdc);
  const books = hasByBook ? CORE_BOOKS : CORE_BOOKS;
  for (const book of books) {
    const resolved = resolveBookEquityUsdNative(
      status,
      book,
      hasByBook ? byBook[book] : null,
      totalEquityUsdc
    );
    equityUsdByBook[book] = resolved.equityUsdByBook;
    equityNativeByBook[book] = resolved.equityNativeByBook;
    if (resolved.needsReconcile) reconcileBooks.push(book);
  }

  if (!hasByBook) {
    for (const book of CORE_BOOKS) {
      if (equityUsdByBook[book] === null) {
        equityUsdByBook[book] = bookEquityUsdForDisplay(book, status);
      }
      if (equityNativeByBook[book] === null) {
        equityNativeByBook[book] = bookEquityNative(status, book);
      }
    }
  }

  reconcileOverviewEquityBreakdown(
    equityUsdByBook,
    equityNativeByBook,
    reconcileBooks,
    portfolio,
    status
  );
  return { equityUsdByBook, equityNativeByBook };
}

export function resolvedPortfolio() {
  const live = STATE.status?.portfolio;
  const snap = STATE.portfolioSnapshot?.portfolio;
  const snapUsable = snap && Object.keys(snap).length > 0;
  const liveUsable = live && Object.keys(live).length > 0;
  const cacheMode = STATE.dataFreshness.source === "cache";
  const cacheAgeMs =
    STATE.dataFreshness.cacheSavedAt != null
      ? Math.max(0, Date.now() - STATE.dataFreshness.cacheSavedAt)
      : STATE.dataFreshness.cacheAgeMs;

  if (cacheMode) {
    if (liveUsable && portfolioHasEquity(live) && livePortfolioDisplayReady()) {
      return { portfolio: live, source: "cache", freshnessMs: cacheAgeMs };
    }
    if (snapUsable && portfolioHasEquity(snap)) {
      return {
        portfolio: snap,
        source: "cache",
        freshnessMs: num(STATE.portfolioSnapshot?.freshness_ms) ?? cacheAgeMs,
      };
    }
    if (liveUsable && portfolioHasEquity(live)) {
      return { portfolio: live, source: "cache", freshnessMs: cacheAgeMs };
    }
    if (snapUsable) {
      return {
        portfolio: snap,
        source: "cache",
        freshnessMs: num(STATE.portfolioSnapshot?.freshness_ms) ?? cacheAgeMs,
      };
    }
    return { portfolio: null, source: null, freshnessMs: null };
  }

  if (
    INVESTOR &&
    STATE.refreshInFlight &&
    liveUsable &&
    portfolioHasEquity(live) &&
    !livePortfolioDisplayReady()
  ) {
    if (snapUsable && portfolioHasEquity(snap) && isPortfolioBreakdownConsistent(snap)) {
      return {
        portfolio: snap,
        source: "snapshot",
        freshnessMs: num(STATE.portfolioSnapshot?.freshness_ms),
      };
    }
  }

  if (liveUsable && portfolioHasEquity(live) && (!INVESTOR || livePortfolioDisplayReady())) {
    return {
      portfolio: live,
      source: "live",
      freshnessMs: STATE.dataFreshness.statusMs ?? 0,
    };
  }
  if (
    STATE.dataFreshness.live &&
    snapUsable &&
    portfolioHasEquity(snap) &&
    (STATE.portfolioSnapshot?.source === "portal_cache" || STATE.portfolioSnapshot?.cache_kind === "live")
  ) {
    return {
      portfolio: snap,
      source: "live",
      freshnessMs: num(STATE.portfolioSnapshot?.freshness_ms) ?? 0,
    };
  }
  if (snapUsable && portfolioHasEquity(snap)) {
    return {
      portfolio: snap,
      source: "snapshot",
      freshnessMs: num(STATE.portfolioSnapshot?.freshness_ms),
    };
  }
  if (liveUsable) {
    return {
      portfolio: live,
      source: "live",
      freshnessMs: STATE.dataFreshness.statusMs ?? 0,
    };
  }
  if (snapUsable) {
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
  if (resolved.source === "cache") {
    if (STATE.refreshInFlight) {
      return `<span id="data-freshness-badge" class="freshness-badge freshness-badge--stale">${i18n(
        "Last visit · syncing…",
        "上次瀏覽 · 同步中…"
      )}</span>`;
    }
    const mins = fmtFreshnessMinutes(resolved.freshnessMs);
    const label =
      mins !== null
        ? i18n(`Last visit · ~${mins}m ago`, `上次瀏覽 · 約 ${mins} 分鐘前`)
        : i18n("Last visit", "上次瀏覽");
    return `<span id="data-freshness-badge" class="freshness-badge freshness-badge--stale">${label}</span>`;
  }
  if (resolved.source === "live") {
    const age = num(STATE.dataFreshness.statusMs);
    if (age !== null && age < 30_000) {
      return `<span id="data-freshness-badge" class="freshness-badge freshness-badge--live">${i18n("Live", "即時")}</span>`;
    }
  }
  if (resolved.source === "snapshot") {
    const mins = fmtFreshnessMinutes(resolved.freshnessMs);
    const label =
      mins !== null
        ? i18n(`Snapshot · ~${mins}m ago`, `快照 · 約 ${mins} 分鐘前`)
        : i18n("Snapshot", "快照");
    return `<span id="data-freshness-badge" class="freshness-badge freshness-badge--stale">${label}</span>`;
  }
  if (STATE.summaryLoadPending) {
    return `<span id="data-freshness-badge" class="freshness-badge freshness-badge--stale">${i18n("Syncing summary…", "摘要同步中…")}</span>`;
  }
  if (INVESTOR && STATE.groupsLivePending && STATE.refreshInFlight) {
    return `<span id="data-freshness-badge" class="freshness-badge freshness-badge--stale">${i18n("Syncing live marks…", "即時標價同步中…")}</span>`;
  }
  if (STATE.refreshInFlight) {
    return `<span id="data-freshness-badge" class="freshness-badge">${i18n("Loading…", "載入中…")}</span>`;
  }
  return `<span id="data-freshness-badge" class="freshness-badge">${i18n("—", "—")}</span>`;
}

export function renderDataFreshnessBadge() {
  const host =
    document.getElementById("overview-freshness-slot") ||
    document.getElementById("data-freshness-slot");
  if (!host) return;
  host.innerHTML = dataFreshnessBadgeHtml();
}

export function setRefreshProgressBar(active, { indeterminate = false } = {}) {
  for (const id of ["investor-progress-bar", "dashboard-progress-bar"]) {
    const bar = document.getElementById(id);
    if (!bar) continue;
    bar.classList.toggle("hidden", !active);
    bar.classList.toggle("investor-progress-bar--indeterminate", active && indeterminate);
  }
}

export function setInvestorProgressBar(active, options = {}) {
  setRefreshProgressBar(active, options);
}

export function setRefreshControlsDisabled(disabled) {
  const refreshBtn = document.getElementById("refresh-now");
  if (refreshBtn) refreshBtn.disabled = disabled;
}

function profitNativeToUsdApprox(book, native) {
  const n = num(native);
  if (n === null) return null;
  const b = String(book || "").toUpperCase();
  if (b === "USDC" || b === "USDT") return n;
  const spot = bookSpotUsd(STATE.status, b);
  if (spot === null || spot <= 0) return null;
  return n * spot;
}

function fmtUsdAbsForPnlCue(value) {
  const n = num(value);
  if (n === null) return "—";
  return fmtUsd(n);
}

function fmtAprBlockHtml(apr, label, variant = "hero") {
  const tone = pnlClass(apr);
  const tag = variant === "inline" ? "span" : "div";
  return `<${tag} class="overview-apr overview-apr--${variant} ${tone}">
    <span class="overview-apr-value font-mono tabular-nums">${fmtPct(apr)}</span>
    <span class="overview-apr-label">${label}</span>
  </${tag}>`;
}

function overviewBreakdownRowHtml(row) {
  const headPct = row.isLoss
    ? ""
    : `<span class="overview-breakdown-pct font-mono tabular-nums">${row.pctText}</span>`;
  const valuesBlock = `<div class="overview-breakdown-values">
        <span class="overview-breakdown-detail font-mono tabular-nums ${row.detailTone ?? ""}">${row.detailText}</span>
        <span class="overview-breakdown-primary font-mono tabular-nums ${row.tone ?? ""}">${row.primaryText}</span>
      </div>`;
  return `<div class="overview-breakdown-row overview-breakdown-row--${row.book.toLowerCase()}${row.isLoss ? " overview-breakdown-row--loss" : ""}">
      <div class="overview-breakdown-head">
        <span class="overview-breakdown-label">${bookNativeSymbolHtml(row.book)}<span class="overview-breakdown-book">${row.book}</span></span>
        ${headPct}
      </div>
      ${row.showBar === false ? "" : `<div class="overview-breakdown-bar" aria-hidden="true"><span class="overview-breakdown-bar-fill ${row.barFillClass ?? `overview-breakdown-bar-fill--${row.book.toLowerCase()}`}" style="width:${row.barWidth}"></span></div>`}
      ${valuesBlock}
    </div>`;
}

function overviewBreakdownRowsHtml(rows, { emptyLabel, sectionLabel = "" } = {}) {
  if (!rows.length) {
    return `<p class="overview-breakdown-empty">${emptyLabel ?? i18n("—", "—")}</p>`;
  }
  const label = sectionLabel
    ? `<p class="overview-breakdown-section-label">${sectionLabel}</p>`
    : "";
  return `${label}<div class="overview-breakdown">${rows.map((row) => overviewBreakdownRowHtml(row)).join("")}</div>`;
}

export function overviewEquityCompositionHtml(totalEquity, nativeByBook, usdByBook) {
  const total = num(totalEquity);
  const places = { BTC: 5, ETH: 4, USDC: 2, USDT: 2 };
  const rows = CORE_BOOKS.map((book) => {
    const usd = num(usdByBook?.[book]);
    const native = num(nativeByBook?.[book]);
    if (usd === null && native === null) return null;
    if (usd !== null && Math.abs(usd) < 0.005 && (native === null || Math.abs(native) < 1e-8)) {
      return null;
    }
    const isStable = book === "USDC" || book === "USDT";
    const pct = total && usd !== null && total > 0 ? usd / total : null;
    const pctText = pct !== null ? fmtPct(pct, 1) : "—";
    const barWidth = pct !== null ? `${Math.min(100, Math.max(pct * 100, 2)).toFixed(1)}%` : "0%";
    const primaryText = usd !== null ? fmtUsd(usd) : i18n("pending", "待更新");
    const detailText = isStable
      ? i18n("stablecoin", "穩定幣")
      : native !== null
      ? fmtNum(native, places[book])
      : "—";
    return { book, pctText, barWidth, detailText, primaryText };
  }).filter(Boolean);
  return overviewBreakdownRowsHtml(rows, { emptyLabel: i18n("No book balances", "尚無帳本餘額") });
}

function compositionRowUsd({ book, swappedUsdt, native, totalUsd, earnedUsd }) {
  if (book === "USDC" || book === "USDT") {
    return totalUsd ?? earnedUsd ?? 0;
  }
  const unsweptUsd = isMeaningfulNativeForBook(native, book)
    ? profitNativeToUsdApprox(book, native) ?? 0
    : 0;
  if (swappedUsdt > 0.005) {
    if (unsweptUsd > 0.005) {
      return totalUsd ?? swappedUsdt + unsweptUsd;
    }
    // Fully swapped: match Profit swap → SOLD USDT (spotSoldQuote).
    return swappedUsdt;
  }
  return totalUsd ?? earnedUsd ?? unsweptUsd;
}

function buildProfitCompositionRows(ctx) {
  const { summary, profitCompositionByBook } = ctx;
  if (!summary || !profitCompositionByBook) return "";
  const {
    nativeByBook,
    earnedNativeByBook,
    earnedUsdByBook,
    swappedNativeByBook,
    swappedUsdtByBook,
    usdByBook,
    hedgeTotalUsd,
  } = profitCompositionByBook;
  const hedgeUsd = num(hedgeTotalUsd) ?? 0;
  const hasHedge = Math.abs(hedgeUsd) >= 0.005;
  const places = { BTC: 5, ETH: 4, USDC: 2 };
  const entries = PROFIT_HELD_BOOKS.map((book) => {
    const native = num(nativeByBook?.[book]);
    const earnedNative = num(earnedNativeByBook?.[book]) ?? 0;
    const swappedNative = num(swappedNativeByBook?.[book]) ?? 0;
    const earnedUsd =
      num(earnedUsdByBook?.[book]) ??
      (native !== null && native !== 0 ? profitNativeToUsdApprox(book, native) : null);
    const swappedUsdt = num(swappedUsdtByBook?.[book]) ?? 0;
    if (earnedUsd === null && (native === null || native === 0) && earnedNative === 0) return null;
    if (
      earnedUsd !== null &&
      Math.abs(earnedUsd) < 0.005 &&
      (native === null || Math.abs(native) < 1e-8) &&
      Math.abs(earnedNative) < 1e-8
    ) {
      return null;
    }
    const totalUsd =
      num(usdByBook?.[book]) ??
      (swappedUsdt > 0.005 && isMeaningfulNativeForBook(native, book)
        ? swappedUsdt + (profitNativeToUsdApprox(book, native) ?? 0)
        : null) ??
      earnedUsd ??
      (native !== null && native !== 0 ? profitNativeToUsdApprox(book, native) : null);
    return {
      book,
      native: native ?? 0,
      earnedNative,
      swappedNative,
      earnedUsd,
      swappedUsdt,
      totalUsd,
    };
  }).filter(Boolean);

  const rowUsd = (e) => compositionRowUsd(e);
  let gainUsdTotal = entries.filter((e) => rowUsd(e) > 0).reduce((sum, e) => sum + Math.abs(rowUsd(e)), 0);
  let lossUsdTotal = entries.filter((e) => rowUsd(e) < 0).reduce((sum, e) => sum + Math.abs(rowUsd(e)), 0);
  if (hasHedge) {
    if (hedgeUsd > 0) gainUsdTotal += hedgeUsd;
    else if (hedgeUsd < 0) lossUsdTotal += Math.abs(hedgeUsd);
  }
  const byBook = Object.fromEntries(entries.map((entry) => [entry.book, entry]));

  function profitRow(entry, { isLoss, usdDenom }) {
    const { book, native, earnedNative, swappedNative, earnedUsd, swappedUsdt } = entry;
    const displayUsd = compositionRowUsd(entry);
    const pct = displayUsd !== null && usdDenom > 0 ? Math.abs(displayUsd) / usdDenom : null;
    const pctText = pct !== null ? fmtPct(pct, 1) : "—";
    const barWidth =
      pct !== null ? `${Math.min(100, Math.max(pct * 100, isLoss ? 8 : 2)).toFixed(1)}%` : "0%";
    const tone = pnlClass(displayUsd ?? native);
    const nativeText = fmtNum(native, places[book] ?? 4);
    const earnedNativeText = fmtNum(earnedNative, places[book] ?? 4);
    const swappedNativeText = fmtNum(swappedNative, places[book] ?? 4);
    const isStable = book === "USDC" || book === "USDT";
    let detailText;
    let primaryText;
    if (displayUsd === null && earnedUsd === null) {
      detailText = i18n("native · USD pending", "原幣 · USD 待更新");
      primaryText = nativeText;
    } else if (isStable) {
      detailText = i18n("stablecoin", "穩定幣");
      primaryText = displayUsd !== null ? fmtUsdAbsForPnlCue(displayUsd) : nativeText;
    } else if (swappedUsdt > 0.005 && isMeaningfulNativeForBook(swappedNative, book)) {
      const swappedLabel = i18n("swapped", "已兌");
      detailText = `${earnedNativeText} ${book} (${swappedNativeText} ${book} ${swappedLabel})`;
      primaryText = fmtUsdAbsForPnlCue(displayUsd ?? swappedUsdt);
    } else if (isMeaningfulNativeForBook(earnedNative, book) && isMeaningfulNativeForBook(native, book)) {
      detailText = `${earnedNativeText} ${book}`;
      primaryText =
        displayUsd !== null ? fmtUsdAbsForPnlCue(displayUsd) : `${earnedNativeText} ${book}`;
    } else if (isMeaningfulNativeForBook(earnedNative, book)) {
      detailText = `${earnedNativeText} ${book}`;
      primaryText =
        displayUsd !== null ? fmtUsdAbsForPnlCue(displayUsd) : `${earnedNativeText} ${book}`;
    } else if (isMeaningfulNativeForBook(native, book)) {
      detailText = `${nativeText} ${book}`;
      primaryText =
        displayUsd !== null ? fmtUsdAbsForPnlCue(displayUsd) : `${nativeText} ${book}`;
    } else {
      detailText = `${nativeText} ${book}`;
      primaryText = displayUsd !== null ? fmtUsdAbsForPnlCue(displayUsd) : nativeText;
    }
    return {
      book,
      pctText,
      barWidth,
      detailText,
      primaryText,
      tone,
      isLoss,
      barFillClass: isLoss ? "overview-breakdown-bar-fill--loss" : undefined,
    };
  }

  function hedgeProfitRow(usd, usdDenom, isLoss) {
    const pct = usdDenom > 0 ? Math.abs(usd) / usdDenom : null;
    const pctText = pct !== null ? fmtPct(pct, 1) : "—";
    const barWidth =
      pct !== null ? `${Math.min(100, Math.max(pct * 100, isLoss ? 8 : 2)).toFixed(1)}%` : "0%";
    return {
      book: "USDC",
      pctText,
      barWidth,
      detailText: "perp hedge",
      primaryText: fmtUsdAbsForPnlCue(usd),
      tone: pnlClass(usd),
      isLoss,
      barFillClass: isLoss ? "overview-breakdown-bar-fill--loss" : undefined,
    };
  }

  const rows = [];
  for (const book of CORE_BOOKS) {
    const entry = byBook[book];
    if (entry) {
      const isLoss = rowUsd(entry) < 0;
      rows.push(
        profitRow(entry, {
          isLoss,
          usdDenom: isLoss ? lossUsdTotal : gainUsdTotal,
        })
      );
    }
    if (book === "USDC" && hasHedge) {
      rows.push(
        hedgeProfitRow(hedgeUsd, hedgeUsd < 0 ? lossUsdTotal : gainUsdTotal, hedgeUsd < 0)
      );
    }
  }

  return overviewBreakdownRowsHtml(rows, {
    emptyLabel: i18n("No realized profit yet", "尚無已實現獲利"),
  });
}

export function overviewProfitCompositionHtml(ctx) {
  if (!ctx.summary) {
    return `<p class="overview-breakdown-empty">${i18n("Loading performance…", "績效摘要載入中…")}</p>`;
  }
  return buildProfitCompositionRows(ctx);
}

export function overviewCompositionGridHtml(ctx) {
  const { totalEquity, equityNativeByBook, equityUsdByBook } = ctx;
  return `<div class="overview-composition-grid">
    <section class="overview-composition-card overview-composition-card--equity" aria-label="${i18n("Equity composition", "權益組成")}">
      <header class="overview-composition-head">
        <h3 class="overview-composition-title">${i18n("Equity composition", "權益組成")}</h3>
        <span class="overview-composition-sub">${i18n("By collateral book · USDC eq.", "依帳本 · USDC 約當")}</span>
      </header>
      ${overviewEquityCompositionHtml(totalEquity, equityNativeByBook, equityUsdByBook)}
    </section>
    <section class="overview-composition-card overview-composition-card--profit" aria-label="${i18n("Profit composition", "獲利組成")}">
      <header class="overview-composition-head">
        <h3 class="overview-composition-title">${i18n("Profit composition", "獲利組成")}</h3>
        <span class="overview-composition-sub">${i18n("Realized · lifetime", "已實現 · 存續")}</span>
      </header>
      ${overviewProfitCompositionHtml(ctx)}
    </section>
  </div>`;
}

export function fmtBookEquityAllocationChips(nativeByBook, usdByBook) {
  const places = { BTC: 5, ETH: 4, USDC: 2, USDT: 2 };
  const chips = CORE_BOOKS.map((book) => {
    const native = num(nativeByBook?.[book]);
    const usd = num(usdByBook?.[book]);
    const isStable = book === "USDC" || book === "USDT";
    const usdVal = isStable ? (usd ?? native) : usd;
    const hasNative = native !== null && Math.abs(native) >= 1e-8;
    const hasUsd = usdVal !== null && Math.abs(usdVal) >= 0.005;
    if (!hasNative && !hasUsd) return "";
    const sym = bookNativeSymbolHtml(book);
    let val;
    if (isStable) {
      val = hasUsd ? fmtUsd(usdVal) : "—";
    } else if (hasNative && hasUsd) {
      val = `${fmtNum(native, places[book])} · ${fmtUsd(usdVal)}`;
    } else if (hasNative) {
      val = `${fmtNum(native, places[book])} <span class="overview-alloc-pending">${i18n("(USD pending)", "（USD 待更新）")}</span>`;
    } else if (hasUsd) {
      val = fmtUsd(usdVal);
    } else {
      return "";
    }
    return `<span class="overview-alloc-chip overview-alloc-chip--${book.toLowerCase()}">${sym}<span class="overview-alloc-chip-val font-mono tabular-nums">${val}</span></span>`;
  }).filter(Boolean);
  if (!chips.length) {
    return `<span class="overview-alloc-empty">${i18n("—", "—")}</span>`;
  }
  return `<div class="overview-alloc-chips">${chips.join("")}</div>`;
}

export function aggregateSkeletonHtml() {
  const hero = `<div class="overview-hero skeleton-block" style="min-height:6.5rem;border-radius:16px"></div>`;
  const stats = `<div class="overview-stat-row">${`<div class="overview-stat skeleton-block" style="min-height:4.5rem;border-radius:12px"></div>`.repeat(3)}</div>`;
  const headline = `<div class="overview-headline">${hero}${profitSkeletonHtml()}${stats}</div>`;
  const desktopContent = `<div class="overview-stack">${headline}</div>`;
  const inner = INVESTOR
    ? `<div class="investor-view-desktop">${desktopContent}</div><div class="investor-view-mobile"><div class="inv-dashboard">
      <div class="inv-panel skeleton-block" style="height:5.5rem"></div>
      <div class="inv-panel skeleton-block" style="height:4rem"></div>
      <div class="inv-panel skeleton-block" style="height:6rem"></div>
      <div class="inv-panel skeleton-block" style="height:10rem"></div>
    </div></div>`
    : desktopContent;
  return `<div class="overview-panel-inner">${inner}<div id="overview-freshness-slot" class="overview-freshness-corner"></div></div>`;
}

export function profitSkeletonHtml() {
  const card = (h) =>
    `<section class="overview-composition-card skeleton-block" style="min-height:${h};border-radius:14px"></section>`;
  return `<div class="overview-composition-grid">${card("11rem")}${card("11rem")}</div>`;
}

export function overviewDesktopContentHtml(ctx) {
  return `<div class="overview-stack">
    ${overviewMetricsGridHtml(ctx)}
    ${overviewProfitSectionHtml(ctx)}
  </div>`;
}

export function overviewMetricsGridHtml(ctx) {
  const {
    totalEquity,
    openCredit,
    creditByStrategy,
    summary,
    winRate,
    avgHolding,
    sinceLine,
    lifetimePnl,
    lifetimeApr,
  } = ctx;
  const winHold = summary
    ? `${fmtPct(winRate, 1)} · ${fmtNum(avgHolding, 2)}${INVESTOR_ZH ? " 天" : "d"}`
    : "—";
  const winSub = summary ? sinceLine : i18n("Loading…", "載入中…");
  return `
    <div class="overview-headline">
      <section class="overview-hero" aria-label="${i18n("Portfolio summary", "投資組合摘要")}">
        <div class="overview-hero-primary">
          <span class="overview-hero-label">${i18n("Total equity", "總權益")}</span>
          <span class="overview-hero-value font-mono tabular-nums">${fmtUsd(totalEquity)}</span>
          <span class="overview-hero-foot">${i18n("USDC equivalent · all books", "USDC 約當 · 全帳本")}</span>
        </div>
        <div class="overview-hero-secondary overview-hero-secondary--profit">
          <span class="overview-hero-label">${i18n("Total profit", "累計獲利")}</span>
          <span class="overview-hero-value font-mono tabular-nums ${pnlClass(lifetimePnl)}">${summary ? fmtUsd(lifetimePnl) : "—"}</span>
          ${summary ? fmtAprBlockHtml(lifetimeApr, i18n("APR · lifetime", "年化 · 存續"), "hero") : `<span class="overview-hero-foot">${winSub}</span>`}
        </div>
      </section>
      ${overviewCompositionGridHtml(ctx)}
      <div class="overview-stat-row">
        <div class="overview-stat">
          <span class="overview-stat-label">${i18n("Open credit", "未實現權利金")}</span>
          <span class="overview-stat-value font-mono tabular-nums">${fmtUsd(openCredit)}</span>
          <span class="overview-stat-sub">${fmtOpenCreditStrategyBreakdown(creditByStrategy)}</span>
        </div>
        <div class="overview-stat">
          <span class="overview-stat-label">${i18n("Win rate · hold", "勝率 · 持有")}</span>
          <span class="overview-stat-value font-mono tabular-nums">${winHold}</span>
          <span class="overview-stat-sub">${winSub}</span>
        </div>
        <div class="overview-stat">
          <span class="overview-stat-label">${i18n("Closed groups", "已平倉")}</span>
          <span class="overview-stat-value font-mono tabular-nums">${summary ? String(ctx.closedCount ?? 0) : "—"}</span>
          <span class="overview-stat-sub">${summary ? i18n("lifetime realized", "存續期已實現") : winSub}</span>
        </div>
      </div>
    </div>`;
}

export function overviewProfitSummaryCardHtml(ctx) {
  const {
    summary,
    lifetimePnl,
    lifetimeNativeByBook,
    closedCount,
    windowLabelDays,
    windowPnl,
    windowNativeByBook,
    lifetimeApr,
    windowApr,
  } = ctx;
  const windowDays = Math.round(windowLabelDays ?? 30);
  const sameWindowPnl =
    summary &&
    lifetimePnl !== null &&
    windowPnl !== null &&
    Math.abs(lifetimePnl - windowPnl) < 0.005;
  const compareHtml = summary
    ? `<div class="overview-profit-compare">
        <div class="overview-profit-compare-item">
          <span class="overview-profit-compare-label">${i18n("Last", "近")} ${windowDays}${INVESTOR_ZH ? " 日" : "d"}</span>
          <span class="overview-profit-compare-value font-mono tabular-nums ${pnlClass(windowPnl)}">${fmtUsd(windowPnl)}</span>
        </div>
        <div class="overview-profit-compare-item">
          <span class="overview-profit-compare-label">${i18n("APR", "年化")} ${windowDays}${INVESTOR_ZH ? " 日" : "d"}</span>
          <span class="overview-profit-compare-value font-mono tabular-nums">${fmtPct(windowApr)}</span>
        </div>
      </div>`
    : "";
  const metaHtml = summary
    ? `<div class="overview-profit-meta">
        <span class="overview-profit-meta-note">${closedCount ?? 0} ${i18n("closed", "筆平倉")} · ${fmtPct(lifetimeApr)} ${i18n("APR lifetime", "年化·存續")}</span>
        <div class="overview-profit-chips">${investorNativeChipsHtml(lifetimeNativeByBook, { pnl: true })}</div>
        ${sameWindowPnl ? `<span class="overview-profit-meta-hint">${i18n(`Rolling ${windowDays}d matches lifetime`, `近 ${windowDays} 日與存續期相同`)}</span>` : `<div class="overview-profit-chips overview-profit-chips--window">${investorNativeChipsHtml(windowNativeByBook, { pnl: true })}</div>`}
      </div>`
    : `<p class="overview-profit-empty">${i18n("Loading performance…", "績效摘要載入中…")}</p>`;
  return `<section class="overview-profit-card overview-profit-card--realized" aria-label="${i18n("Realized profit", "已實現損益")}">
    <header class="overview-profit-card-head">
      <p class="overview-profit-eyebrow">${i18n("Realized P&L", "已實現損益")}</p>
      <h3 class="overview-profit-title">${i18n("Profit", "獲利")}</h3>
    </header>
    <div class="overview-profit-hero">
      <div class="overview-profit-hero-main">
        <span class="overview-profit-hero-label">${i18n("Lifetime", "存續")}</span>
        <span class="overview-profit-hero-value font-mono tabular-nums ${pnlClass(lifetimePnl)}">${summary ? fmtUsd(lifetimePnl) : "—"}</span>
      </div>
      ${compareHtml}
    </div>
    ${metaHtml}
  </section>`;
}

export function overviewProfitSectionHtml(ctx) {
  const swapBody = profitSwapDetailBodyHtml(ctx);
  const hasSwap = swapBody && !swapBody.includes("overview-profit-empty");
  if (!hasSwap) return "";
  return `<section class="overview-composition-card overview-composition-card--swap" aria-label="${i18n("Profit swap", "獲利兌換")}">
    <header class="overview-composition-head">
      <h3 class="overview-composition-title">${i18n("Profit swap", "獲利兌換")}</h3>
      <span class="overview-composition-sub">${i18n("Spot → USDT", "現貨 → USDT")}</span>
    </header>
    ${swapBody}
  </section>`;
}

export function overviewLayoutHtml(ctx) {
  return overviewDesktopContentHtml(ctx);
}

function fmtProfitSwapScopePanelOnly(disposition) {
  if (!disposition) return "";
  const summary = summarizeProfitDisposition(disposition, { status: STATE.status });
  if (!summary?.hasSwap && !summary?.hasPending) return "";
  return fmtProfitSwapPanel(disposition, summary) || "";
}

function profitSwapScopePanelSectionHtml(disposition, scopeLabel) {
  const content = fmtProfitSwapScopePanelOnly(disposition);
  if (!content) return "";
  return `<section class="overview-profit-scope">
    <span class="profit-swap-scope-label">${scopeLabel}</span>
    ${content}
  </section>`;
}

export function profitSwapDetailBodyHtml(ctx) {
  const { summary, lifetimeProfitDisposition } = ctx;
  if (!summary) {
    return `<p class="overview-profit-empty">${i18n("Loading performance…", "績效摘要載入中…")}</p>`;
  }
  const lifetime = profitSwapScopePanelSectionHtml(
    lifetimeProfitDisposition,
    i18n("Lifetime", "存續")
  );
  if (!lifetime) {
    return `<p class="overview-profit-empty">${i18n("No profit swaps yet", "尚未進行獲利兌換")}</p>`;
  }
  return `<div class="overview-profit-scopes overview-profit-scopes--swap overview-profit-scopes--single">${lifetime}</div>`;
}

export function overviewProfitSwapDetailHtml(ctx) {
  return `<section class="overview-profit-card overview-profit-card--swap" aria-label="${i18n("Profit swap", "獲利兌換")}">
    <header class="overview-profit-card-head">
      <p class="overview-profit-eyebrow">${i18n("Profit swap", "獲利兌換")}</p>
      <h3 class="overview-profit-title">${i18n("Spot → USDT", "現貨 → USDT")}</h3>
    </header>
    <div class="overview-profit-swap-body">${profitSwapDetailBodyHtml(ctx)}</div>
  </section>`;
}

export function profitSwapCardBodyHtml(ctx) {
  return profitSwapDetailBodyHtml(ctx);
}

export function profitSwapSideCardHtml(ctx) {
  return overviewProfitSwapDetailHtml(ctx);
}

export function investorNativeChipsHtml(byBook, { pnl = false, places = { BTC: 5, ETH: 4, USDC: 2, USDT: 2 }, books = CORE_BOOKS } = {}) {
  return books
    .filter((book) => {
      const n = num(byBook?.[book]);
      return n !== null && n !== 0;
    })
    .map((book) => {
      const n = num(byBook[book]);
      const text = n === null ? "—" : fmtNum(n, places[book] ?? 4);
      const tone = pnl ? pnlClass(byBook[book]) : "";
      return `<span class="inv-chip"><span class="inv-chip-sym">${bookNativeSymbolHtml(book)}</span><span class="inv-chip-val font-mono tabular-nums ${tone}">${text}</span></span>`;
    })
    .join("");
}

export function investorOpenCreditMiniHtml(byStrategy) {
  return strategyOrder(new Set(dashboardStrategyIds()))
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
    openCredit,
    creditByStrategy,
    summary,
    winRate,
    avgHolding,
    sinceLine,
    lifetimePnl,
    lifetimeApr,
  } = ctx;
  const winHold =
    summary !== null && summary !== undefined
      ? `${fmtPct(winRate, 1)} · ${fmtNum(avgHolding, 2)}${INVESTOR_ZH ? " 天" : "d"}`
      : "—";
  const winSub = summary ? sinceLine : i18n("Loading…", "載入中…");
  const swapSection = overviewProfitSectionHtml(ctx);
  return `<div class="inv-dashboard">
    <section class="inv-panel inv-panel--hero" aria-label="${i18n("Portfolio summary", "投資組合摘要")}">
      <div class="inv-split">
        <div class="inv-kpi inv-kpi--equity">
          <span class="inv-kpi-label">${i18n("Total equity", "總權益")}</span>
          <span class="inv-kpi-value inv-kpi-value--hero font-mono tabular-nums">${fmtUsd(totalEquity)}</span>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${i18n("Total profit", "累計獲利")}</span>
          <span class="inv-kpi-value inv-kpi-value--hero font-mono tabular-nums ${pnlClass(lifetimePnl)}">${summary ? fmtUsd(lifetimePnl) : "—"}</span>
          ${summary ? fmtAprBlockHtml(lifetimeApr, "APR", "mobile") : `<span class="inv-kpi-foot">${winSub}</span>`}
        </div>
      </div>
    </section>

    ${overviewCompositionGridHtml(ctx)}

    <div class="inv-stat-row">
      <div class="inv-stat">
        <span class="inv-stat-label">${i18n("Open credit", "未實現權利金")}</span>
        <span class="inv-stat-value font-mono tabular-nums">${fmtUsd(openCredit)}</span>
        <div class="inv-mini-list">${investorOpenCreditMiniHtml(creditByStrategy)}</div>
      </div>
      <div class="inv-stat">
        <span class="inv-stat-label">${i18n("Win rate", "勝率")}</span>
        <span class="inv-stat-value font-mono tabular-nums">${winHold}</span>
        <span class="inv-kpi-foot">${winSub}</span>
      </div>
    </div>

    ${swapSection}
  </div>`;
}

/** Deribit-style: (₿)/(♦)/($)/(₮) prefix for option premium / mark. */
export function fmtDeribitPriceCell(value, collateralCurrency) {
  const n = num(value);
  if (n === null) return "—";
  const c = String(collateralCurrency || "").toUpperCase();
  if (!isKnownDeribitCollateral(c)) return fmtNum(n, 4);
  return `${deribitSymbolHtml(c)}\u00A0${fmtNum(n, deribitPricePlaces(c))}`;
}

/** Plain-text premium for activity meta lines (no inline HTML). */
export function fmtDeribitPricePlain(value, collateralCurrency) {
  const n = num(value);
  if (n === null) return "—";
  const c = String(collateralCurrency || "").toUpperCase();
  if (!isKnownDeribitCollateral(c)) return fmtNum(n, 4);
  return `${bookNativeSymbol(c)} ${fmtNum(n, deribitPricePlaces(c))}`;
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

/**
 * Perp 避險合約名稱（與後端 ``_perp_instrument`` 一致：線性 USDC 永續）。
 * 例：``BTC`` → ``BTC_USDC-PERPETUAL``。
 */
export function hedgePerpInstrumentForCurrency(currency) {
  const cur = String(currency || "").toUpperCase();
  if (!cur) return "";
  return `${cur}_USDC-PERPETUAL`;
}

/** True when hedge summary carries no displayable lifetime PnL. */
export function hedgePnlSummaryIsEmpty(hedge) {
  if (!hedge || typeof hedge !== "object") return true;
  const tradeCount = num(hedge.trade_count);
  const net = num(hedge.net_pnl_usdc);
  if (tradeCount !== null && tradeCount > 0) return false;
  return net === null || Math.abs(net) < 0.005;
}

/** Lifetime realized perp-hedge PnL from trade journal (engine status payload). */
export function hedgeLifetimePnlSummary(status) {
  const raw = status?.hedge_pnl_summary;
  if (!raw || typeof raw !== "object") return null;
  const tradeCount = num(raw.trade_count);
  const netPnlUsd = num(raw.net_pnl_usdc);
  if (hedgePnlSummaryIsEmpty(raw)) return null;
  return {
    tradeCount: tradeCount ?? 0,
    realizedPnlUsd: num(raw.realized_pnl_usdc),
    feesUsd: num(raw.fees_usdc),
    netPnlUsd,
    byCurrency: raw.by_currency || {},
    windowNetPnlByDays: raw.window_net_pnl_by_days || {},
  };
}

/** Net realized perp-hedge PnL included in performance totals (lifetime). */
export function hedgeLifetimeNetPnlUsd(status) {
  const summary = hedgeLifetimePnlSummary(status);
  if (summary?.netPnlUsd !== null && summary?.netPnlUsd !== undefined) {
    return summary.netPnlUsd;
  }
  const raw = status?.hedge_pnl_summary;
  if (!raw || typeof raw !== "object") return null;
  return num(raw.net_pnl_usdc);
}

/** Prefer live status hedge summary; fall back to realized report summary. */
export function resolveHedgeNetPnlUsd(status, report) {
  const fromStatus = hedgeLifetimeNetPnlUsd(status);
  if (fromStatus !== null) return fromStatus;
  return num(report?.summary?.hedge_net_pnl_usdc);
}

/** Net realized perp-hedge PnL for a rolling window (matches chart APR windows). */
export function hedgeWindowNetPnlUsd(status, windowDays) {
  const summary = hedgeLifetimePnlSummary(status);
  if (!summary) return null;
  const days = Math.round(windowDays ?? 30);
  const raw = summary.windowNetPnlByDays?.[String(days)];
  return raw === undefined || raw === null ? null : num(raw);
}

/** Per-book lifetime net perp-hedge PnL (USDC) for profit composition. */
export function hedgeUsdByBook(status) {
  const summary = hedgeLifetimePnlSummary(status);
  const out = { BTC: 0, ETH: 0, USDC: 0 };
  if (!summary) return out;
  for (const [cur, row] of Object.entries(summary.byCurrency || {})) {
    const book = String(cur).toUpperCase();
    if (!(book in out)) continue;
    const net = num(row?.net_pnl_usdc);
    if (net !== null) out[book] = net;
  }
  return out;
}

/**
 * 彙整某幣別的避險 perp 部位（跨帳戶合計）。無部位時回 ``null``。
 * 避險是以「幣別」為單位中和整個帳本淨 delta，故同帳多筆合併計算。
 */
export function hedgePerpAggregateForCurrency(status, currency) {
  const name = hedgePerpInstrumentForCurrency(currency);
  if (!name) return null;
  const rows = status?.positions || [];
  let signedSize = 0;
  let notionalUsd = 0;
  let pnlUsd = 0;
  let hasPnl = false;
  let markPrice = null;
  let matched = 0;
  for (const p of rows) {
    if (String(p.instrument_name || "") !== name) continue;
    const sz = openRowPositionSignedSizeForDisplay(p);
    if (sz === null) continue;
    matched += 1;
    signedSize += sz;
    const mark = num(p.mark_price);
    if (mark !== null && mark > 0) {
      markPrice = mark;
      notionalUsd += sz * mark;
    }
    const fpl = num(p.floating_profit_loss_usd);
    if (fpl !== null) {
      pnlUsd += fpl;
      hasPnl = true;
    }
  }
  if (!matched || signedSize === 0) return null;
  return {
    currency: String(currency || "").toUpperCase(),
    instrumentName: name,
    signedSize,
    notionalUsd,
    markPrice,
    pnlUsd: hasPnl ? pnlUsd : null,
    side: signedSize < 0 ? "short" : "long",
  };
}

/**
 * 目前開倉部位涉及、且有實際避險 perp 的幣別彙總列。
 * 每列含：避險 perp 部位、該幣別選擇權合計未實現 PnL，以及「含避險淨額」
 * （選擇權 PnL + perp PnL，依需求把避險損益併入該幣別策略合計）。
 */
export function activeHedgeSummaryRows(status, openRows, groups = null) {
  const ctx = groups ?? STATE.groups;
  const rows = openRows || [];
  const byCurrency = new Map();
  for (const g of rows) {
    const cur = String(g.currency || "").toUpperCase();
    if (!cur) continue;
    if (!byCurrency.has(cur)) byCurrency.set(cur, []);
    byCurrency.get(cur).push(g);
  }
  const out = [];
  for (const [cur, groupRows] of byCurrency) {
    const hedge = hedgePerpAggregateForCurrency(status, cur);
    if (!hedge) continue;
    let optionsPnlUsd = 0;
    let hasOptionsPnl = false;
    for (const g of groupRows) {
      const v = openRowDisplayUnrealizedUsd(g, status, ctx);
      if (v !== null && v !== undefined) {
        optionsPnlUsd += v;
        hasOptionsPnl = true;
      }
    }
    const perpPnl = hedge.pnlUsd;
    const hasNet = hasOptionsPnl || perpPnl !== null;
    out.push({
      ...hedge,
      optionGroupCount: groupRows.length,
      optionsPnlUsd: hasOptionsPnl ? optionsPnlUsd : null,
      netPnlUsd: hasNet
        ? (hasOptionsPnl ? optionsPnlUsd : 0) + (perpPnl !== null ? perpPnl : 0)
        : null,
    });
  }
  out.sort((a, b) => a.currency.localeCompare(b.currency));
  return out;
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
let _sharedLegCounts = null;
let _sharedLegCountsStatus = null;
let _sharedLegCountsGroups = null;

function sharedLegCounts(status, groups) {
  if (_sharedLegCounts && _sharedLegCountsStatus === status && _sharedLegCountsGroups === groups) {
    return _sharedLegCounts;
  }
  const counts = new Map();
  const seen = new Set();
  for (const src of [status?.trade_groups || [], groups?.open || []]) {
    for (const row of src) {
      if (!isOpenTradeGroup(row)) continue;
      const key = tradeGroupKey(row);
      if (seen.has(key)) continue;
      seen.add(key);
      const account = String(row?.account_name || "");
      for (const legRole of ["short", "long"]) {
        const instrument = openRowLegInstrumentName(row, legRole);
        if (!instrument) continue;
        const mapKey = `${account}|${legRole}|${instrument}`;
        counts.set(mapKey, (counts.get(mapKey) || 0) + 1);
      }
    }
  }
  _sharedLegCounts = counts;
  _sharedLegCountsStatus = status;
  _sharedLegCountsGroups = groups;
  return counts;
}

export function countOpenGroupsSharingLeg(status, groups, g, role) {
  const instrument = openRowLegInstrumentName(g, role);
  if (!instrument) return 0;
  const account = String(g?.account_name || "");
  return sharedLegCounts(status, groups).get(`${account}|${role}|${instrument}`) || 0;
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
  return `${deribitSymbolHtml("USDC")}\u00A0${body}`;
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
  if (mtm === null) return "—";
  if (coll === "USDC") return fmtUsdcUnrealizedDeribit(mtm);
  if (isKnownDeribitCollateral(coll)) {
    return `${deribitSymbolHtml(coll)}\u00A0${fmtNum(mtm, 8)}`;
  }
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
  if (places >= 8) return fmt.num8.format(n);
  if (places === 4) return fmt.num4.format(n);
  if (places === 5) {
    if (!fmt.num5) {
      fmt.num5 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 5 });
    }
    return fmt.num5.format(n);
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: places }).format(n);
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
  if (b === "USDC" || b === "USDT") return native;
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

export function fmtNativeBookBreakdown(byBook, { places = { BTC: 5, ETH: 4, USDC: 2, USDT: 2 }, pnl = false } = {}) {
  const items = CORE_BOOKS.map((book) => {
    const n = num(byBook[book]);
    const text = n === null ? "—" : fmtNum(n, places[book] ?? 4);
    const cls = pnl ? ` ${pnlClass(byBook[book])}` : "";
    return `<span class="native-book-item">${bookNativeSymbolHtml(book)} <span class="font-mono tabular-nums${cls}">${text}</span></span>`;
  });
  return `<span class="native-book-breakdown">${items.join("")}</span>`;
}

export function fmtLifetimeRealizedNativeBreakdown(byBook) {
  return fmtNativeBookBreakdown(byBook, { pnl: true });
}

const PROFIT_HELD_BOOKS = ["BTC", "ETH", "USDC"];
const PROFIT_SWEEP_BOOKS = ["BTC", "ETH"];
const PROFIT_USD_PLACES = 3;
const PROFIT_NATIVE_INTERNAL_PLACES = 8;
const PROFIT_DISP_PLACES = { BTC: 6, ETH: 6, USDC: PROFIT_USD_PLACES, USDT: PROFIT_USD_PLACES };

/** Exchange fill qty for closed-trade display (falls back to journal amount). */
export function profitSweepExchangeNativeSold(g, book) {
  const exchange = num(g?.profit_sweep_exchange_native);
  if (exchange !== null && exchange > 0) return exchange;
  const journal = num(g?.profit_sweep_amount);
  if (journal === null || journal <= 0) return null;
  if (!profitSweepHasExchangeFill(g)) return journal;
  return journal;
}

/** Truncate toward zero — profit-swap display never rounds up vs exchange/journal math. */
export function truncateDecimal(value, places) {
  const n = num(value);
  if (n === null) return null;
  if (places <= 0) return Math.trunc(n);
  const factor = 10 ** places;
  return Math.trunc(n * factor) / factor;
}

/** Truncate a decimal literal toward zero without float drift (prefers string digits). */
export function truncateDecimalLiteral(value, places) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string") {
    const s = value.trim();
    if (!s) return null;
    const m = s.match(/^(-?)(\d+)(?:\.(\d*))?$/);
    if (m) {
      const [, sign, intPart, frac = ""] = m;
      const clipped = frac.slice(0, Math.max(0, places));
      const text = clipped.length ? `${sign}${intPart}.${clipped}` : `${sign}${intPart}`;
      const n = Number(text);
      return Number.isFinite(n) ? n : null;
    }
  }
  return truncateDecimal(value, places);
}

/** Format stored decimal for display: truncate toward zero, strip trailing zeros, no Intl rounding. */
function fmtDecimalLiteral(value, places) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string") {
    const s = value.trim();
    if (!s) return null;
    const m = s.match(/^(-?)(\d+)(?:\.(\d*))?$/);
    if (m) {
      const [, sign, intPart, frac = ""] = m;
      const clipped = frac.slice(0, Math.max(0, places));
      const text = clipped.length ? `${sign}${intPart}.${clipped}` : `${sign}${intPart}`;
      return stripTrailingZeros(text);
    }
  }
  const t = truncateDecimal(value, places);
  if (t === null) return null;
  return stripTrailingZeros(t.toFixed(places));
}

function profitNativePrecision(book) {
  const b = String(book || "").toUpperCase();
  if (b === "BTC" || b === "ETH") return PROFIT_DISP_PLACES.BTC;
  if (b === "USDC" || b === "USDT") return PROFIT_USD_PLACES;
  return 6;
}

function profitUsdPlaces() {
  return PROFIT_USD_PLACES;
}

function profitInternalNativePrecision(_book) {
  return PROFIT_NATIVE_INTERNAL_PLACES;
}

/** Strip trailing zeros after a fixed-decimal string (keeps all significant digits). */
function stripTrailingZeros(fixed) {
  if (!fixed.includes(".")) return fixed;
  return fixed.replace(/0+$/, "").replace(/\.$/, "");
}

/** Convert a decimal to integer units at `places` without float drift. */
function decimalToUnits(value, places) {
  if (value === null || value === undefined || value === "") return 0;
  let text;
  if (typeof value === "string") {
    const s = value.trim();
    const m = s.match(/^(-?)(\d+)(?:\.(\d*))?$/);
    if (!m) return 0;
    const [, sign, intPart, frac = ""] = m;
    const clipped = frac.slice(0, Math.max(0, places));
    text = clipped.length ? `${sign}${intPart}.${clipped}` : `${sign}${intPart}`;
  } else {
    const n = num(value);
    if (n === null) return 0;
    const raw = stripTrailingZeros(n.toFixed(12));
    const m = raw.match(/^(-?)(\d+)(?:\.(\d*))?$/);
    if (!m) return 0;
    const [, sign, intPart, frac = ""] = m;
    const clipped = frac.slice(0, Math.max(0, places));
    text = clipped.length ? `${sign}${intPart}.${clipped}` : `${sign}${intPart}`;
  }
  const neg = text.startsWith("-");
  const body = neg ? text.slice(1) : text;
  const [intPart, frac = ""] = body.split(".");
  const fracPadded = (frac + "0".repeat(places)).slice(0, places);
  const units = BigInt(intPart || "0") * BigInt(10 ** places) + BigInt(fracPadded || "0");
  const n = Number(units);
  return neg ? -n : n;
}

/** Subtract decimals at fixed precision without float drift (truncates result toward zero). */
function subtractDecimals(a, b, places) {
  const factor = 10 ** places;
  const diff = decimalToUnits(a, places) - decimalToUnits(b, places);
  return Math.max(0, diff / factor);
}

/** Native BTC/ETH qty in profit-swap panel: up to 6 dp, truncated not rounded. */
export function fmtProfitNative(book, value) {
  const places = profitNativePrecision(book);
  const text = fmtDecimalLiteral(value, places);
  return text === null ? "—" : text;
}

/** USDT in profit-swap panel: up to 3 dp, truncated (no Intl rounding). */
export function fmtProfitUsdt(value) {
  const text = fmtDecimalLiteral(value, profitUsdPlaces());
  if (text === null) return "—";
  return `$${text}`;
}

function profitAvgUsdPlaces(_book) {
  return profitUsdPlaces();
}

/** Execution avg in profit-swap panel: truncated (no Intl rounding). */
export function fmtProfitAvgUsd(book, value) {
  const places = profitAvgUsdPlaces(book);
  const t = truncateDecimal(value, places);
  if (t === null) return "—";
  return `$${stripTrailingZeros(t.toFixed(places))}`;
}

/** Avg from full quote ÷ native; USD result truncated to display precision. */
export function profitSwapDisplayAvg(book, quote, sold) {
  const q = num(quote);
  const s = truncateDecimalLiteral(sold, profitNativePrecision(book));
  if (q === null || s === null || s <= 0) return null;
  return truncateDecimal(q / s, profitUsdPlaces());
}

/** USD/USDC from stored engine fields: truncate toward zero, strip trailing zeros. */
export function fmtUsdPrecise(value, maxPlaces = profitUsdPlaces()) {
  const text = fmtDecimalLiteral(value, maxPlaces);
  if (text === null) return "—";
  return `$${text}`;
}

/** When live USDT wallet differs from summed lifetime swap proceeds, keep lifetime totals for display. */
export function alignProfitDispositionToUsdtWallet(disposition, _status) {
  return disposition;
}

/** True when native amount is large enough to show at book display precision. */
export function isMeaningfulNativeForBook(native, book) {
  const n = num(native);
  if (n === null || Math.abs(n) < 1e-12) return false;
  const places = PROFIT_DISP_PLACES[String(book || "").toUpperCase()] ?? 4;
  return Math.abs(n) >= 0.5 * 10 ** -places;
}

/** True when this group has an exchange-confirmed premium spot sell (not ledger pool split). */
export function profitSweepHasExchangeFill(g) {
  const status = String(g?.profit_sweep_status || "").toLowerCase();
  if (status !== "filled") return false;
  const orderId = String(g?.profit_sweep_order_id || "").trim();
  if (orderId) return true;
  const reason = String(g?.profit_sweep_reason || "").toLowerCase();
  if (reason.includes("exchange_fully_swept")) return true;
  if (reason.includes("unlabeled_premium_reconciled")) return true;
  if (reason.includes("manual_swap")) return true;
  if (reason.includes("dust_pool_sweep")) return true;
  // proceeds_reconciled alone = journal pool attribution, not a per-group fill.
  if (reason.includes("proceeds_reconciled")) return false;
  return true;
}

/** Lifetime USDT high-water from journal reconcile (may exceed actual fill quote). */
export function profitSweepLifetimeQuoteUsdt(g) {
  const lifetime = num(g?.profit_sweep_quote_proceeds_lifetime);
  if (lifetime !== null && lifetime > 0) return lifetime;
  const quote = num(g?.profit_sweep_quote_proceeds);
  const status = String(g?.profit_sweep_status || "").toLowerCase();
  if (status === "filled" && quote !== null && quote > 0) return quote;
  return null;
}

/** USDT actually received from premium swap fills (exchange quote, not reconcile high-water). */
export function profitSweepRealizedQuoteUsdt(g) {
  if (!profitSweepHasExchangeFill(g)) return null;
  const quote = num(g?.profit_sweep_quote_proceeds);
  if (quote !== null && quote > 0) return quote;
  return profitSweepLifetimeQuoteUsdt(g);
}

/** Exchange VWAP stats from status (premium-sweep spot fills). */
export function premiumSweepFillStatsByBook(status) {
  return status?.premium_sweep_fill_stats_by_book ?? null;
}

/** Merge live status; retain exchange fill stats when a fast refresh omits them. */
export function mergeStatusPayload(prev, next) {
  if (!next || typeof next !== "object") return prev ?? next ?? null;
  const merged = { ...(prev && typeof prev === "object" ? prev : {}), ...next };
  const nextFill = premiumSweepFillStatsByBook(next);
  const prevFill = premiumSweepFillStatsByBook(prev);
  if (!nextFill && prevFill) {
    merged.premium_sweep_fill_stats_by_book = prevFill;
  }
  const nextHedge = next.hedge_pnl_summary;
  const prevHedge = prev?.hedge_pnl_summary;
  if (hedgePnlSummaryIsEmpty(nextHedge) && !hedgePnlSummaryIsEmpty(prevHedge)) {
    merged.hedge_pnl_summary = prevHedge;
  }
  return merged;
}

/** Manual / pre-label premium swaps excluded from labeled exchange reconcile pool. */
export function isPremiumProceedsPoolExcludedGroup(g) {
  const reason = String(g?.profit_sweep_reason || "").toLowerCase();
  if (reason.includes("unlabeled_premium_reconciled")) return true;
  if (reason.includes("manual_swap")) return true;
  return false;
}

/** Book-level premium-sweep display: exchange net native/USDT when fills exist, else journal. */
export function resolvePremiumSweepBookDisplay({ journalSold, journalQuote, exchange, earned }) {
  const netUsdt = num(exchange?.net_usdt);
  const netNative = num(exchange?.net_native_sold);
  const hasExchange = netNative !== null && netNative > 0;

  let soldNative = journalSold;
  if (hasExchange) {
    soldNative = netNative;
  } else if (soldNative <= 0 && netNative !== null && netNative > 0) {
    soldNative = netNative;
  }
  // Journal attribution cap only when exchange fills are unavailable.
  if (!hasExchange && earned > 0 && soldNative > earned) {
    soldNative = earned;
  }

  let soldQuote = journalQuote;
  if (netUsdt !== null && netUsdt > 0) {
    soldQuote = netUsdt;
  }

  const avg =
    netUsdt !== null && netUsdt > 0 && netNative !== null && netNative > 0
      ? netUsdt / netNative
      : soldNative > 0 && soldQuote > 0
      ? soldQuote / soldNative
      : null;
  return { soldNative, soldQuote, avg };
}

/** Roll up held / pending / sold coin profit for the swap panel. */
export function summarizeProfitDisposition(disposition, { status = null } = {}) {
  if (!disposition) return null;
  const fillStats = premiumSweepFillStatsByBook(status ?? STATE.status);
  const spotEarned = {};
  const spotHeld = {};
  const spotPending = {};
  const spotSold = {};
  const spotSoldQuote = {};
  const spotSoldAvg = {};
  let hasPending = false;
  let hasSwap = false;
  let hasCoinProfit = false;
  for (const book of PROFIT_SWEEP_BOOKS) {
    const held = num(disposition.heldNative?.[book]) ?? 0;
    const pending = num(disposition.pendingSweepNative?.[book]) ?? 0;
    const journalSold = num(disposition.sweptNativeRef?.[book]) ?? 0;
    const journalQuote = num(disposition.sweptQuoteProceedsByBook?.[book]) ?? 0;
    spotHeld[book] = held;
    spotPending[book] = pending;

    const exchange = fillStats?.[book];
    const earned = held + pending + journalSold;
    const display = resolvePremiumSweepBookDisplay({
      journalSold,
      journalQuote,
      exchange,
      earned,
    });

    const excludedQuote = num(disposition.excludedSweptQuoteProceedsByBook?.[book]) ?? 0;
    const excludedNative = num(disposition.excludedSweptNativeRefByBook?.[book]) ?? 0;
    const displayUsdt = num(exchange?.display_usdt);
    const displayNative = num(exchange?.display_native_sold);
    if (displayNative !== null && displayNative > 0 && displayUsdt !== null && displayUsdt > 0) {
      spotSold[book] = displayNative;
      spotSoldQuote[book] = displayUsdt;
    } else {
      spotSold[book] = display.soldNative + excludedNative;
      spotSoldQuote[book] = display.soldQuote + excludedQuote;
    }
    if (spotSold[book] > 0 && spotSoldQuote[book] > 0) {
      spotSoldAvg[book] = profitSwapDisplayAvg(book, spotSoldQuote[book], spotSold[book]);
    }

    if (Math.abs(earned) > 0) {
      spotEarned[book] = earned;
      hasCoinProfit = true;
    }
    if (pending > 0) hasPending = true;
    if (spotSold[book] > 0 || spotSoldQuote[book] > 0) hasSwap = true;
  }
  for (const book of PROFIT_SWEEP_BOOKS) {
    const earned = num(spotEarned[book]) ?? 0;
    const sold = num(spotSold[book]) ?? 0;
    const pending = num(spotPending[book]) ?? 0;
    const places = profitInternalNativePrecision(book);
    const impliedRemainder = subtractDecimals(subtractDecimals(earned, sold, places), pending, places);
    if (impliedRemainder > 0 && isMeaningfulNativeForBook(impliedRemainder, book)) {
      spotHeld[book] = impliedRemainder;
    } else if (!isMeaningfulNativeForBook(spotHeld[book], book)) {
      spotHeld[book] = 0;
    }
  }
  const exchangeQuoteTotal = PROFIT_SWEEP_BOOKS.reduce(
    (sum, book) => sum + (num(spotSoldQuote[book]) ?? 0),
    0,
  );
  const journalUsdt = num(disposition.sweptUsdt) ?? 0;
  const usdtSwapped =
    fillStats && exchangeQuoteTotal > 0.005 ? exchangeQuoteTotal : journalUsdt;
  if (usdtSwapped > 0) hasSwap = true;
  const displayUsdtSwapped = usdtSwapped;
  const usdcHeld = num(disposition.heldNative?.USDC) ?? 0;
  return {
    spotEarned,
    spotHeld,
    spotPending,
    spotSold,
    spotSoldQuote,
    spotSoldAvg,
    usdcHeld,
    usdtSwapped: displayUsdtSwapped,
    hasPending,
    hasSwap,
    hasCoinProfit,
  };
}

/** Per-book USD: swapped USDT + unswept native × live spot; USDC uses held realized. */
export function realizedUsdByBookFromProfitDisposition(disposition, status) {
  const out = { BTC: 0, ETH: 0, USDC: 0 };
  if (!disposition) return null;
  const summary = summarizeProfitDisposition(disposition, { status });
  if (!summary) return null;
  let any = false;
  for (const book of PROFIT_SWEEP_BOOKS) {
    const swapped = num(summary.spotSoldQuote?.[book]) ?? 0;
    const held = num(summary.spotHeld?.[book]) ?? 0;
    const pending = num(summary.spotPending?.[book]) ?? 0;
    const unswept = held + pending;
    const spot = spotUsdForBook(status, book);
    const unsweptUsd =
      spot !== null && spot > 0 && isMeaningfulNativeForBook(unswept, book) ? unswept * spot : 0;
    if (swapped > 0.005 || Math.abs(unsweptUsd) >= 0.005) {
      out[book] = swapped + unsweptUsd;
      any = true;
    }
  }
  const usdc = num(disposition.heldNative?.USDC) ?? 0;
  if (Math.abs(usdc) >= 0.005) {
    out.USDC = usdc;
    any = true;
  }
  return any ? out : null;
}

/** Total profit: Σ (swapped USDT + unswept native × live spot) + USDC realized. */
export function realizedUsdFromProfitDisposition(disposition, status) {
  const byBook = realizedUsdByBookFromProfitDisposition(disposition, status);
  if (!byBook) return null;
  return PROFIT_HELD_BOOKS.reduce((sum, book) => sum + (num(byBook[book]) ?? 0), 0);
}

function fmtProfitDispositionCoinValues(byBook, books, { abs = false, showSign = false } = {}) {
  return books
    .filter((book) => {
      const n = num(byBook?.[book]);
      return n !== null && n !== 0;
    })
    .map((book) => {
      const raw = num(byBook[book]);
      const display = abs && raw !== null ? Math.abs(raw) : raw;
      const prefix = showSign && raw !== null && raw > 0 ? "+" : "";
      const text = display === null ? "—" : `${prefix}${fmtProfitNative(book, display)}`;
      const cls = raw === null ? "" : pnlClass(raw);
      return `<span class="native-book-item">${bookNativeSymbolHtml(book)} <span class="font-mono tabular-nums ${cls}">${text}</span></span>`;
    })
    .join("");
}

function fmtProfitDispositionRow(label, valuesHtml, { rowClass = "" } = {}) {
  if (!valuesHtml) return "";
  const cls = rowClass ? ` ${rowClass}` : "";
  return `<div class="profit-disposition-row${cls}">
    <span class="profit-disposition-label">${label}</span>
    <span class="profit-disposition-values native-book-breakdown">${valuesHtml}</span>
  </div>`;
}

function fmtProfitDispositionSoldValues(summary) {
  return PROFIT_SWEEP_BOOKS.filter((book) => {
    const sold = num(summary.spotSold[book]);
    return sold !== null && sold > 0;
  })
    .map((book) => {
      const sold = num(summary.spotSold[book]);
      const soldText = fmtProfitNative(book, sold);
      const quote = summary.spotSoldQuote[book];
      const usdtHtml = fmtProfitSweepUsdtProceedsHtml(quote);
      const avgHtml = fmtProfitSweepAvgPriceHtml(book, quote, sold, summary.spotSoldAvg?.[book]);
      return `<span class="native-book-item">${bookNativeSymbolHtml(book)} <span class="font-mono tabular-nums pnl-neg">-${soldText}</span>${usdtHtml}${avgHtml}</span>`;
    })
    .join("");
}

function fmtProfitSwapRemainingValues(summary) {
  const parts = [];
  for (const book of PROFIT_SWEEP_BOOKS) {
    const held = num(summary.spotHeld[book]) ?? 0;
    const pending = num(summary.spotPending[book]) ?? 0;
    if (held !== 0) {
      const prefix = held > 0 ? "+" : "";
      parts.push(
        `<span class="native-book-item">${bookNativeSymbolHtml(book)} <span class="font-mono tabular-nums ${pnlClass(held)}">${prefix}${fmtProfitNative(book, held)}</span></span>`
      );
    }
    if (pending > 0) {
      parts.push(
        `<span class="native-book-item profit-swap-pending-item">${bookNativeSymbolHtml(book)} <span class="font-mono tabular-nums text-amber-200/90">${fmtProfitNative(book, pending)}</span><span class="profit-disposition-pending-arrow">${i18n("pending", "待兌")}</span></span>`
      );
    }
  }
  return parts.join("");
}

function profitSwapActiveBooks(summary) {
  return PROFIT_SWEEP_BOOKS.filter((book) => {
    const earned = num(summary.spotEarned?.[book]) ?? 0;
    const sold = num(summary.spotSold?.[book]) ?? 0;
    const held = num(summary.spotHeld?.[book]) ?? 0;
    const pending = num(summary.spotPending?.[book]) ?? 0;
    return earned !== 0 || sold > 0 || held !== 0 || pending > 0;
  });
}

function fmtProfitSwapEmptySlot() {
  return `<div class="profit-swap-book-slot profit-swap-book-slot--pad" aria-hidden="true"><div class="profit-swap-book-slot-main">&nbsp;</div><div class="profit-swap-book-slot-sub">&nbsp;</div></div>`;
}

function fmtProfitSwapEarnedSlot(summary, book) {
  const raw = num(summary.spotEarned?.[book]);
  if (raw === null || raw === 0) return fmtProfitSwapEmptySlot();
  const prefix = raw > 0 ? "+" : "";
  const cls = pnlClass(raw);
  return `<div class="profit-swap-book-slot">
    <div class="profit-swap-book-slot-main">${bookNativeSymbolHtml(book)} <span class="font-mono tabular-nums ${cls}">${prefix}${fmtProfitNative(book, raw)}</span></div>
    <div class="profit-swap-book-slot-sub profit-swap-book-slot-sub--empty" aria-hidden="true">&nbsp;</div>
  </div>`;
}

function fmtProfitSwapSoldSlot(summary, book) {
  const sold = num(summary.spotSold?.[book]);
  if (sold === null || sold <= 0) return fmtProfitSwapEmptySlot();
  const quote = num(summary.spotSoldQuote?.[book]);
  const subParts = [];
  if (quote !== null && quote > 0) {
    subParts.push(`<span class="font-mono tabular-nums pnl-pos">${fmtProfitUsdt(quote)} USDT</span>`);
    const avgOverride = num(summary.spotSoldAvg?.[book]);
    const avg =
      avgOverride !== null && avgOverride > 0
        ? avgOverride
        : profitSwapDisplayAvg(book, quote, sold);
    subParts.push(
      `<span class="profit-sweep-avg-price font-mono tabular-nums">${i18n("avg", "均價")} ${fmtProfitAvgUsd(book, avg)}</span>`
    );
  }
  return `<div class="profit-swap-book-slot">
    <div class="profit-swap-book-slot-main">${bookNativeSymbolHtml(book)} <span class="font-mono tabular-nums pnl-neg">-${fmtProfitNative(book, sold)}</span></div>
    <div class="profit-swap-book-slot-sub">${subParts.join('<span class="profit-swap-book-slot-sep" aria-hidden="true">·</span>')}</div>
  </div>`;
}

function fmtProfitSwapRemainingSlot(summary, book) {
  const held = num(summary.spotHeld?.[book]) ?? 0;
  const pending = num(summary.spotPending?.[book]) ?? 0;
  if (held === 0 && pending === 0) return fmtProfitSwapEmptySlot();
  if (pending > 0 && held === 0) {
    return `<div class="profit-swap-book-slot">
      <div class="profit-swap-book-slot-main">${bookNativeSymbolHtml(book)} <span class="font-mono tabular-nums text-amber-200/90">${fmtProfitNative(book, pending)}</span></div>
      <div class="profit-swap-book-slot-sub"><span class="profit-disposition-pending-arrow">${i18n("pending", "待兌")}</span></div>
    </div>`;
  }
  const prefix = held > 0 ? "+" : "";
  const pendingSub =
    pending > 0
      ? `<span class="profit-swap-pending-item"><span class="font-mono tabular-nums text-amber-200/90">${fmtProfitNative(book, pending)}</span> <span class="profit-disposition-pending-arrow">${i18n("pending", "待兌")}</span></span>`
      : `<span class="profit-swap-book-slot-sub--empty" aria-hidden="true">&nbsp;</span>`;
  return `<div class="profit-swap-book-slot">
    <div class="profit-swap-book-slot-main">${bookNativeSymbolHtml(book)} <span class="font-mono tabular-nums ${pnlClass(held)}">${prefix}${fmtProfitNative(book, held)}</span></div>
    <div class="profit-swap-book-slot-sub">${pendingSub}</div>
  </div>`;
}

function fmtProfitSwapColumnHtml(summary, label, books, slotFn) {
  const slots = books.map((book) => slotFn(summary, book)).join("");
  return fmtProfitSwapLedgerRow(label, slots);
}

function fmtProfitSwapLedgerRow(label, valuesHtml, { emptyText = "—" } = {}) {
  const value = valuesHtml
    ? `<div class="profit-swap-col-values native-book-breakdown">${valuesHtml}</div>`
    : `<div class="profit-swap-col-empty font-mono tabular-nums">${emptyText}</div>`;
  return `<div class="profit-swap-col">
    <span class="profit-swap-col-label">${label}</span>
    ${value}
  </div>`;
}

/** Profit-sweep panel: USDT headline + earned / sold / remaining ledger. */
export function fmtProfitSwapPanel(disposition, summary = null) {
  if (!disposition) return "";
  const totals = summary ?? summarizeProfitDisposition(disposition, { status: STATE.status });
  if (!totals || (!totals.hasSwap && !totals.hasPending)) return "";

  const activeBooks = profitSwapActiveBooks(totals);
  if (!activeBooks.length) return "";

  const walletUsdt = bookEquityNative(STATE.status, "USDT");
  const journalUsdt = totals.usdtSwapped > 0 ? totals.usdtSwapped : null;
  const displayUsdt =
    walletUsdt !== null &&
    journalUsdt !== null &&
    !totals.hasPending &&
    walletUsdt > journalUsdt + 0.5
      ? walletUsdt
      : journalUsdt;
  const usdtHeroClass =
    displayUsdt !== null && displayUsdt > 0
      ? "pnl-pos"
      : totals.hasPending
      ? "profit-swap-hero-pending"
      : "";
  const usdtHeroText =
    displayUsdt !== null && displayUsdt > 0
      ? fmtProfitUsdt(displayUsdt)
      : totals.hasPending
      ? i18n("Pending", "待兌")
      : "—";

  const walletFootnote =
    walletUsdt !== null &&
    journalUsdt !== null &&
    journalUsdt > 0.005 &&
    walletUsdt + 0.01 < journalUsdt
      ? `<p class="profit-swap-wallet-footnote text-slate-500">${i18n(
          "USDT wallet now (withdrawals do not reduce lifetime total above)",
          "USDT 錢包現餘（提領不影響上方累計）"
        )}: <span class="font-mono tabular-nums">${fmtUsd(walletUsdt)}</span></p>`
      : walletUsdt !== null &&
        journalUsdt !== null &&
        !totals.hasPending &&
        walletUsdt > journalUsdt + 0.5
      ? `<p class="profit-swap-wallet-footnote text-slate-500">${i18n(
          "Includes pre-label premium swaps in wallet not yet split per trade group",
          "含尚未逐筆分攤的早期現貨兌換 USDT"
        )}: <span class="font-mono tabular-nums">${fmtUsd(walletUsdt)}</span></p>`
      : "";

  const detailRows = [
    fmtProfitSwapColumnHtml(totals, i18n("Earned", "兌換前"), activeBooks, fmtProfitSwapEarnedSlot),
    fmtProfitSwapColumnHtml(totals, i18n("Sold", "已賣出"), activeBooks, fmtProfitSwapSoldSlot),
    fmtProfitSwapColumnHtml(totals, i18n("Remaining", "剩餘"), activeBooks, fmtProfitSwapRemainingSlot),
  ];

  const detailsHtml = detailRows.length
    ? `<div class="profit-swap-detail">${detailRows.join("")}</div>`
    : "";

  return `<div class="profit-disposition-panel profit-swap-panel">
    <div class="profit-swap-kpi">
      <span class="profit-swap-kpi-label">${i18n("USDT received", "兌得 USDT")}</span>
      <span class="profit-swap-kpi-value font-mono tabular-nums ${usdtHeroClass}">${usdtHeroText}</span>
    </div>
    ${walletFootnote}
    ${detailsHtml}
  </div>`;
}

/** Realized P&L meta: simple book chips, or full swap panel when profit sweep applies. */
export function fmtRealizedProfitBreakdown(disposition, nativeByBookFallback) {
  if (!disposition) {
    return `<div class="overview-metric-line">${fmtHeldProfitBreakdown(nativeByBookFallback)}</div>`;
  }
  const summary = summarizeProfitDisposition(disposition, { status: STATE.status });
  if (summary?.hasSwap || summary?.hasPending) {
    const panel = fmtProfitSwapPanel(disposition, summary);
    if (panel) return panel;
  }
  const held = disposition.heldNative ?? nativeByBookFallback;
  return `<div class="overview-metric-line">${fmtHeldProfitBreakdown(held)}</div>`;
}

/** Per closed group: held spot profit vs profit-sweep disposition (coin books only). */
export function profitDispositionForGroup(g, status) {
  const book = tradeGroupAprBook(g);
  const native = realizedPnlInAprBookNative(g, status);
  if (native === null || native === 0) return null;
  if (book === "USDC") {
    return { held: native, pending: 0, sweptNative: 0, sweptUsdt: 0, book: "USDC" };
  }
  if (book !== "BTC" && book !== "ETH") return null;
  if (native <= 0) {
    return { held: native, pending: 0, sweptNative: 0, sweptUsdt: 0, book };
  }
  const sweep = String(g?.profit_sweep_status || "").toLowerCase();
  const sweepAmtRaw = num(g?.profit_sweep_amount);
  const sweptUsdt = profitSweepRealizedQuoteUsdt(g) ?? 0;
  const sweepAmt = sweepAmtRaw !== null && sweepAmtRaw > 0 ? sweepAmtRaw : native;

  if (sweep === "filled") {
    if (!profitSweepHasExchangeFill(g)) {
      return { held: native, pending: 0, sweptNative: 0, sweptUsdt: 0, book };
    }
    const sweptNative = Math.min(sweepAmt, native);
    let remainder = Math.max(0, native - sweptNative);
    if (!isMeaningfulNativeForBook(remainder, book)) remainder = 0;
    return { held: remainder, pending: 0, sweptNative, sweptUsdt, book };
  }
  if (sweep === "pending" || sweep === "submitted") {
    // amount < native: prior partial sweep done; only remainder is queued.
    if (sweepAmtRaw !== null && sweepAmtRaw > 0 && sweepAmtRaw < native) {
      const remainder = Math.max(0, native - sweepAmtRaw);
      return {
        held: 0,
        pending: remainder,
        sweptNative: sweepAmtRaw,
        sweptUsdt,
        book,
      };
    }
    return {
      held: 0,
      pending: sweepAmt,
      sweptNative: 0,
      sweptUsdt: 0,
      book,
    };
  }
  return { held: native, pending: 0, sweptNative: 0, sweptUsdt: 0, book };
}

export function emptyProfitDisposition() {
  return {
    heldNative: { BTC: 0, ETH: 0, USDC: 0 },
    pendingSweepNative: { BTC: 0, ETH: 0 },
    sweptNativeRef: { BTC: 0, ETH: 0 },
    sweptQuoteProceedsByBook: { BTC: 0, ETH: 0 },
    excludedSweptNativeRefByBook: { BTC: 0, ETH: 0 },
    excludedSweptQuoteProceedsByBook: { BTC: 0, ETH: 0 },
    sweptUsdt: 0,
  };
}

export function fmtHeldProfitBreakdown(heldNative) {
  const places = { BTC: 5, ETH: 4, USDC: 2 };
  const items = PROFIT_HELD_BOOKS.filter((book) => {
    const n = num(heldNative?.[book]);
    return n !== null && n !== 0;
  }).map((book) => {
    const n = heldNative[book];
    const text = fmtNum(n, places[book] ?? 4);
    const cls = pnlClass(n);
    return `<span class="native-book-item">${bookNativeSymbolHtml(book)} <span class="font-mono tabular-nums ${cls}">${text}</span></span>`;
  });
  if (!items.length) {
    return `<span class="text-slate-500">${i18n("—", "—")}</span>`;
  }
  return `<span class="native-book-breakdown">${items.join("")}</span>`;
}

export function fmtProfitSweepUsdtProceedsHtml(quoteProceeds) {
  const quote = num(quoteProceeds);
  if (quote === null || quote <= 0) return "";
  return `<span class="profit-sweep-usdt-proceeds font-mono tabular-nums pnl-pos">· ${fmtProfitUsdt(quoteProceeds)} USDT</span>`;
}

export function fmtProfitSweepAvgPriceHtml(book, quoteProceeds, nativeSold, avgOverride = null) {
  const override = num(avgOverride);
  const quote = num(quoteProceeds);
  const native = num(nativeSold);
  const avg =
    override !== null && override > 0
      ? override
      : profitSwapDisplayAvg(book, quoteProceeds, nativeSold);
  if (avg === null) return "";
  return `<span class="profit-sweep-avg-price text-slate-500 font-mono tabular-nums">· ${i18n("avg", "均價")} ${fmtProfitAvgUsd(book, avg)}</span>`;
}

export function fmtProfitSwappedLine(disposition) {
  return fmtProfitSwapPanel(disposition);
}

export function fmtBookEquityNativeBreakdown(byBook) {
  return fmtNativeBookBreakdown(byBook);
}

/** Total-equity KPI: per-book native amount and USDC equivalent (matches book cards). */
export function fmtBookEquityDualBreakdown(nativeByBook, usdByBook, _totalEquity = null) {
  const places = { BTC: 5, ETH: 4, USDC: 2, USDT: 2 };
  const rows = CORE_BOOKS.map((book) => {
      const native = num(nativeByBook?.[book]);
      const usd = num(usdByBook?.[book]);
      const isStable = book === "USDC" || book === "USDT";
      const usdVal = usd ?? native ?? 0;
      const sym = bookNativeSymbolHtml(book);
      const usdStr = fmtUsd(usdVal);
      if (isStable) {
        return `<span class="native-book-item equity-book-item">
          ${sym}
          <span class="font-mono tabular-nums">${usdStr}</span>
        </span>`;
      }
      const nativeStr = native === null ? "—" : fmtNum(native, places[book]);
      return `<span class="native-book-item equity-book-item">
        ${sym}
        <span class="font-mono tabular-nums">
          <span class="equity-book-native">${nativeStr}</span>
          <span class="equity-book-usd">(${usdStr})</span>
        </span>
      </span>`;
    });
  return `<div class="equity-book-rows">${rows.join("")}</div>`;
}

export function fmtOpenCreditStrategyBreakdown(byStrategy) {
  const rows = strategyOrder(new Set(dashboardStrategyIds())).map((id) => {
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

export function transfersUrl(days = 90, limit = 100) {
  return `/api/transfers?days=${days}&limit=${limit}`;
}

export function applyDiskGroupsPayload(groups) {
  if (!groups) return;
  STATE.groups = groups;
  if (INVESTOR && !STATE.dataFreshness.live) {
    STATE.groupsLivePending = true;
  }
}

export function applyDashboardBundlePayload(d) {
  let changed = false;
  if (d?.groups) {
    STATE.groups = d.groups;
    changed = true;
  }
  if (d?.status) {
    STATE.status = mergeStatusPayload(STATE.status, d.status);
    STATE.statusErrorOnce = false;
    STATE.dataFreshness.source = "live";
    STATE.dataFreshness.live = true;
    STATE.dataFreshness.statusMs = 0;
    STATE.groupsLivePending = false;
    changed = true;
  }
  if (d?.realized_summary) {
    STATE.report = d.realized_summary;
    STATE.summaryLoadPending = false;
    STATE.summaryLoadInFlight = false;
    changed = true;
  }
  if (changed) STATE.dashboardRenderHook?.();
}

/** Earliest entry among realized closed and open groups (lifetime APR sample start). */
export function lifetimePerformanceStartMs(report, groups) {
  let min = null;
  const consider = (g) => {
    if (!g) return;
    const entry = entryTimestampMs(g);
    if (entry === null || entry <= 0) return;
    const isOpen = String(g?.status || "").toLowerCase() === "open";
    if (!isOpen) {
      if (num(g.realized_pnl) === null) return;
      if (!isDisplayableClosedTradeGroup(g, STATE.status, groups)) return;
    }
    if (min === null || entry < min) min = entry;
  };
  for (const g of groups?.closed || []) consider(g);
  for (const g of groups?.open || []) consider(g);
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

/** Enabled strategies from accounts.toml (via /api/health); falls back to full catalog. */
function parseDashboardStrategyList(raw) {
  if (!Array.isArray(raw) || !raw.length) return null;
  const ids = [];
  const seen = new Set();
  for (const item of raw) {
    const id = normalizeStrategyId(item);
    if (!id || !STRATEGY_BY_ID[id] || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  return ids.length ? ids : null;
}

export function dashboardStrategyIds() {
  if (typeof window !== "undefined") {
    const boot = parseDashboardStrategyList(window.__DASHBOARD_STRATEGIES__);
    if (boot) return boot;
  }
  const fromHealth = parseDashboardStrategyList(STATE.health?.dashboard_strategies);
  if (fromHealth) return fromHealth;
  const fromSnapshot = parseDashboardStrategyList(STATE.portfolioSnapshot?.dashboard_strategies);
  if (fromSnapshot) return fromSnapshot;
  return STRATEGIES.map((s) => s.id);
}

export function isDashboardStrategy(id) {
  const key = normalizeStrategyId(id);
  return Boolean(key && STRATEGY_BY_ID[key] && dashboardStrategyIds().includes(key));
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
  "entry_fee_collateral",
  "close_fee_collateral",
  "current_close_fee_collateral",
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
  "profit_sweep_status",
  "profit_sweep_amount",
  "profit_sweep_exchange_native",
  "profit_sweep_exchange_quote_proceeds",
  "profit_sweep_instrument_name",
  "profit_sweep_order_id",
  "profit_sweep_quote_proceeds",
  "profit_sweep_reason",
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

/** Live exchange shows no open short leg (manual close / flat row with size 0). */
export function exchangeShortLegIsFlat(status, g) {
  if (!status?.positions) return false;
  const instrument = String(g?.short_instrument_name || "").trim();
  if (!instrument) return false;
  const account = String(g?.account_name || "").trim();
  const rows = status.positions.filter((p) => {
    if (String(p?.instrument_name || "") !== instrument) return false;
    if (account && String(p?.account_name || "") !== account) return false;
    return true;
  });
  if (!rows.length) return true;
  return rows.every((p) => {
    const size = num(p?.size);
    return size !== null && size === 0;
  });
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
    if (exchangeShortLegIsFlat(status, g)) continue;
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
  const known = dashboardStrategyIds();
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

/** Closed spread: per-leg entry premium (coin/USDC per Deribit quote). */
export function closedRowLegEntryPremium(g, role) {
  const raw = role === "short" ? g?.short_entry_average_price : g?.long_entry_average_price;
  const px = num(raw);
  return px !== null && px > 0 ? px : null;
}

/** Closed spread: per-leg exit premium; settlement intrinsic when reconcile left no fill price. */
export function closedRowLegExitPremium(g, role) {
  const raw = role === "short" ? g?.short_close_average_price : g?.long_close_average_price;
  let px = num(raw);
  if (px !== null && px > 0) return px;
  const reason = String(g?.close_reason || "").toLowerCase();
  if (reason !== "reconciled_expiry" && reason !== "reconciled_external") return null;
  const idx = num(g?.close_index_usd) ?? num(g?.entry_index_usd);
  const strike = openRowLegStrike(g, role);
  if (idx === null || strike === null || idx < 100) return null;
  return Math.max(strike - idx, 0);
}

export function closedRowLegExitIsSettlementEstimate(g, role) {
  const raw = role === "short" ? g?.short_close_average_price : g?.long_close_average_price;
  const px = num(raw);
  if (px !== null && px > 0) return false;
  return closedRowLegExitPremium(g, role) !== null;
}

export function closedRowLegPriceGap(g, phase) {
  const shortPx =
    phase === "entry" ? closedRowLegEntryPremium(g, "short") : closedRowLegExitPremium(g, "short");
  const longPx =
    phase === "entry" ? closedRowLegEntryPremium(g, "long") : closedRowLegExitPremium(g, "long");
  if (shortPx === null || longPx === null) return null;
  return shortPx - longPx;
}

export function bullPutSpreadClosedTitle(g) {
  const ccy = String(g?.currency || "").toUpperCase() || "Option";
  const shortStrike = openRowLegStrike(g, "short");
  const longStrike = openRowLegStrike(g, "long");
  if (shortStrike !== null && longStrike !== null) {
    const pair = `${fmtStrike(shortStrike)}/${fmtStrike(longStrike)}`;
    return INVESTOR_ZH ? `${ccy} ${pair} 賣權價差` : `${ccy} ${pair} bull put`;
  }
  return openPositionTitle(g);
}

/** One-line leg premium for activity meta (matches naked card footer style). */
export function closedSpreadLegMetaValue(g, role, phase) {
  const coll = tradeGroupAprBook(g) || String(g?.collateral_currency || "USDC").toUpperCase();
  const strike = openRowLegStrike(g, role);
  const premium =
    phase === "entry" ? closedRowLegEntryPremium(g, role) : closedRowLegExitPremium(g, role);
  const settle = phase === "exit" && closedRowLegExitIsSettlementEstimate(g, role);
  const strikePart = strike === null ? "—" : fmtStrike(strike);
  const pxPart = fmtDeribitPricePlain(premium, coll);
  if (settle) {
    return `${strikePart} · ${pxPart} (${i18n("settle", "結算")})`;
  }
  return `${strikePart} · ${pxPart}`;
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
  if (eq !== null && eq > 0) return eq;
  // Read portfolio inline — never call resolvedPortfolio() here (overviewEquityBreakdown → bookEquityNative cycle).
  const portfolio =
    status?.portfolio ||
    (status === STATE.status ? STATE.portfolioSnapshot?.portfolio : null);
  const fromNative = num(portfolio?.equity_native_by_book?.[b]);
  if (fromNative !== null && fromNative > 0) return fromNative;
  const fromUsd = num(portfolio?.equity_by_book?.[b]);
  if (fromUsd !== null && fromUsd > 0 && (b === "USDC" || b === "USDT")) return fromUsd;
  return null;
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

const DEFAULT_OPTION_FEE_RATE = 0.0003;
const DEFAULT_OPTION_FEE_CAP_RATE = 0.125;

/** Deribit inverse options: min(fee_rate, fee_cap_rate × premium) per contract (coin). */
export function inverseOptionFeeNativePerContract(
  premium,
  feeRate = DEFAULT_OPTION_FEE_RATE,
  feeCapRate = DEFAULT_OPTION_FEE_CAP_RATE,
) {
  const p = num(premium);
  if (p === null || p <= 0) return null;
  return Math.min(feeRate, feeCapRate * p);
}

/** Entry fee in collateral coin; prefers stored native fee over USDC round-trip. */
export function coinCollateralEntryFeeNative(g) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return null;
  const stored = num(g?.entry_fee_collateral);
  if (stored !== null && stored > 0) return stored;
  const prem = num(g?.short_entry_average_price);
  const longPrem = num(g?.long_entry_average_price);
  const qty = num(g?.quantity);
  if (prem === null || prem <= 0 || qty === null || qty <= 0) return null;
  let fee = (inverseOptionFeeNativePerContract(prem) ?? 0) * qty;
  if (longPrem !== null && longPrem > 0 && g?.long_instrument_name) {
    fee += (inverseOptionFeeNativePerContract(longPrem) ?? 0) * qty;
  }
  return fee > 0 ? fee : null;
}

/** Gross entry premium in collateral coin (short − long for spreads). */
export function coinCollateralGrossPremiumNative(g) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return null;
  const prem = num(g?.short_entry_average_price);
  const longPrem = num(g?.long_entry_average_price);
  const qty = num(g?.quantity);
  if (prem === null || prem <= 0 || qty === null || qty <= 0) return null;
  let gross = prem * qty;
  if (longPrem !== null && longPrem > 0 && g?.long_instrument_name) {
    gross -= longPrem * qty;
  }
  return gross;
}

/** Net entry credit in collateral coin (gross premium − native fee). */
export function coinCollateralNetEntryCreditNative(g) {
  const gross = coinCollateralGrossPremiumNative(g);
  const fee = coinCollateralEntryFeeNative(g);
  if (gross === null || fee === null) return null;
  return gross - fee;
}

/** Close fee in collateral coin; prefers stored native fee over USDC round-trip. */
export function coinCollateralCloseFeeNative(g, status) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return null;
  const stored = num(g?.close_fee_collateral);
  if (stored !== null && stored > 0) return stored;
  const openEst = num(g?.current_close_fee_collateral);
  if (openEst !== null && openEst > 0 && !isClosedTradeGroup(g)) return openEst;
  const prem = num(g?.short_close_average_price) ?? closedRowLegExitPremium(g, "short");
  const longPrem = num(g?.long_close_average_price) ?? closedRowLegExitPremium(g, "long");
  const qty = num(g?.quantity);
  if (prem === null || prem <= 0 || qty === null || qty <= 0) return null;
  let fee = (inverseOptionFeeNativePerContract(prem) ?? 0) * qty;
  if (longPrem !== null && longPrem > 0 && g?.long_instrument_name) {
    fee += (inverseOptionFeeNativePerContract(longPrem) ?? 0) * qty;
  }
  return fee > 0 ? fee : null;
}

/** Gross exit premium in collateral coin (buy-back short − sell long for spreads). */
export function coinCollateralGrossExitPremiumNative(g) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return null;
  const prem = num(g?.short_close_average_price) ?? closedRowLegExitPremium(g, "short");
  const longPrem = num(g?.long_close_average_price) ?? closedRowLegExitPremium(g, "long");
  const qty = num(g?.quantity);
  if (prem === null || prem <= 0 || qty === null || qty <= 0) return null;
  let gross = prem * qty;
  if (longPrem !== null && longPrem > 0 && g?.long_instrument_name) {
    gross -= longPrem * qty;
  }
  return gross;
}

/** All-in close debit in collateral coin (exit premium + close fee). */
export function coinCollateralCloseDebitNative(g, status) {
  const gross = coinCollateralGrossExitPremiumNative(g);
  const fee = coinCollateralCloseFeeNative(g, status);
  if (gross === null || fee === null) return null;
  return gross + fee;
}

/** 逆線期權：幣本位已實現損益 = entry_amount − exit_amount − fee（ETH/BTC）。 */
export function realizedPnlCoinNative(g, status) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return num(g?.realized_pnl);
  const qty = num(g?.quantity);
  if (qty === null || qty <= 0) return null;
  let idxEntry = num(g?.entry_index_usd);
  let idxClose = num(g?.close_index_usd) ?? idxEntry;
  let entryAmount = null;
  let exitAmount = null;
  const entryPx = num(g?.short_entry_average_price);
  let closePx = num(g?.short_close_average_price) ?? closedRowLegExitPremium(g, "short");
  const entryCredit = num(g?.entry_credit);
  if (entryPx !== null && entryPx > 0) {
    entryAmount = entryPx * qty;
    const longPrem = num(g?.long_entry_average_price);
    if (longPrem !== null && longPrem > 0 && g?.long_instrument_name) {
      entryAmount -= longPrem * qty;
    }
    const entryFee = num(g?.entry_fee) ?? 0;
    if ((idxEntry === null || idxEntry <= 0) && entryCredit !== null) {
      idxEntry = (entryCredit + entryFee) / (entryPx * qty);
    }
  } else if (entryCredit !== null && idxEntry !== null && idxEntry > 0) {
    const entryFee = num(g?.entry_fee) ?? 0;
    entryAmount = (entryCredit + entryFee) / idxEntry;
  }
  if (closePx !== null && closePx > 0) {
    exitAmount = closePx * qty;
    const longClosePrem = num(g?.long_close_average_price) ?? closedRowLegExitPremium(g, "long");
    if (longClosePrem !== null && longClosePrem > 0 && g?.long_instrument_name) {
      exitAmount -= longClosePrem * qty;
    }
    const closeDebit = num(g?.realized_close_debit);
    const closeFeeUsd = num(g?.realized_close_fee) ?? 0;
    if ((idxClose === null || idxClose <= 0) && closeDebit !== null) {
      idxClose = Math.max(0, closeDebit - closeFeeUsd) / (closePx * qty);
    }
  } else {
    exitAmount = coinCollateralGrossExitPremiumNative(g);
  }
  if (exitAmount === null) {
    const closeDebit = num(g?.realized_close_debit);
    if (closeDebit !== null && idxClose !== null && idxClose > 0) {
      const closeFeeUsd = num(g?.realized_close_fee) ?? 0;
      exitAmount = Math.max(0, closeDebit - closeFeeUsd) / idxClose;
    }
  }
  if (entryAmount !== null && exitAmount !== null) {
    let fees = coinCollateralEntryFeeNative(g);
    const closeFeeNative = coinCollateralCloseFeeNative(g, status);
    if (closeFeeNative !== null) {
      fees = (fees ?? 0) + closeFeeNative;
    } else {
      const closeFee = num(g?.realized_close_fee) ?? 0;
      if (closeFee > 0) {
        if (idxClose === null || idxClose <= 0) return null;
        fees = (fees ?? 0) + closeFee / idxClose;
      }
    }
    if (fees !== null) {
      return entryAmount - exitAmount - fees;
    }
  }
  const stored = num(g?.realized_pnl_collateral_native);
  if (stored !== null) return stored;
  return null;
}

export function isInverseCoinBookGroup(g) {
  const book = tradeGroupAprBook(g);
  return book === "BTC" || book === "ETH";
}

/** Coin collateral PnL in USDT terms: swapped portion uses actual USDT received;
 *  unswept/pending portion uses live index × native. USDC book uses stored USDC PnL. */
export function realizedPnlDisplayUsdc(g, status) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return num(g?.realized_pnl);
  const spot = collateralBookSpotUsd(g, status);
  const disp = profitDispositionForGroup(g, status);
  if (disp) {
    const sweptUsdt = num(disp.sweptUsdt) ?? 0;
    const held = num(disp.held) ?? 0;
    const pending = num(disp.pending) ?? 0;
    const unsweptNative = held + pending;
    if (sweptUsdt > 0) {
      if (unsweptNative > 0 && spot !== null && spot > 0) {
        return sweptUsdt + unsweptNative * spot;
      }
      return sweptUsdt;
    }
    if (unsweptNative !== 0 && spot !== null && spot > 0) {
      return unsweptNative * spot;
    }
  }
  const native = realizedPnlCoinNative(g, status);
  if (native !== null && spot !== null && spot > 0) return native * spot;
  return null;
}

/** 已實現損益換成 APR 帳本原生單位（幣本位：premium−fee，不用 USDC÷指數 回推）。 */
export function realizedPnlInAprBookNative(g, status) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return num(g?.realized_pnl);
  const stored = num(g?.realized_pnl_collateral_native);
  if (stored !== null) return stored;
  return realizedPnlCoinNative(g, status);
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
    return pnlUsd === null ? "—" : fmtUsdPrecise(pnlUsd);
  }
  const native =
    g?.realized_pnl_collateral_native !== undefined &&
    g?.realized_pnl_collateral_native !== null &&
    g?.realized_pnl_collateral_native !== ""
      ? g.realized_pnl_collateral_native
      : realizedPnlInAprBookNative(g, status);
  const storedUsd = g?.realized_pnl;
  if (native === null || (typeof native !== "string" && num(native) === null)) {
    const pnlUsd = storedUsd;
    return pnlUsd === null || pnlUsd === undefined || pnlUsd === ""
      ? "—"
      : fmtUsdPrecise(pnlUsd);
  }
  const usdc =
    isClosedTradeGroup(g) &&
    storedUsd !== undefined &&
    storedUsd !== null &&
    storedUsd !== ""
      ? storedUsd
      : realizedPnlDisplayUsdc(g, status);
  const nativeStr = `${fmtProfitNative(book, native)} ${book}`;
  const usdStr = fmtUsdPrecise(usdc);
  return INVESTOR_ZH ? `${usdStr}（${nativeStr}）` : `${usdStr} (${nativeStr})`;
}

export function fmtNativeBookAmount(native, book) {
  const n = num(native);
  if (n === null) return `— ${book || ""}`.trim();
  return `${fmtProfitNative(book || "", n)} ${book}`;
}

export function fmtUsdWithNativeBookAmount(usd, native, book) {
  const usdStr = fmtUsdPrecise(usd);
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
  const stored = num(g?.entry_index_usd);
  const persisted = stored !== null && stored > 0 ? stored : null;
  return (
    persisted ??
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
  const book = tradeGroupAprBook(g);
  if (book !== "USDC") {
    const native = coinCollateralNetEntryCreditNative(g);
    if (native !== null) return native;
  }
  const credit = num(g?.entry_credit);
  if (credit === null) return null;
  const fee = num(g?.entry_fee) ?? 0;
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
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return null;
  const native = coinCollateralEntryFeeNative(g);
  if (native !== null) return native;
  return nativeFromUsdAtIndex(groupEntryFeeUsd(g), entryIndexUsdForGroup(g, status));
}

export function groupCloseFeeUsd(g) {
  const openEst = num(g?.current_close_fee);
  if (openEst !== null && openEst > 0) return openEst;
  return num(g?.realized_close_fee);
}

export function groupCloseFeeNative(g, status) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return null;
  const native = coinCollateralCloseFeeNative(g, status);
  if (native !== null) return native;
  const openEst = num(g?.current_close_fee);
  const index = openEst !== null && openEst > 0 ? collateralBookSpotUsd(g, status) : closeIndexUsdForGroup(g, status);
  return nativeFromUsdAtIndex(groupCloseFeeUsd(g), index);
}

export function groupEntryCreditUsd(g, status, groups) {
  return openRowEntryCreditUsd(g, status, groups);
}

export function groupEntryCreditNative(g, status) {
  const book = tradeGroupAprBook(g);
  if (book === "USDC") return num(g?.entry_credit);
  const native = coinCollateralNetEntryCreditNative(g);
  if (native !== null) return native;
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
    .filter((g) => isDashboardStrategy(strategyId(g)))
    .sort((a, b) => (entryTimestampMs(b) || 0) - (entryTimestampMs(a) || 0));
}

function profitSweepAvgSuffix(g, book, soldNative) {
  const quoteRaw =
    g?.profit_sweep_exchange_quote_proceeds ?? g?.profit_sweep_quote_proceeds;
  const quote = num(quoteRaw);
  const sold = num(soldNative);
  if (quote === null || quote <= 0 || sold === null || sold <= 0) return "";
  const avg = profitSwapDisplayAvg(book, quoteRaw ?? quote, soldNative ?? sold);
  if (avg === null) return "";
  return ` · ${i18n("avg", "均價")} ${fmtProfitAvgUsd(book, avg)}`;
}

function profitSweepDisplayQuoteUsdt(g) {
  const exchange = num(g?.profit_sweep_exchange_quote_proceeds);
  if (exchange !== null && exchange > 0) return exchange;
  return profitSweepRealizedQuoteUsdt(g);
}

export function profitSweepMetaLine(g) {
  const status = String(g?.profit_sweep_status || "").toLowerCase();
  if (!status) return null;
  const inst = String(g?.profit_sweep_instrument_name || "");
  const base = inst.split("_")[0] || String(g?.currency || "").toUpperCase() || "—";
  if (status === "filled") {
    if (!profitSweepHasExchangeFill(g)) return null;
    const soldRaw =
      g?.profit_sweep_exchange_native ??
      (profitSweepHasExchangeFill(g) ? g?.profit_sweep_amount : null);
    const soldNative = num(soldRaw);
    const amtDisplay =
      soldRaw === null || soldRaw === undefined || soldRaw === "" || soldNative === null
        ? "—"
        : fmtProfitNative(base, soldRaw);
    const quoteRaw =
      g?.profit_sweep_exchange_quote_proceeds ?? g?.profit_sweep_quote_proceeds;
    const quoteNum = num(quoteRaw);
    const quoteDisplay =
      quoteRaw !== undefined &&
      quoteRaw !== null &&
      quoteRaw !== "" &&
      quoteNum !== null &&
      quoteNum > 0
        ? fmtProfitUsdt(quoteRaw).replace(/^\$/, "")
        : null;
    const avgSuffix = profitSweepAvgSuffix(g, base, soldRaw ?? soldNative);
    return [
      i18n("Profit swapped", "獲利已兌"),
      quoteDisplay
        ? `${amtDisplay} ${base} → ${quoteDisplay} USDT${avgSuffix}`
        : `${amtDisplay} ${base} → USDT${avgSuffix}`,
    ];
  }
  const amt = g?.profit_sweep_amount;
  const amtDisplay =
    amt !== undefined && amt !== null && amt !== ""
      ? fmtProfitNative(base, amt)
      : "—";
  if (status === "pending" || status === "submitted") {
    return [
      i18n("Profit swapped", "獲利已兌"),
      amtDisplay !== "—"
        ? `${amtDisplay} ${base} → USDT (${i18n("pending", "待兌")})`
        : i18n("pending", "待兌"),
    ];
  }
  if (status === "skipped") {
    return [
      i18n("Profit sweep", "獲利兌 USDT"),
      i18n("below min size, skipped", "低於最小成交單位，未兌換"),
    ];
  }
  if (status === "failed") {
    return [
      i18n("Profit sweep", "獲利兌 USDT"),
      i18n("failed", "兌換失敗"),
    ];
  }
  return null;
}

export function activityClosedRows(status, report, groups) {
  return mergedClosedRows(report, groups, 500, status).filter((g) =>
    isDashboardStrategy(strategyId(g))
  );
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
  const isBullPutClosed = id === "bull_put_spread" && isClosedTradeGroup(g);
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
  const title = isBullPutClosed ? bullPutSpreadClosedTitle(g) : tradeGroupActivityTitle(g);
  const entryCreditDisplay = credit === null ? "—" : fmtUsdWithNativeBookAmount(credit, creditNative, book);
  const entryAprDisplay = entryApr === null ? "—" : fmtPct(entryApr, 1);
  const entryFeeDisplay = entryFee === null ? null : fmtUsdWithNativeBookAmount(entryFee, entryFeeNative, book);
  const spreadLegEntryMeta = isBullPutClosed
    ? [
        [i18n("Short entry", "短腿進場"), closedSpreadLegMetaValue(g, "short", "entry")],
        [i18n("Long entry", "長腿進場"), closedSpreadLegMetaValue(g, "long", "entry")],
      ]
    : [];
  const entryMetaSecondary = [
    [i18n("Opened", "開倉"), fmtTime(entryMs)],
    ...spreadLegEntryMeta,
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
    const spreadLegExitMeta = isBullPutClosed
      ? [
          [i18n("Short exit", "短腿出場"), closedSpreadLegMetaValue(g, "short", "exit")],
          [i18n("Long exit", "長腿出場"), closedSpreadLegMetaValue(g, "long", "exit")],
        ]
      : [];
    const exitMetaSecondary = [
      [i18n("Closed", "平倉"), fmtTime(closedTimestampMs(g))],
      ...spreadLegExitMeta,
      closeFee !== null
        ? [i18n("Close fee", "平倉手續費"), fmtUsdWithNativeBookAmount(closeFee, closeFeeNative, book)]
        : null,
      holding !== null
        ? [i18n("Held", "持有"), `${fmtNum(holding, 1)}${INVESTOR_ZH ? " 天" : "d"}`]
        : null,
      profitSweepMetaLine(g),
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
  const longInst = openRowLegInstrumentName(g, "long");
  const instrumentBlock =
    isBullPutClosed && longInst
      ? `${escapeHtml(g.short_instrument_name || "")}<br>${escapeHtml(longInst)}`
      : escapeHtml(g.short_instrument_name || "");
  const groupId = String(g?.group_id || "").trim();
  const groupIdSuffix = groupId
    ? `<span class="activity-card-group-id" title="group_id"> · #${escapeHtml(groupId)}</span>`
    : "";
  return `
    <li class="activity-card">
      <div class="activity-card-head">
        ${strategyChipHtml(id)}
        <span class="activity-card-title">${escapeHtml(title)}</span>
        <span class="text-[11px] text-slate-500">${escapeHtml(book)}</span>
        ${acct ? `<span class="text-[11px] text-slate-500">${escapeHtml(acct)}</span>` : ""}
      </div>
      <div class="activity-card-instrument">${instrumentBlock}${groupIdSuffix}</div>
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

export function showToast(msg, options = {}) {
  const el = document.getElementById("toast");
  if (!el) return;
  const retry = typeof options.retry === "function" ? options.retry : null;
  if (retry) {
    el.innerHTML =
      `<span class="toast-msg"></span>` +
      `<button type="button" class="toast-retry">${i18n("Retry", "重試")}</button>`;
    el.querySelector(".toast-msg").textContent = msg;
    el.querySelector(".toast-retry").addEventListener("click", () => {
      el.classList.add("hidden");
      retry();
    });
  } else {
    el.textContent = msg;
  }
  el.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.add("hidden"), retry ? 12000 : 5000);
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

export function isFetchNetworkError(err) {
  if (!err) return false;
  if (err.name === "AbortError") return true;
  const msg = String(err.message || err).toLowerCase();
  return (
    msg.includes("failed to fetch") ||
    msg.includes("networkerror") ||
    msg.includes("load failed") ||
    msg.includes("network request failed")
  );
}

export function formatFetchError(err, fallback = "Request failed") {
  if (isFetchNetworkError(err)) {
    return i18n(
      "Cannot reach dashboard server — check that the service is running.",
      "無法連線至 Dashboard 伺服器，請確認服務是否正在執行。"
    );
  }
  const raw = String(err?.message || err || fallback);
  if (/\b524\b/.test(raw)) {
    return i18n(
      "Request timed out — try again in a moment.",
      "請求逾時，請稍後再試。"
    );
  }
  if (/\b(522|504)\b/.test(raw)) {
    return i18n(
      "Server is slow to respond — try again shortly.",
      "伺服器回應較慢，請稍後再試。"
    );
  }
  return raw;
}

export async function fetchJson(url, options = {}) {
  const targetUrl = resolveApiUrl(url);
  let httpAttempt = 0;
  let networkAttempt = 0;
  while (true) {
    let res;
    try {
      res = await fetch(targetUrl, options);
    } catch (err) {
      if (networkAttempt < FETCH_JSON_NETWORK_MAX_RETRIES) {
        networkAttempt += 1;
        await delay(FETCH_JSON_NETWORK_RETRY_BASE_MS * networkAttempt);
        continue;
      }
      throw new Error(formatFetchError(err));
    }
    if (res.ok) return res.json();
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = `${res.status} ${body.detail}`;
    } catch (_) {}
    if (FETCH_JSON_RETRYABLE_STATUS.has(res.status) && httpAttempt < FETCH_JSON_MAX_RETRIES) {
      httpAttempt += 1;
      await delay(FETCH_JSON_RETRY_BASE_MS * httpAttempt);
      continue;
    }
    throw new Error(detail);
  }
}
