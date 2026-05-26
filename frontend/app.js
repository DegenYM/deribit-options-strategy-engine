(() => {
  // src/shared/context.js
  var DASHBOARD_MODE = typeof window !== "undefined" && window.__DASHBOARD_MODE__ === "investor" ? "investor" : "ops";
  var INVESTOR = DASHBOARD_MODE === "investor";
  var INVESTOR_LOCALE = (() => {
    if (!INVESTOR) return "en";
    const raw = String(
      typeof window !== "undefined" && window.__INVESTOR_LOCALE__ || "en"
    ).trim().toLowerCase();
    if (raw === "zh-hant" || raw === "zh_tw" || raw === "zh-tw" || raw === "zh-hk" || raw === "zh") {
      return "zh";
    }
    return "en";
  })();
  var INVESTOR_ZH = INVESTOR && INVESTOR_LOCALE === "zh";
  function i18n(en, zh) {
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
  function resolveApiUrl(path) {
    if (/^https?:\/\//i.test(path)) return path;
    const fromWindow = typeof window !== "undefined" && window.__API_BASE__ ? String(window.__API_BASE__).trim() : "";
    const base = (fromWindow || readApiBaseFromMeta()).replace(/\/$/, "");
    const p = path.startsWith("/") ? path : `/${path}`;
    return base ? `${base}${p}` : p;
  }

  // src/shared/config.js
  var fmt = {
    usd0: new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0
    }),
    usd2: new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2
    }),
    num4: new Intl.NumberFormat("en-US", {
      maximumFractionDigits: 4
    }),
    num8: new Intl.NumberFormat("en-US", {
      maximumFractionDigits: 8
    }),
    pct2: new Intl.NumberFormat("en-US", {
      style: "percent",
      maximumFractionDigits: 2,
      minimumFractionDigits: 2
    }),
    pct1: new Intl.NumberFormat("en-US", {
      style: "percent",
      maximumFractionDigits: 1,
      minimumFractionDigits: 1
    })
  };
  var BOOK_COLORS = {
    BTC: "#fb923c",
    ETH: "#818cf8",
    USDC: "#38bdf8",
    TOTAL: "#a3e635"
  };
  var CORE_BOOKS = ["BTC", "ETH", "USDC"];
  var FRONTEND_REFRESH_INTERVAL_MS = 18e4;
  var FRONTEND_API_CONCURRENCY = INVESTOR ? 6 : 3;
  var USE_DASHBOARD_BUNDLE = true;
  var INVESTOR_STATUS_TIMEOUT_MS = 45e3;
  var INVESTOR_OVERLAY_MAX_MS = 3e3;
  var FETCH_JSON_RETRYABLE_STATUS = /* @__PURE__ */ new Set([502, 503, 504]);
  var FETCH_JSON_MAX_RETRIES = 2;
  var FETCH_JSON_RETRY_BASE_MS = 450;
  var ACTIVITY_PAGE_SIZE = 10;
  var STRATEGIES = [
    {
      id: "covered_call",
      title: "Covered Call",
      titleZh: "\u5099\u514C\u8CB7\u6B0A",
      short: "Covered Call",
      shortZh: "\u5099\u514C",
      chipShort: "CC",
      chipShortZh: "\u5099\u514C",
      accentClass: "strategy-card-call",
      description: "Short call backed by existing BTC/ETH spot collateral.",
      descriptionZh: "\u5728\u6301\u6709\u73FE\u8CA8\u64D4\u4FDD\u4E0B\u8CE3\u51FA\u8CB7\u6B0A\uFF0C\u4EE5\u6B0A\u5229\u91D1\u589E\u5F37\u6536\u76CA\u3002"
    },
    {
      id: "naked_short",
      title: "Naked Short",
      titleZh: "\u55AE\u8CE3\u9078\u64C7\u6B0A\uFF08\u88F8\u8CE3\uFF09",
      short: "Naked Short",
      shortZh: "\u88F8\u8CE3",
      chipShort: "Naked",
      chipShortZh: "\u88F8\u8CE3",
      accentClass: "strategy-card-put",
      description: "Single-leg short option (put / call / both) with uncapped tail risk on the chosen side.",
      descriptionZh: "\u55AE\u908A\u8CE3\u51FA\u8CB7\uFF0F\u8CE3\u6B0A\uFF1B\u5728\u5C0D\u61C9\u65B9\u5411\u5177\u5C3E\u90E8\u98A8\u96AA\uFF0C\u9700\u56B4\u683C\u98A8\u63A7\u3002"
    },
    {
      id: "bull_put_spread",
      title: "Bull Put Spread",
      titleZh: "\u725B\u52E2\u8CE3\u6B0A\u50F9\u5DEE",
      short: "Put Spread",
      shortZh: "\u8CE3\u6B0A\u50F9\u5DEE",
      chipShort: "Spread",
      chipShortZh: "\u50F9\u5DEE",
      accentClass: "strategy-card-spread",
      description: "Short put paired with a lower-strike long put protection leg.",
      descriptionZh: "\u8CE3\u51FA\u8F03\u9AD8\u5C65\u7D04\u50F9\u8CE3\u6B0A\uFF0C\u4E26\u8CB7\u5165\u8F03\u4F4E\u5C65\u7D04\u50F9\u8CE3\u6B0A\u4F5C\u4FDD\u8B77\u3002"
    }
  ];
  var STRATEGY_BY_ID = Object.fromEntries(STRATEGIES.map((s) => [s.id, s]));

  // src/shared/state.js
  var STATE = {
    health: null,
    status: null,
    report: null,
    stress: null,
    groups: null,
    cumulativePnl: null,
    aprSeries: null,
    portfolioSnapshot: null,
    dataFreshness: { source: null, snapshotMs: null, statusMs: null, live: false },
    chartsDataLoaded: false,
    chartsLoadInFlight: false,
    bookFilter: "ALL",
    aprWindow: 30,
    charts: {},
    autoRefreshHandle: null,
    refreshInFlight: false,
    investorReady: false,
    investorLoadTotal: 0,
    investorLoadDone: 0,
    lastRefreshStartedMs: 0,
    statusErrorOnce: false,
    lastUnderlyingIndexUsd: {},
    lastSpotUsd: { BTC: null, ETH: null },
    activityOpenPage: 1,
    activityClosedPage: 1
  };

  // src/dashboard.js
  function num(value) {
    if (value === null || value === void 0 || value === "") return null;
    const n = typeof value === "number" ? value : Number(value);
    return Number.isFinite(n) ? n : null;
  }
  function fmtUsd(value, places = 2) {
    const n = num(value);
    if (n === null) return "\u2014";
    return places === 0 ? fmt.usd0.format(n) : fmt.usd2.format(n);
  }
  function fmtPct(value, decimals = 2) {
    const n = num(value);
    if (n === null) return "\u2014";
    return decimals === 1 ? fmt.pct1.format(n) : fmt.pct2.format(n);
  }
  function resolvedPortfolio() {
    if (STATE.status?.portfolio) {
      return {
        portfolio: STATE.status.portfolio,
        source: "live",
        freshnessMs: STATE.dataFreshness.statusMs ?? 0
      };
    }
    const snap = STATE.portfolioSnapshot?.portfolio;
    if (snap && Object.keys(snap).length > 0) {
      return {
        portfolio: snap,
        source: "snapshot",
        freshnessMs: num(STATE.portfolioSnapshot?.freshness_ms)
      };
    }
    return { portfolio: null, source: null, freshnessMs: null };
  }
  function fmtFreshnessMinutes(ms) {
    const n = num(ms);
    if (n === null || n < 0) return null;
    const mins = Math.max(1, Math.round(n / 6e4));
    return mins;
  }
  function dataFreshnessBadgeHtml() {
    const resolved = resolvedPortfolio();
    if (resolved.source === "live") {
      const age = num(STATE.dataFreshness.statusMs);
      if (age !== null && age < 3e4) {
        return `<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-emerald-500/40 bg-emerald-500/10 text-emerald-200">${i18n("Live", "\u5373\u6642")}</span>`;
      }
    }
    if (resolved.source === "snapshot") {
      const mins = fmtFreshnessMinutes(resolved.freshnessMs);
      const label = mins !== null ? i18n(`Snapshot \xB7 ~${mins}m ago`, `\u5FEB\u7167 \xB7 \u7D04 ${mins} \u5206\u9418\u524D`) : i18n("Snapshot", "\u5FEB\u7167");
      return `<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-200">${label}</span>`;
    }
    return `<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-800/60 text-slate-400">${i18n("Loading\u2026", "\u8F09\u5165\u4E2D\u2026")}</span>`;
  }
  function renderDataFreshnessBadge() {
    if (!INVESTOR) return;
    const host = document.getElementById("data-freshness-slot");
    if (!host) return;
    host.innerHTML = dataFreshnessBadgeHtml();
  }
  function setInvestorProgressBar(active, { indeterminate = false } = {}) {
    const bar = document.getElementById("investor-progress-bar");
    if (!bar) return;
    bar.classList.toggle("hidden", !active);
    bar.classList.toggle("investor-progress-bar--indeterminate", active && indeterminate);
  }
  function aggregateSkeletonHtml() {
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
  function overviewMetricsGridHtml(ctx) {
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
      equityNativeByBook
    } = ctx;
    return `
    <div class="overview-metrics-grid">
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${i18n("Total equity", "\u7E3D\u6B0A\u76CA\uFF08USDC \u7D04\u7576\uFF09")}</div>
        <div class="text-2xl font-mono">${fmtUsd(totalEquity)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${fmtBookEquityNativeBreakdown(equityNativeByBook)}</div>
          <div class="overview-metric-line">${i18n("day-start", "\u65E5\u521D")} ${fmtUsd(dayStart)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${i18n("Day P&L", "\u672C\u65E5\u640D\u76CA")}</div>
        <div class="text-2xl font-mono ${pnlClass(dayPnl)}">${fmtUsd(dayPnl)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${i18n("drawdown", "\u56DE\u64A4")} ${fmtPct(dayDrawdown)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${i18n("Open credit", "\u672A\u5BE6\u73FE\u6B0A\u5229\u91D1\uFF08\u9032\u5834\u6536\u6582\uFF09")}</div>
        <div class="text-2xl font-mono">${fmtUsd(openCredit)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${fmtOpenCreditStrategyBreakdown(creditByStrategy)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${i18n("Win rate \xB7 avg holding", "\u52DD\u7387 \xB7 \u5E73\u5747\u6301\u6709")}</div>
        <div class="text-2xl font-mono">${summary ? `${fmtPct(winRate, 1)} \xB7 ${fmtNum(avgHolding, 2)}${INVESTOR_ZH ? " \u5929" : "d"}` : "\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${summary ? sinceLine : i18n("Loading performance\u2026", "\u7E3E\u6548\u6458\u8981\u8F09\u5165\u4E2D\u2026")}</div>
        </div>
      </div>

      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${i18n("Total profit (lifetime)", "\u7D2F\u8A08\u5DF2\u5BE6\u73FE\u640D\u76CA")}</div>
        <div class="text-2xl font-mono ${pnlClass(lifetimePnl)}">${summary ? fmtUsd(lifetimePnl) : "\u2014"}</div>
        <div class="overview-metric-meta">
          ${summary ? `<div class="overview-metric-line">${fmtLifetimeRealizedNativeBreakdown(lifetimeNativeByBook)}</div>` : ""}
          <div class="overview-metric-line">${summary ? `${closedCount ?? 0} ${i18n("closed groups", "\u7B46\u5DF2\u5E73\u5009\u90E8\u4F4D")}` : ""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${rollingWindowProfitLabel(windowLabelDays)}</div>
        <div class="text-2xl font-mono ${pnlClass(windowPnl)}">${summary ? fmtUsd(windowPnl) : "\u2014"}</div>
        <div class="overview-metric-meta">
          ${summary ? `<div class="overview-metric-line">${fmtLifetimeRealizedNativeBreakdown(windowNativeByBook)}</div>` : ""}
          <div class="overview-metric-line">${summary ? rollingWindowPnlHint(windowLabelDays) : ""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${i18n("Realized APR (lifetime)", "\u5DF2\u5BE6\u73FE\u5E74\u5316\uFF08\u5B58\u7E8C\u671F\uFF09")}</div>
        <div class="text-2xl font-mono">${summary ? fmtPct(lifetimeApr) : "\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${summary ? i18n("annualized on actual span", "\u4F9D\u5BE6\u969B\u5340\u9593\u5E74\u5316") : ""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${rollingWindowAprLabel(windowLabelDays)}</div>
        <div class="text-2xl font-mono">${summary ? fmtPct(windowApr) : "\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line overview-metric-line--hint">${summary ? rollingWindowAprHint(windowLabelDays) : ""}</div>
        </div>
      </div>
    </div>`;
  }
  function investorNativeChipsHtml(byBook, { pnl = false, places = { BTC: 5, ETH: 4, USDC: 2 } } = {}) {
    const symbols = { BTC: "\u20BF", ETH: "\u25C6", USDC: "$" };
    return ["BTC", "ETH", "USDC"].map((book) => {
      const n = num(byBook[book]);
      const text = n === null ? "\u2014" : fmtNum(n, places[book] ?? 4);
      const tone = pnl ? pnlClass(byBook[book]) : "";
      return `<span class="inv-chip ${tone}"><span class="inv-chip-sym">${symbols[book]}</span><span class="inv-chip-val font-mono tabular-nums">${text}</span></span>`;
    }).join("");
  }
  function investorOpenCreditMiniHtml(byStrategy) {
    return strategyOrder(new Set(STRATEGIES.map((s) => s.id))).map((id) => {
      const short = escapeHtml(strategyInfo(id).short);
      const n = num(byStrategy[id]);
      const text = n === null ? "\u2014" : fmtUsd(n);
      return `<div class="inv-mini-row"><span class="inv-mini-label">${short}</span><span class="inv-mini-value font-mono tabular-nums">${text}</span></div>`;
    }).join("");
  }
  function investorOverviewHtml(ctx) {
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
      equityNativeByBook
    } = ctx;
    const winHold = summary !== null && summary !== void 0 ? `${fmtPct(winRate, 1)} \xB7 ${fmtNum(avgHolding, 2)}${INVESTOR_ZH ? " \u5929" : "d"}` : "\u2014";
    const winSub = summary ? sinceLine : i18n("Loading performance\u2026", "\u7E3E\u6548\u6458\u8981\u8F09\u5165\u4E2D\u2026");
    return `<div class="inv-dashboard">
    <section class="inv-panel inv-panel--hero" aria-label="${i18n("Account snapshot", "\u5E33\u6236\u5FEB\u7167")}">
      <div class="inv-split">
        <div class="inv-kpi">
          <span class="inv-kpi-label">${i18n("Total equity", "\u7E3D\u6B0A\u76CA")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${fmtUsd(totalEquity)}</span>
          <span class="inv-kpi-foot">${i18n("day-start", "\u65E5\u521D")} ${fmtUsd(dayStart)}</span>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${i18n("Day P&L", "\u672C\u65E5\u640D\u76CA")}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${pnlClass(dayPnl)}">${fmtUsd(dayPnl)}</span>
          <span class="inv-kpi-foot">${i18n("drawdown", "\u56DE\u64A4")} ${fmtPct(dayDrawdown)}</span>
        </div>
      </div>
      <div class="inv-chips-row">${investorNativeChipsHtml(equityNativeByBook)}</div>
    </section>

    <section class="inv-panel" aria-label="${i18n("Open risk", "\u672A\u5E73\u5009\u98A8\u96AA")}">
      <div class="inv-split">
        <div class="inv-kpi">
          <span class="inv-kpi-label">${i18n("Open credit", "\u672A\u5BE6\u73FE\u6B0A\u5229\u91D1")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${fmtUsd(openCredit)}</span>
          <div class="inv-mini-list">${investorOpenCreditMiniHtml(creditByStrategy)}</div>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${i18n("Win rate \xB7 hold", "\u52DD\u7387 \xB7 \u6301\u6709")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${winHold}</span>
          <span class="inv-kpi-foot">${winSub}</span>
        </div>
      </div>
    </section>

    <section class="inv-panel" aria-label="${i18n("Realized performance", "\u5DF2\u5BE6\u73FE\u7E3E\u6548")}">
      <h3 class="inv-panel-title">${i18n("Realized P&L", "\u5DF2\u5BE6\u73FE\u640D\u76CA")}</h3>
      <div class="inv-compare">
        <div class="inv-compare-col">
          <span class="inv-compare-tag">${i18n("Lifetime", "\u5B58\u7E8C")}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${pnlClass(lifetimePnl)}">${summary ? fmtUsd(lifetimePnl) : "\u2014"}</span>
          <div class="inv-chips-row inv-chips-row--compact">${summary ? investorNativeChipsHtml(lifetimeNativeByBook, { pnl: true }) : ""}</div>
          <span class="inv-kpi-foot">${summary ? `${closedCount ?? 0} ${i18n("closed", "\u7B46\u5E73\u5009")}` : ""}</span>
        </div>
        <div class="inv-compare-col">
          <span class="inv-compare-tag">${i18n("Last", "\u8FD1")} ${windowLabelDays}${INVESTOR_ZH ? " \u65E5" : "d"}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${pnlClass(windowPnl)}">${summary ? fmtUsd(windowPnl) : "\u2014"}</span>
          <div class="inv-chips-row inv-chips-row--compact">${summary ? investorNativeChipsHtml(windowNativeByBook, { pnl: true }) : ""}</div>
          <span class="inv-kpi-foot">${summary ? rollingWindowPnlHint(windowLabelDays) : ""}</span>
        </div>
      </div>
      <div class="inv-split inv-split--apr">
        <div class="inv-kpi inv-kpi--compact">
          <span class="inv-kpi-label">${i18n("APR lifetime", "\u5E74\u5316\xB7\u5B58\u7E8C")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${summary ? fmtPct(lifetimeApr) : "\u2014"}</span>
        </div>
        <div class="inv-kpi inv-kpi--compact">
          <span class="inv-kpi-label">${i18n("APR", "\u5E74\u5316")} ${windowLabelDays}${INVESTOR_ZH ? " \u65E5" : "d"}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${summary ? fmtPct(windowApr) : "\u2014"}</span>
          <span class="inv-kpi-foot">${summary ? rollingWindowAprHint(windowLabelDays) : ""}</span>
        </div>
      </div>
    </section>
  </div>`;
  }
  function fmtDeribitPriceCell(value, collateralCurrency) {
    const n = num(value);
    if (n === null) return "\u2014";
    const c = String(collateralCurrency || "").toUpperCase();
    const sp = '<span class="text-slate-500">';
    const ep = "</span>";
    if (c === "USDC") return `${sp}($)${ep}\xA0${fmtNum(n, 4)}`;
    if (c === "BTC") return `${sp}\u20BF${ep}\xA0${fmtNum(n, 5)}`;
    if (c === "ETH") return `${sp}\u2666${ep}\xA0${fmtNum(n, 5)}`;
    return fmtNum(n, 4);
  }
  function openRowPositionSignedSizeForDisplay(p) {
    if (!p) return null;
    const kind = String(p.kind || "").toLowerCase();
    const sell = String(p.direction || "").toLowerCase() === "sell";
    if (kind === "option") {
      const sz2 = num(p.size);
      if (sz2 === null || sz2 === 0) return null;
      return sell ? -Math.abs(sz2) : Math.abs(sz2);
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
  function fmtShortAmountDisplay(g, status, groups = null) {
    const ctx = groups ?? STATE.groups;
    if (countOpenGroupsSharingLeg(status, ctx, g, "short") > 1) {
      const groupSize = openRowLegGroupSignedSize(g, "short");
      if (groupSize !== null) return fmtNum(groupSize, 4);
    }
    const p = openRowPosition(status, g);
    const signed = openRowPositionSignedSizeForDisplay(p);
    if (signed !== null) return fmtNum(signed, 4);
    const q = num(g.quantity);
    if (q === null) return "\u2014";
    const qSigned = q > 0 ? -Math.abs(q) : q;
    return fmtNum(qSigned, 4);
  }
  function openRowLegInstrumentName(g, role) {
    return role === "long" ? String(g?.long_instrument_name || "") : String(g?.short_instrument_name || "");
  }
  function openRowLegGroupSignedSize(g, role) {
    const q = num(g.quantity);
    if (q === null) return null;
    return role === "short" ? -Math.abs(q) : Math.abs(q);
  }
  function countOpenGroupsSharingLeg(status, groups, g, role) {
    const instrument = openRowLegInstrumentName(g, role);
    if (!instrument) return 0;
    const account = String(g?.account_name || "");
    const seen = /* @__PURE__ */ new Set();
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
  function openRowLegPosition(status, g, role) {
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
  function openRowPosition(status, g) {
    return openRowLegPosition(status, g, "short");
  }
  function enrichOpenGroupRow(status, g, groups = null) {
    const ctx = groups ?? STATE.groups;
    let avg = g.short_average_price;
    let mrk = g.short_mark_price;
    let fpl = g.short_floating_profit_loss;
    let hasFpl = g.short_has_floating_profit_loss;
    let fplUsd = g.short_floating_profit_loss_usd;
    let hasFplUsd = g.short_has_floating_profit_loss_usd;
    const missingAvg = avg === null || avg === void 0 || avg === "";
    const missingMrk = mrk === null || mrk === void 0 || mrk === "";
    const missingFpl = fpl === null || fpl === void 0 || fpl === "";
    const missingFplUsd = fplUsd === null || fplUsd === void 0 || fplUsd === "";
    const sharedShort = countOpenGroupsSharingLeg(status, ctx, g, "short") > 1;
    if ((missingAvg || missingMrk || missingFpl || missingFplUsd || hasFpl === void 0 || hasFplUsd === void 0) && status?.positions?.length) {
      const p = openRowPosition(status, g);
      if (p) {
        if (missingAvg) avg = p.average_price;
        if (missingMrk) mrk = p.mark_price;
        if (!sharedShort) {
          if (missingFpl) fpl = p.floating_profit_loss;
          if (hasFpl === void 0) hasFpl = p.has_floating_profit_loss;
          if (missingFplUsd) fplUsd = p.floating_profit_loss_usd;
          if (hasFplUsd === void 0) hasFplUsd = p.has_floating_profit_loss_usd;
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
      short_has_floating_profit_loss_usd: hasFplUsd
    };
  }
  function parseExpiryMsUtc(g) {
    const raw = g.expiration_timestamp_ms;
    if (raw !== null && raw !== void 0 && raw !== "") {
      if (typeof raw === "number" && Number.isFinite(raw)) return Math.round(raw);
      if (typeof raw === "bigint") return Number(raw);
      const s = String(raw).trim();
      if (/^\d+$/.test(s)) {
        const n = Number(s);
        return Number.isFinite(n) ? n : null;
      }
    }
    if (g.expiry) {
      const dt = luxon.DateTime.fromISO(String(g.expiry), { zone: "utc" });
      if (dt.isValid) return dt.toMillis();
    }
    return null;
  }
  function openRowDteDays(g) {
    const fromApi = num(g.dte_days) ?? num(g.dte);
    if (fromApi !== null) return fromApi;
    const ms = parseExpiryMsUtc(g);
    if (ms === null) return null;
    const exp = luxon.DateTime.fromMillis(ms, { zone: "utc" });
    if (!exp.isValid) return null;
    return exp.diff(luxon.DateTime.utc(), "days").days;
  }
  function optionPutCallLabel(g) {
    const t = String(g.option_type || "").toLowerCase();
    if (t === "call") return "Call";
    if (t === "put") return "Put";
    const n = String(g.short_instrument_name || "");
    if (/-C$/i.test(n) || n.endsWith("-C")) return "Call";
    return "Put";
  }
  function updateUnderlyingIndexCache(status, groups) {
    for (const k of ["BTC", "ETH"]) {
      const sv = num(status?.underlying_index_usd?.[k]);
      const gv = num(groups?.underlying_index_usd?.[k]);
      const v = sv > 0 ? sv : gv > 0 ? gv : null;
      if (v !== null) STATE.lastUnderlyingIndexUsd[k] = v;
    }
  }
  function mergeUnderlyingIndexUsd(status, groups) {
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
  function indexUsdUnderlying(status, groups, currency) {
    const cur = String(currency || "").toUpperCase();
    const m = mergeUnderlyingIndexUsd(status, groups);
    return num(m[cur]);
  }
  function openRowBookCollateralUpper(g) {
    let c = String(g.collateral_currency || "").toUpperCase();
    if (c === "BTC" || c === "ETH" || c === "USDC") return c;
    const n = String(g.short_instrument_name || "");
    if (n.includes("_USDC-")) return "USDC";
    if (n.startsWith("BTC-")) return "BTC";
    if (n.startsWith("ETH-")) return "ETH";
    return String(g.currency || "").toUpperCase() || "BTC";
  }
  function underlyingIndexKeyForGroup(g) {
    const book = openRowBookCollateralUpper(g);
    if (book === "BTC" || book === "ETH") return book;
    return String(g.currency || "BTC").toUpperCase();
  }
  function openRowFallbackIndexFromPositions(status, g) {
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
  function openRowSpotIndexUsdForPnl(g, status, groups) {
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
  function openRowSpotUsdScalarForBook(g, status, groups) {
    const coll = openRowBookCollateralUpper(g);
    if (coll === "USDC") return 1;
    if (coll === "BTC" || coll === "ETH") {
      const s = openRowSpotIndexUsdForPnl(g, status, groups);
      return s !== null && s > 0 ? s : null;
    }
    return null;
  }
  function openRowPositionSignedSizeForPnl(p) {
    return openRowPositionSignedSizeForDisplay(p);
  }
  function openRowLegSignedSizeForDisplay(g, status, role, groups = null) {
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
  function openRowLegFieldValue(g, status, role, fieldName) {
    if (role === "short" && hasOwn(g, `short_${fieldName}`)) {
      const v = g[`short_${fieldName}`];
      if (v !== null && v !== void 0 && v !== "") return v;
    }
    const p = openRowLegPosition(status, g, role);
    return p?.[fieldName] ?? null;
  }
  function openRowLegPremiumMtmNative(status, g, role, groups = null) {
    const avg = num(openRowLegFieldValue(g, status, role, "average_price"));
    const mrk = num(openRowLegFieldValue(g, status, role, "mark_price"));
    const sz = openRowLegSignedSizeForDisplay(g, status, role, groups);
    if (avg === null || mrk === null || sz === null) return null;
    return (mrk - avg) * sz;
  }
  function openRowLegPnlUsd(status, g, groups, role) {
    const native = openRowLegPremiumMtmNative(status, g, role, groups);
    if (native === null) return null;
    const spot = openRowSpotUsdScalarForBook(g, status, groups);
    if (spot === null || spot <= 0) return null;
    return native * spot;
  }
  function openRowPositionPremiumMtmNative(status, g, groups = null) {
    const ctx = groups ?? STATE.groups;
    if (countOpenGroupsSharingLeg(status, ctx, g, "short") > 1) {
      const p2 = openRowPosition(status, g);
      if (!p2) return null;
      const avg2 = num(p2.average_price);
      const mrk2 = num(p2.mark_price);
      const sz2 = openRowLegGroupSignedSize(g, "short");
      if (avg2 === null || mrk2 === null || sz2 === null) return null;
      return (mrk2 - avg2) * sz2;
    }
    const p = openRowPosition(status, g);
    if (!p) return null;
    const avg = num(p.average_price);
    const mrk = num(p.mark_price);
    const sz = openRowPositionSignedSizeForPnl(p);
    if (avg === null || mrk === null || sz === null) return null;
    return (mrk - avg) * sz;
  }
  function openRowNativeUnrealizedDisplayValue(g, status) {
    const calc = openRowPositionPremiumMtmNative(status, g);
    if (calc !== null) return calc;
    if (g.short_has_floating_profit_loss) {
      const v = num(g.short_floating_profit_loss);
      if (v !== null) return v;
    }
    return null;
  }
  function openRowPositionPnlUsd(status, g, groups) {
    const ctx = groups ?? STATE.groups;
    if (countOpenGroupsSharingLeg(status, ctx, g, "short") > 1) {
      const p2 = openRowPosition(status, g);
      if (!p2) return null;
      const avg2 = num(p2.average_price);
      const mrk2 = num(p2.mark_price);
      const sz2 = openRowLegGroupSignedSize(g, "short");
      if (avg2 === null || mrk2 === null || sz2 === null) return null;
      const spot2 = openRowSpotUsdScalarForBook(g, status, groups);
      if (spot2 === null || spot2 <= 0) return null;
      return (mrk2 - avg2) * sz2 * spot2;
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
  function openRowUnrealizedUsdPreferDeribit(g, status, groups) {
    const calc = openRowPositionPnlUsd(status, g, groups);
    if (calc !== null) return calc;
    if (g.short_has_floating_profit_loss_usd) {
      const v = num(g.short_floating_profit_loss_usd);
      if (v !== null) return v;
    }
    return null;
  }
  function fmtUsdcUnrealizedDeribit(usdEstimate) {
    const v = num(usdEstimate);
    if (v === null) return "\u2014";
    const body = new Intl.NumberFormat("en-US", {
      maximumFractionDigits: 2,
      minimumFractionDigits: 2
    }).format(v);
    const sp = '<span class="text-slate-500">';
    const ep = "</span>";
    return `${sp}($)${ep}\xA0${body}`;
  }
  function openRowUnrealizedUsd(g) {
    const u = num(g.unrealized_usdc_estimate);
    if (u !== null) return u;
    const ec = num(g.entry_credit);
    const cd = num(g.current_debit);
    if (ec !== null && cd !== null) return ec - cd;
    return null;
  }
  function openRowSpreadLegPnlUsd(status, g, groups) {
    const shortPnl = openRowLegPnlUsd(status, g, groups, "short");
    const longPnl = openRowLegPnlUsd(status, g, groups, "long");
    if (shortPnl === null && longPnl === null) return null;
    return (shortPnl || 0) + (longPnl || 0);
  }
  function openRowSpreadLegMtmUsdSumStrict(status, g, groups) {
    const shortPnl = openRowLegPnlUsd(status, g, groups, "short");
    const longPnl = openRowLegPnlUsd(status, g, groups, "long");
    if (shortPnl === null || longPnl === null) return null;
    return shortPnl + longPnl;
  }
  function openRowDisplayUnrealizedUsd(g, status, groups) {
    if (strategyId(g) === "bull_put_spread") {
      return openRowSpreadLegMtmUsdSumStrict(status, g, groups) ?? openRowUnrealizedUsd(g) ?? openRowSpreadLegPnlUsd(status, g, groups) ?? openRowUnrealizedUsdPreferDeribit(g, status, groups);
    }
    return openRowUnrealizedUsdPreferDeribit(g, status, groups) ?? openRowUnrealizedUsd(g);
  }
  function openRowEntryCreditUsd(g, status, groups) {
    const credit = num(g.entry_credit);
    return credit;
  }
  function fmtNativeUnrealizedDisplay(mtm, collateralCurrency) {
    const coll = String(collateralCurrency || "").toUpperCase();
    if (coll === "USDC") {
      if (mtm === null) return "\u2014";
      return fmtUsdcUnrealizedDeribit(mtm);
    }
    if (mtm === null) return "\u2014";
    if (coll === "BTC") return `<span class="text-slate-500">\u20BF</span>\xA0${fmtNum(mtm, 8)}`;
    if (coll === "ETH") return `<span class="text-slate-500">\u2666</span>\xA0${fmtNum(mtm, 8)}`;
    return fmtNum(mtm, 8);
  }
  function openRowDisplayNativeUnrealizedValue(g, status, groups) {
    if (strategyId(g) !== "bull_put_spread") return openRowNativeUnrealizedDisplayValue(g, status);
    const native = num(g.unrealized_coin_native);
    if (native !== null) return native;
    const usd = openRowDisplayUnrealizedUsd(g, status, groups);
    const spot = openRowSpotUsdScalarForBook(g, status, groups);
    if (usd === null || spot === null || spot <= 0) return null;
    return usd / spot;
  }
  function fmtNum(value, places = 4) {
    const n = num(value);
    if (n === null) return "\u2014";
    return (places >= 8 ? fmt.num8 : fmt.num4).format(n);
  }
  function hasOwn(obj, key) {
    return Object.prototype.hasOwnProperty.call(obj || {}, key);
  }
  function bookEquityUsdForDisplay(book, status) {
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
  function portfolioDayPnlUsdForDisplay(portfolio, totalEquity, dayStart) {
    const netFlow = num(portfolio?.day_net_flow_usdc);
    return num(portfolio?.day_pnl_usdc_ex_flow) ?? num(portfolio?.day_pnl_usdc_ex_flow_ex_spot) ?? (totalEquity !== null && dayStart !== null ? totalEquity - dayStart - (netFlow ?? 0) : null);
  }
  function bookDayPnlUsdForDisplay(book, status, equityUsdc, dayStartUsdc) {
    const b = String(book || "").toUpperCase();
    const portfolio = status?.portfolio || {};
    const netFlow = num(portfolio?.day_net_flow_usdc_by_book?.[b]);
    return num(portfolio?.day_pnl_usdc_ex_flow_by_book?.[b]) ?? num(portfolio?.day_pnl_usdc_ex_flow_ex_spot_by_book?.[b]) ?? (equityUsdc !== null && dayStartUsdc !== null ? equityUsdc - dayStartUsdc - (netFlow ?? 0) : null);
  }
  function pnlClass(value) {
    const n = num(value);
    if (n === null || n === 0) return "";
    return n > 0 ? "pnl-pos" : "pnl-neg";
  }
  function fmtTime(msOrIso) {
    if (msOrIso === null || msOrIso === void 0) return "\u2014";
    let dt;
    if (typeof msOrIso === "number") dt = luxon.DateTime.fromMillis(msOrIso, { zone: "utc" });
    else dt = luxon.DateTime.fromISO(String(msOrIso), { zone: "utc" });
    if (!dt.isValid) return "\u2014";
    return dt.toLocal().toFormat("yyyy-LL-dd HH:mm");
  }
  function fmtDate(msOrIso) {
    if (msOrIso === null || msOrIso === void 0) return "\u2014";
    let dt;
    if (typeof msOrIso === "number") dt = luxon.DateTime.fromMillis(msOrIso, { zone: "utc" });
    else dt = luxon.DateTime.fromISO(String(msOrIso), { zone: "utc" });
    if (!dt.isValid) return "\u2014";
    return dt.toLocal().toFormat("yyyy-LL-dd");
  }
  function rollingWindowProfitLabel(days) {
    const n = Math.round(days ?? 30);
    return i18n(`Total profit (rolling ${n}d)`, `\u5DF2\u5BE6\u73FE\u640D\u76CA\uFF08\u6EFE\u52D5 ${n} \u65E5\u8996\u7A97\uFF09`);
  }
  function rollingWindowAprLabel(days) {
    const n = Math.round(days ?? 30);
    return i18n(`Realized APR (rolling ${n}d)`, `\u5DF2\u5BE6\u73FE\u5E74\u5316\uFF08\u6EFE\u52D5 ${n} \u65E5\u8996\u7A97\uFF09`);
  }
  function rollingWindowPnlHint(days) {
    const n = Math.round(days ?? 30);
    return i18n(`Closes in last ${n}d only`, `\u50C5\u8A08\u6700\u8FD1 ${n} \u65E5\u5167\u5E73\u5009`);
  }
  function rollingWindowAprHint(days) {
    const n = Math.round(days ?? 30);
    return i18n(
      `Last ${n}d closes \xF7 ledger total equity`,
      `\u8FD1 ${n} \u65E5\u5E73\u5009 \xF7 \u7576\u65E5\u7E3D\u6B0A\u76CA`
    );
  }
  function realizedSummaryUrl(days = 30) {
    let url = `/api/realized_summary?days=${days}`;
    const cap = aprEffectiveCapitalUsdc();
    if (cap !== null) {
      url += `&effective_capital_usdc=${encodeURIComponent(String(cap))}`;
    }
    return url;
  }
  function dashboardBundleUrl(days = 30) {
    let url = `/api/dashboard_bundle?days=${days}`;
    const cap = aprEffectiveCapitalUsdc();
    if (cap !== null) {
      url += `&effective_capital_usdc=${encodeURIComponent(String(cap))}`;
    }
    return url;
  }
  function applyDashboardBundlePayload(d) {
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
  function lifetimePerformanceStartMs(report, groups) {
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
  function looksLikeCoveredCallRow(g) {
    if (!g || optionPutCallLabel(g).toLowerCase() !== "call") return false;
    const covered = num(g.covered_underlying_quantity);
    if (covered !== null && covered > 0) return true;
    if (String(g.short_label || "").startsWith("covered_call-")) return true;
    if (String(g.account_name || "") === "covered_call") return true;
    return String(g.account_env_file || "").includes(".env.covered_call");
  }
  function normalizeStrategyId(raw) {
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
      coveredcall: "covered_call"
    };
    return aliases[normalized] || normalized;
  }
  function strategyId(g) {
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
  function strategyInfo(id) {
    const key = normalizeStrategyId(id);
    if (STRATEGY_BY_ID[key]) {
      const base = STRATEGY_BY_ID[key];
      if (!INVESTOR || !INVESTOR_ZH) return base;
      return {
        ...base,
        title: base.titleZh || base.title,
        short: base.shortZh || base.short,
        chipShort: base.chipShortZh || base.chipShort || base.shortZh || base.short,
        description: base.descriptionZh || base.description
      };
    }
    const label = key ? key.replaceAll("_", " ") : "\u2014";
    return {
      id: key || "",
      title: label,
      short: label,
      chipShort: label,
      accentClass: "border-slate-700",
      description: ""
    };
  }
  function strategyTitle(id) {
    return strategyInfo(id).title;
  }
  function strategyChipClass(id) {
    const key = normalizeStrategyId(id);
    if (key === "naked_short") return "chip-strategy-naked";
    if (key === "bull_put_spread") return "chip-strategy-spread";
    if (key === "covered_call") return "chip-strategy-covered";
    return "chip-strategy-unknown";
  }
  function strategyChipHtml(id, { compact = false } = {}) {
    const info = strategyInfo(id);
    const cls = strategyChipClass(info.id || id);
    const label = compact ? info.chipShort || info.short : info.short;
    const compactClass = compact ? " chip--compact" : "";
    return `<span class="chip ${cls}${compactClass}">${escapeHtml(label)}</span>`;
  }
  function tradeGroupKey(g) {
    return [
      String(g?.account_name || ""),
      String(g?.group_id || ""),
      String(g?.short_instrument_name || "")
    ].join("\0");
  }
  var TRADE_GROUP_ENRICH_KEYS = [
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
    "short_strike"
  ];
  function hasTradeGroupValue(v) {
    if (v === null || v === void 0 || v === "") return false;
    if (typeof v === "number" && !Number.isFinite(v)) return false;
    return true;
  }
  function mergeTradeGroupRow(a, b) {
    const out = { ...b, ...a };
    for (const key of TRADE_GROUP_ENRICH_KEYS) {
      if (hasTradeGroupValue(a[key])) out[key] = a[key];
      else if (hasTradeGroupValue(b[key])) out[key] = b[key];
    }
    return out;
  }
  function dedupeTradeGroups(rows) {
    const byKey = /* @__PURE__ */ new Map();
    for (const g of rows || []) {
      const key = tradeGroupKey(g);
      const prev = byKey.get(key);
      byKey.set(key, prev ? mergeTradeGroupRow(prev, g) : g);
    }
    return [...byKey.values()];
  }
  function isOpenTradeGroup(g) {
    const st = String(g?.status || "open").toLowerCase();
    return st !== "closed";
  }
  function isClosedTradeGroup(g) {
    const st = String(g?.status || "").toLowerCase();
    if (st === "closed") return true;
    return closedTimestampMs(g) !== null;
  }
  var PHANTOM_RECONCILE_MAX_HOLDING_MS = 3e5;
  function openShortInstrumentNames(status, groups) {
    const names = /* @__PURE__ */ new Set();
    for (const g of currentOpenRows(status, groups)) {
      const name = String(g?.short_instrument_name || "").trim();
      if (name) names.add(name);
    }
    return names;
  }
  function isPhantomReconcileClose(g, status, groups) {
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
  function isDisplayableClosedTradeGroup(g, status, groups) {
    return isClosedTradeGroup(g) && !isPhantomReconcileClose(g, status, groups);
  }
  function currentOpenRows(status, groups) {
    const out = [];
    const seen = /* @__PURE__ */ new Set();
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
  function mergedClosedRows(report, groups, limit = 20, status = null) {
    const st = status ?? STATE.status;
    const rows = dedupeTradeGroups([
      ...groups?.closed || [],
      ...report?.recent_closed_trades || []
    ]).filter((g) => isDisplayableClosedTradeGroup(g, st, groups));
    rows.sort((a, b) => (closedTimestampMs(b) || 0) - (closedTimestampMs(a) || 0));
    return rows.slice(0, limit);
  }
  function closedRowsForStrategyStats(report, groups) {
    return mergedClosedRows(report, groups, 500);
  }
  function strategyOrder(ids) {
    const known = STRATEGIES.map((s) => s.id);
    const ordered = known.filter((id) => ids.has(id));
    const unknown = [...ids].filter((id) => !known.includes(id)).sort();
    return ordered.concat(unknown);
  }
  function strikeFromInstrumentName(instrumentName) {
    const match = String(instrumentName || "").match(/-([0-9]+(?:\.[0-9]+)?)-[CP]$/i);
    if (!match) return null;
    return num(match[1]);
  }
  function openRowLegStrike(g, role) {
    const explicit = role === "long" ? num(g?.long_strike) : num(g?.short_strike);
    if (explicit !== null) return explicit;
    return strikeFromInstrumentName(openRowLegInstrumentName(g, role));
  }
  function fmtStrike(value) {
    const v = num(value);
    if (v === null) return "\u2014";
    return fmtUsd(v, 0);
  }
  function bullPutSpreadWidth(g) {
    const shortStrike = openRowLegStrike(g, "short");
    const longStrike = openRowLegStrike(g, "long");
    if (shortStrike === null || longStrike === null) return null;
    return shortStrike - longStrike;
  }
  function openRowLegPriceGap(g, status, fieldName) {
    const shortPrice = num(openRowLegFieldValue(g, status, "short", fieldName));
    const longPrice = num(openRowLegFieldValue(g, status, "long", fieldName));
    if (shortPrice === null || longPrice === null) return null;
    return shortPrice - longPrice;
  }
  function strategyLegDetail(g) {
    const longLeg = String(g?.long_instrument_name || "").trim();
    if (longLeg) return i18n(`Long ${longLeg}`, `\u8CB7\u817F ${longLeg}`);
    const covered = num(g?.covered_underlying_quantity);
    if (covered !== null && covered > 0) {
      return i18n(
        `Covered ${fmtNum(covered, 4)} ${String(g.currency || "").toUpperCase()}`,
        `\u5099\u514C ${fmtNum(covered, 4)} ${String(g.currency || "").toUpperCase()}`
      );
    }
    return i18n("Single short leg", "\u55AE\u908A\u8CE3\u51FA");
  }
  function accountHint(g) {
    const account = String(g?.account_name || "").trim();
    return account ? `Account ${account}` : "";
  }
  function groupHoldingDays(g) {
    const explicit = num(g?.holding_days);
    if (explicit !== null) return explicit;
    const closed = closedTimestampMs(g);
    const entry = entryTimestampMs(g);
    if (closed === null || entry === null || entry <= 0) return null;
    return Math.max(closed - entry, 0) / 864e5;
  }
  function entryTimestampMs(g) {
    const ms = num(g?.entry_timestamp_ms);
    if (ms !== null) return ms;
    if (g?.entry_timestamp) {
      const dt = luxon.DateTime.fromISO(String(g.entry_timestamp), { zone: "utc" });
      if (dt.isValid) return dt.toMillis();
    }
    return null;
  }
  function groupEntryDteDaysAtOpen(g) {
    const entry = entryTimestampMs(g);
    const exp = parseExpiryMsUtc(g);
    if (entry === null || exp === null || exp <= entry) return null;
    return (exp - entry) / 864e5;
  }
  function tradeGroupAprBook(g) {
    return openRowBookCollateralUpper(g);
  }
  function collateralBookSpotUsd(g, status) {
    const book = tradeGroupAprBook(g);
    if (book === "USDC") return null;
    return num(status?.underlying_index_usd?.[book]) ?? num(STATE.groups?.underlying_index_usd?.[book]) ?? num(STATE.lastSpotUsd?.[book]) ?? num(g?.close_index_usd);
  }
  function realizedPnlCoinNative(g, status) {
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
  function isInverseCoinBookGroup(g) {
    const book = tradeGroupAprBook(g);
    return book === "BTC" || book === "ETH";
  }
  function realizedPnlDisplayUsdc(g, status) {
    const book = tradeGroupAprBook(g);
    if (book === "USDC") return num(g?.realized_pnl);
    const native = realizedPnlCoinNative(g, status);
    const spot = collateralBookSpotUsd(g, status);
    if (native !== null && spot !== null && spot > 0) return native * spot;
    return null;
  }
  function realizedPnlInAprBookNative(g, status) {
    const book = tradeGroupAprBook(g);
    if (book === "USDC") return num(g?.realized_pnl);
    const native = realizedPnlCoinNative(g, status);
    if (native !== null) return native;
    const pnlUsd = num(g?.realized_pnl);
    if (pnlUsd === null) return null;
    const idx = num(g?.close_index_usd) ?? num(status?.underlying_index_usd?.[book]) ?? num(STATE.groups?.underlying_index_usd?.[book]) ?? num(STATE.lastSpotUsd?.[book]);
    if (idx === null || idx <= 0) return null;
    return pnlUsd / idx;
  }
  function tradeGroupContractSize(g) {
    const cs = num(g?.contract_size);
    return cs !== null && cs > 0 ? cs : 1;
  }
  function tradeGroupOpenedAmountPerContract(g, status) {
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
        const idx = usdcLinearUnderlyingIndexUsd(g, status) ?? entryIndexUsdForGroup(g, status) ?? collateralBookSpotUsd(g, status) ?? closeIndexUsdForGroup(g, status);
        if (idx !== null && idx > 0) return idx;
      } else {
        const strike = openRowLegStrike(g, "short");
        if (strike !== null && strike > 0) return strike;
      }
      return null;
    }
    return cs;
  }
  function tradeGroupOpenedNotional(g, status) {
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
  function tradeGroupAprCapitalBase(g, status) {
    return tradeGroupOpenedNotional(g, status);
  }
  function annualizedAprOnPositionCapital(g, status) {
    const pnlN = realizedPnlInAprBookNative(g, status);
    const holding = groupHoldingDays(g);
    const capital = tradeGroupAprCapitalBase(g, status);
    if (pnlN === null || capital === null || capital <= 0 || holding === null || holding <= 0) {
      return null;
    }
    return pnlN / capital * (365 / holding);
  }
  function fmtRealizedPnlDisplay(g, status) {
    const book = tradeGroupAprBook(g);
    if (!isInverseCoinBookGroup(g)) {
      const pnlUsd = num(g?.realized_pnl);
      return pnlUsd === null ? "\u2014" : fmtUsd(pnlUsd);
    }
    const native = realizedPnlCoinNative(g, status);
    if (native === null) {
      const pnlUsd = num(g?.realized_pnl);
      return pnlUsd === null ? "\u2014" : fmtUsd(pnlUsd);
    }
    const usdc = realizedPnlDisplayUsdc(g, status);
    const places = book === "BTC" ? 5 : 4;
    const nativeStr = `${fmtNum(native, places)} ${book}`;
    return INVESTOR_ZH ? `${fmtUsd(usdc)}\uFF08${nativeStr}\uFF09` : `${fmtUsd(usdc)} (${nativeStr})`;
  }
  function fmtNativeBookAmount(native, book) {
    const n = num(native);
    if (n === null) return `\u2014 ${book || ""}`.trim();
    const body = new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 }).format(n);
    return `${body} ${book}`;
  }
  function fmtUsdWithNativeBookAmount(usd, native, book) {
    const usdStr = fmtUsd(usd);
    if (native === null || !book || book === "USDC") return usdStr;
    const nativeStr = fmtNativeBookAmount(native, book);
    return INVESTOR_ZH ? `${usdStr}\uFF08${nativeStr}\uFF09` : `${usdStr} (${nativeStr})`;
  }
  function fmtUsdNativeBookStackHtml(usd, native, book) {
    const usdStr = fmtUsd(usd);
    if (native === null || !book || book === "USDC") return usdStr;
    const nativeStr = escapeHtml(fmtNativeBookAmount(native, book));
    return `<span class="open-position-value-stack"><span class="open-position-value-line">${usdStr}</span><span class="open-position-value-sub">${nativeStr}</span></span>`;
  }
  function nativeFromUsdAtIndex(usd, indexUsd) {
    const v = num(usd);
    const idx = num(indexUsd);
    if (v === null || idx === null || idx <= 0) return null;
    return v / idx;
  }
  function entryIndexUsdForGroup(g, status) {
    const book = tradeGroupAprBook(g);
    if (book === "USDC") return null;
    return num(g?.entry_index_usd) ?? num(status?.underlying_index_usd?.[book]) ?? num(STATE.groups?.underlying_index_usd?.[book]) ?? num(STATE.lastSpotUsd?.[book]);
  }
  function closeIndexUsdForGroup(g, status) {
    const book = tradeGroupAprBook(g);
    if (book === "USDC") return null;
    return num(g?.close_index_usd) ?? num(status?.underlying_index_usd?.[book]) ?? num(STATE.groups?.underlying_index_usd?.[book]) ?? num(STATE.lastSpotUsd?.[book]) ?? num(g?.entry_index_usd);
  }
  function usdcLinearUnderlyingIndexUsd(g, status) {
    const key = underlyingIndexKeyForGroup(g);
    if (key !== "BTC" && key !== "ETH") return null;
    const candidates = [
      num(g?.entry_index_usd),
      num(g?.close_index_usd),
      num(status?.underlying_index_usd?.[key]),
      num(STATE.groups?.underlying_index_usd?.[key]),
      num(STATE.lastSpotUsd?.[key]),
      openRowLegStrike(g, "short")
    ];
    for (const value of candidates) {
      if (value !== null && value > 100) return value;
    }
    return null;
  }
  function groupRealizedApr(g, status) {
    const holding = groupHoldingDays(g);
    if (holding === null || holding <= 0) return null;
    const stored = num(g?.realized_apr_on_equity) ?? num(g?.realized_annualized_return);
    if (stored !== null) return stored;
    return annualizedAprOnPositionCapital(g, status);
  }
  function activityAmountDisplay(g, status, groups = null) {
    const ctx = groups ?? STATE.groups;
    const id = strategyId(g);
    if (id === "bull_put_spread") {
      const shortAmt = openRowLegSignedSizeForDisplay(g, status, "short", ctx);
      const longAmt = openRowLegSignedSizeForDisplay(g, status, "long", ctx);
      if (shortAmt === null && longAmt === null) {
        const q2 = num(g.quantity);
        if (q2 === null) return null;
        return `${fmtNum(-Math.abs(q2), 4)} / ${fmtNum(Math.abs(q2), 4)}`;
      }
      const parts = [];
      if (shortAmt !== null) parts.push(fmtNum(shortAmt, 4));
      if (longAmt !== null) parts.push(fmtNum(longAmt, 4));
      return parts.length ? parts.join(" / ") : null;
    }
    if (!isClosedTradeGroup(g)) {
      const shown = fmtShortAmountDisplay(g, status, ctx);
      return shown === "\u2014" ? null : shown;
    }
    const q = num(g.quantity);
    if (q === null) return null;
    return fmtNum(-Math.abs(q), 4);
  }
  function groupEntryNetCreditAtOpen(g, status) {
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
      const tol = Math.max(0.01, Math.abs(gross) * 1e-3);
      if (Math.abs(gross - credit) <= tol) netUsdc = credit - fee;
      else if (Math.abs(gross - (credit + fee)) <= tol) netUsdc = credit;
    }
    if (book === "USDC") return netUsdc;
    if (idx === null || idx <= 0) return null;
    return netUsdc / idx;
  }
  function groupEntryNetApr(g, status) {
    const dte = groupEntryDteDaysAtOpen(g);
    const opened = tradeGroupOpenedNotional(g, status);
    const net = groupEntryNetCreditAtOpen(g, status);
    if (net === null || net <= 0 || dte === null || dte <= 0 || opened === null || opened <= 0) {
      return num(g?.entry_net_apr);
    }
    return net / opened * (365 / dte);
  }
  function groupEntryFeeUsd(g) {
    return num(g?.entry_fee);
  }
  function groupEntryFeeNative(g, status) {
    return nativeFromUsdAtIndex(groupEntryFeeUsd(g), entryIndexUsdForGroup(g, status));
  }
  function groupCloseFeeUsd(g) {
    const openEst = num(g?.current_close_fee);
    if (openEst !== null && openEst > 0) return openEst;
    return num(g?.realized_close_fee);
  }
  function groupCloseFeeNative(g, status) {
    const openEst = num(g?.current_close_fee);
    const index = openEst !== null && openEst > 0 ? collateralBookSpotUsd(g, status) : closeIndexUsdForGroup(g, status);
    return nativeFromUsdAtIndex(groupCloseFeeUsd(g), index);
  }
  function groupEntryCreditNative(g, status) {
    return nativeFromUsdAtIndex(num(g?.entry_credit), entryIndexUsdForGroup(g, status));
  }
  function allTradeGroupsForActivity(status, groups) {
    const rows = [];
    const seen = /* @__PURE__ */ new Set();
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
  function activityOpenRows(status, groups) {
    return dedupeTradeGroups(allTradeGroupsForActivity(status, groups)).filter((g) => isOpenTradeGroup(g)).sort((a, b) => (entryTimestampMs(b) || 0) - (entryTimestampMs(a) || 0));
  }
  function activityClosedRows(status, report, groups) {
    return mergedClosedRows(report, groups, 500, status);
  }
  function paginateRows(rows, page, pageSize) {
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
      end: Math.min(startIdx + pageSize, total)
    };
  }
  function activityPaginationHtml(section, pageInfo) {
    const { page, totalPages, total, start, end } = pageInfo;
    if (total <= ACTIVITY_PAGE_SIZE) return "";
    const prevDisabled = page <= 1;
    const nextDisabled = page >= totalPages;
    const label = i18n(
      `${start}\u2013${end} of ${total} \xB7 page ${page} of ${totalPages}`,
      `${start}\u2013${end} / \u5171 ${total} \u7B46 \xB7 \u7B2C ${page} / ${totalPages} \u9801`
    );
    return `<div class="activity-pagination" data-activity-section="${escapeHtml(section)}">
      <button type="button" class="filter-chip activity-page-btn" data-activity-section="${escapeHtml(
      section
    )}" data-direction="prev"${prevDisabled ? " disabled" : ""}>${i18n("Prev", "\u4E0A\u4E00\u9801")}</button>
      <span class="activity-pagination-label">${escapeHtml(label)}</span>
      <button type="button" class="filter-chip activity-page-btn" data-activity-section="${escapeHtml(
      section
    )}" data-direction="next"${nextDisabled ? " disabled" : ""}>${i18n("Next", "\u4E0B\u4E00\u9801")}</button>
    </div>`;
  }
  function tradeGroupActivityTitle(g) {
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
  function activityDetailLine(parts) {
    return parts.filter((p) => p).map((p) => {
      if (typeof p === "string") return `<span>${escapeHtml(p)}</span>`;
      return `<span>${escapeHtml(p[0])} <strong>${escapeHtml(String(p[1]))}</strong></span>`;
    }).join("");
  }
  function activityLifecycleCardHtml(g, status, groups) {
    const id = strategyId(g);
    const book = tradeGroupAprBook(g) || "\u2014";
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
    const entryCreditDisplay = credit === null ? "\u2014" : fmtUsdWithNativeBookAmount(credit, creditNative, book);
    const entryAprDisplay = entryApr === null ? "\u2014" : fmtPct(entryApr, 1);
    const entryFeeDisplay = entryFee === null ? null : fmtUsdWithNativeBookAmount(entryFee, entryFeeNative, book);
    const entryMetaSecondary = [
      [i18n("Opened", "\u958B\u5009"), fmtTime(entryMs)],
      amountLabel !== null ? [i18n("Amount", "\u6578\u91CF"), amountLabel] : null,
      entryFeeDisplay ? [i18n("Entry fee", "\u9032\u5834\u624B\u7E8C\u8CBB"), entryFeeDisplay] : null
    ].filter(Boolean);
    const entryInner = `<div class="activity-entry-metrics">
      <div class="activity-entry-metric">
        <span class="activity-entry-metric-label">${i18n("Credit", "\u6536\u6B0A\u5229\u91D1")}</span>
        <span class="activity-entry-metric-value ${pnlClass(credit)}">${escapeHtml(entryCreditDisplay)}</span>
      </div>
      <div class="activity-entry-metric">
        <span class="activity-entry-metric-label">${i18n("Net APR", "\u6DE8\u5E74\u5316\u5831\u916C\u7387")}</span>
        <span class="activity-entry-metric-value ${pnlClass(entryApr)}">${escapeHtml(entryAprDisplay)}</span>
      </div>
    </div>
    <div class="activity-phase-meta activity-phase-meta-secondary">
      ${activityDetailLine(entryMetaSecondary)}
    </div>`;
    let exitInner = "";
    if (closed) {
      const exitMetaSecondary = [
        [i18n("Closed", "\u5E73\u5009"), fmtTime(closedTimestampMs(g))],
        closeFee !== null ? [i18n("Close fee", "\u5E73\u5009\u624B\u7E8C\u8CBB"), fmtUsdWithNativeBookAmount(closeFee, closeFeeNative, book)] : null,
        holding !== null ? [i18n("Held", "\u6301\u6709"), `${fmtNum(holding, 1)}${INVESTOR_ZH ? " \u5929" : "d"}`] : null
      ].filter(Boolean);
      const pnlValue = pnl !== null ? `<span class="activity-closed-pnl-value ${pnlClass(pnl)}">${fmtRealizedPnlDisplay(g, status)}</span>` : `<span class="activity-closed-pnl-value activity-closed-pnl-value-missing">\u2014</span>`;
      const aprValue = realizedApr !== null ? `<span class="activity-closed-pnl-value ${pnlClass(realizedApr)}">${fmtPct(realizedApr, 1)}</span>` : `<span class="activity-closed-pnl-value activity-closed-pnl-value-missing">\u2014</span>`;
      const closedMetrics = `<div class="activity-closed-metrics">
        <div class="activity-closed-pnl">
          <span class="activity-closed-pnl-label">${i18n("Realized PnL", "\u5DF2\u5BE6\u73FE\u640D\u76CA")}</span>
          ${pnlValue}
        </div>
        <div class="activity-closed-pnl">
          <span class="activity-closed-pnl-label">${i18n("Realized APR", "\u5BE6\u73FE\u5E74\u5316\u5831\u916C")}</span>
          ${aprValue}
        </div>
      </div>`;
      exitInner = `${closedMetrics}<div class="activity-phase-meta activity-phase-meta-secondary">${activityDetailLine(
        exitMetaSecondary
      )}</div>`;
    } else {
      const exitMeta = [
        closeFee !== null ? [i18n("Est. close fee", "\u9810\u4F30\u5E73\u5009\u8CBB"), fmtUsdWithNativeBookAmount(closeFee, closeFeeNative, book)] : null
      ].filter(Boolean);
      exitInner = `<div class="activity-phase-meta">
        <span class="activity-status-pill is-open">${i18n("Open", "\u6301\u5009\u4E2D")}</span>
        ${exitMeta.length ? activityDetailLine(exitMeta) : `<span>${i18n("Est. close fee", "\u9810\u4F30\u5E73\u5009\u8CBB")} <strong>\u2014</strong></span>`}
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
          <div class="activity-phase-label">${i18n("Entry", "\u9032\u5834")}</div>
          ${entryInner}
        </div>
        <div class="activity-phase-divider" aria-hidden="true"></div>
        <div class="activity-phase activity-phase-exit">
          <div class="activity-phase-label">${i18n("Exit", "\u51FA\u5834")}</div>
          ${exitInner}
        </div>
      </div>
    </li>`;
  }
  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
  function showToast(msg) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => el.classList.add("hidden"), 5e3);
  }
  function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
  async function promisePool(factories, limit) {
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
  async function fetchJson(url, options = {}) {
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
      } catch (_) {
      }
      if (FETCH_JSON_RETRYABLE_STATUS.has(res.status) && attempt < maxAttempts - 1) {
        await delay(FETCH_JSON_RETRY_BASE_MS * (attempt + 1));
        continue;
      }
      throw new Error(detail);
    }
  }
  function renderInvestorHeaderIdentity(health) {
    if (!INVESTOR || !health) return;
    const name = String(health.investor_display_name || health.investor_id || "").trim();
    const h1 = document.querySelector(".app-header h1");
    if (h1 && name) {
      h1.textContent = `${name} \xB7 ${INVESTOR_ZH ? "\u6295\u8CC7\u7D44\u5408\u7E3D\u89BD" : "Investor summary"}`;
    }
    const sub = document.querySelector(".app-header h1 + p");
    if (!sub) return;
    if (!sub.dataset.investorBaseCopy) {
      sub.dataset.investorBaseCopy = sub.textContent || "";
    }
    const base = sub.dataset.investorBaseCopy;
    const investorId = String(health.investor_id || "").trim();
    sub.textContent = investorId && investorId !== name ? `${i18n("Investor id", "\u6295\u8CC7\u4EBA ID")}: ${investorId} \xB7 ${base}` : base;
  }
  function envBadgeToneClass(env) {
    if (INVESTOR) {
      if (env === "mainnet") {
        return "border-sky-500/50 bg-sky-500/10 text-sky-200";
      }
      if (env === "test") {
        return "border-amber-500/50 bg-amber-500/10 text-amber-200";
      }
      return "border-slate-500/50 bg-slate-500/10 text-slate-200";
    }
    return env === "mainnet" ? "border-rose-500/50 bg-rose-500/10 text-rose-200" : "border-emerald-500/50 bg-emerald-500/10 text-emerald-200";
  }
  function renderTopBar(health) {
    if (!health) return;
    renderInvestorHeaderIdentity(health);
    const env = (health.env || "").toLowerCase();
    const envBadge = document.getElementById("env-badge");
    if (envBadge) {
      envBadge.textContent = INVESTOR ? env === "mainnet" ? i18n("Network: Mainnet", "\u7DB2\u8DEF\uFF1A\u4E3B\u7DB2") : env === "multi" ? i18n("Network: Multi-account", "\u7DB2\u8DEF\uFF1A\u591A\u5E33\u6236") : env === "test" ? i18n("Network: Test", "\u7DB2\u8DEF\uFF1A\u6E2C\u8A66") : `${i18n("Network:", "\u7DB2\u8DEF\uFF1A")} ${env || "\u2014"}` : `env: ${env || "?"}`;
      envBadge.className = "text-xs px-2 py-0.5 rounded-full border " + envBadgeToneClass(env);
    }
    const strategyBadge = document.getElementById("strategy-badge");
    if (strategyBadge) {
      const strategy = normalizeStrategyId(health.option_strategy || "");
      const accountCount = health.accounts?.length || 0;
      strategyBadge.textContent = health.multi_account ? i18n(`strategy: multi (${accountCount} accounts)`, `\u7B56\u7565\uFF1A\u591A\u5E33\u6236\uFF08${accountCount}\uFF09`) : INVESTOR ? `${i18n("Strategy:", "\u7B56\u7565\uFF1A")} ${strategy ? strategyTitle(strategy) : "\u2014"}` : `strategy: ${strategy ? strategyTitle(strategy) : "?"}`;
      strategyBadge.className = "text-xs px-2 py-0.5 rounded-full border border-sky-500/50 bg-sky-500/10 text-sky-200";
    }
    const credsBadge = document.getElementById("creds-badge");
    if (credsBadge) {
      credsBadge.textContent = health.has_private_creds ? "creds: ok" : "creds: missing";
      credsBadge.className = "text-xs px-2 py-0.5 rounded-full border " + (health.has_private_creds ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-200" : "border-rose-500/50 bg-rose-500/10 text-rose-200");
    }
    const sched = document.getElementById("scheduler-badge");
    if (sched) {
      if (health.scheduler_running) {
        const sec = health.snapshot_interval_sec || 300;
        const min = Math.round(sec / 60);
        sched.textContent = i18n(`scheduler: on (every ${min} min)`, `\u5FEB\u7167\u6392\u7A0B\uFF1A\u6BCF ${min} \u5206\u9418`);
        sched.className = "text-xs px-2 py-0.5 rounded-full border border-emerald-500/50 bg-emerald-500/10 text-emerald-200";
      } else {
        sched.textContent = i18n("scheduler: off", "\u5FEB\u7167\u6392\u7A0B\uFF1A\u95DC\u9589");
        sched.className = "text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-700/30 text-slate-300";
      }
    }
    renderDataFreshnessBadge();
  }
  function renderRegime(status) {
    const badge = document.getElementById("regime-badge");
    if (!badge) return;
    const regime = status?.portfolio?.regime || "?";
    const regKey = String(regime).toLowerCase();
    const regZh = { normal: "\u6B63\u5E38", elevated: "\u504F\u9AD8", crisis: "\u8B66\u6212" };
    const regEn = { normal: "Normal", elevated: "Elevated", crisis: "Crisis" };
    badge.textContent = INVESTOR ? `${i18n("Risk posture:", "\u98A8\u63A7\u72C0\u614B\uFF1A")} ${INVESTOR_ZH ? regZh[regKey] || regime : regEn[regKey] || regime}` : `regime: ${regime}`;
    const cls = regime === "normal" ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-200" : regime === "elevated" ? "border-amber-500/50 bg-amber-500/10 text-amber-200" : regime === "crisis" ? "border-rose-500/50 bg-rose-500/10 text-rose-200" : "border-slate-600 bg-slate-700/30 text-slate-300";
    badge.className = `text-xs px-2 py-0.5 rounded-full border ${cls}`;
  }
  function bookCardHtml(book, status) {
    const portfolio = status?.portfolio || {};
    const accounts = status?.accounts || {};
    const account = accounts[book] || {};
    const isRiskBook = hasOwn(portfolio?.equity_by_book, book);
    const equityNative = num(account.equity);
    const equityUsdc = bookEquityUsdForDisplay(book, status);
    const dayStartUsdc = isRiskBook ? num(portfolio?.day_start_equity_by_book?.[book]) : null;
    const drawdownPct = num(portfolio?.day_drawdown_pct_by_book?.[book]);
    const dayPnlUsdc = bookDayPnlUsdForDisplay(book, status, equityUsdc, dayStartUsdc);
    const margin = portfolio?.margin_ratios_by_currency?.[book] || {};
    const imRatio = num(margin.im_ratio);
    const mmRatio = num(margin.mm_ratio);
    const delta = num(portfolio?.delta_totals_by_currency?.[book]);
    const regime = portfolio?.regime_by_currency?.[book];
    const cooling = portfolio?.cooling_down_by_book?.[book];
    const hardDerisk = portfolio?.hard_derisk_by_book?.[book];
    const haltEntries = portfolio?.halt_entries_by_book?.[book];
    const haltReasons = portfolio?.halt_entry_reasons_by_book?.[book] || [];
    const accentClass = book === "BTC" ? "book-card-btc" : book === "ETH" ? "book-card-eth" : "book-card-usdc";
    const chips = [];
    if (!isRiskBook) {
      chips.push('<span class="chip chip-muted">not traded</span>');
    }
    if (regime && isRiskBook) {
      const cls = regime === "normal" ? "chip-ok" : regime === "elevated" ? "chip-warn" : "chip-bad";
      chips.push(`<span class="chip ${cls}">${regime}</span>`);
    }
    if (cooling) chips.push('<span class="chip chip-warn">cooling</span>');
    if (hardDerisk) chips.push('<span class="chip chip-bad">hard derisk</span>');
    if (haltEntries) chips.push('<span class="chip chip-warn">halt entries</span>');
    if (chips.length === 0) chips.push('<span class="chip chip-ok">healthy</span>');
    const imPct = imRatio !== null ? Math.min(1, Math.max(0, imRatio)) : 0;
    const imBarCls = imRatio === null ? "bar-ok" : imRatio >= 0.45 ? "bar-bad" : imRatio >= 0.35 ? "bar-warn" : "bar-ok";
    const mmPct = mmRatio !== null ? Math.min(1, Math.max(0, mmRatio)) : 0;
    const mmBarCls = mmRatio === null ? "bar-ok" : mmRatio >= 0.33 ? "bar-bad" : mmRatio >= 0.22 ? "bar-warn" : "bar-ok";
    return `
    <div class="rounded-2xl border ${accentClass} bg-slate-900/60 p-4 shadow">
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-sm font-semibold tracking-wide text-slate-200">${book} BOOK</h3>
        <div class="flex flex-wrap gap-1">${chips.join("")}</div>
      </div>
      <div class="text-2xl font-mono">${fmtUsd(equityUsdc)}</div>
      <div class="text-xs text-slate-500 mb-3">
        ${equityNative !== null ? fmtNum(equityNative, 8) + " " + book : ""}
        ${dayStartUsdc !== null ? "\xB7 day-start " + fmtUsd(dayStartUsdc) : ""}
      </div>
      <div class="kv"><span class="k">Day P&amp;L</span><span class="v ${pnlClass(
      dayPnlUsdc
    )}">${fmtUsd(dayPnlUsdc)}</span></div>
      <div class="kv"><span class="k">Day drawdown</span><span class="v ${pnlClass(
      drawdownPct === null ? null : -drawdownPct
    )}">${fmtPct(drawdownPct)}</span></div>
      <div class="kv"><span class="k">Delta total</span><span class="v">${fmtNum(
      delta,
      4
    )}</span></div>
      <div class="mt-3 space-y-2">
        <div>
          <div class="flex justify-between text-xs text-slate-400">
            <span>IM ratio</span><span class="font-mono">${fmtPct(imRatio, 2)}</span>
          </div>
          <div class="mini-bar"><span class="${imBarCls}" style="width:${(imPct * 100).toFixed(1)}%"></span></div>
        </div>
        <div>
          <div class="flex justify-between text-xs text-slate-400">
            <span>MM ratio</span><span class="font-mono">${fmtPct(mmRatio, 2)}</span>
          </div>
          <div class="mini-bar"><span class="${mmBarCls}" style="width:${(mmPct * 100).toFixed(1)}%"></span></div>
        </div>
      </div>
      ${haltReasons.length ? `<p class="mt-3 text-xs text-rose-300">${haltReasons.map(escapeHtml).join("<br>")}</p>` : ""}
    </div>
  `;
  }
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    })[c]);
  }
  function renderBookCards(status) {
    const root = document.getElementById("book-cards");
    if (!root) return;
    if (!status) {
      root.innerHTML = `
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm md:col-span-3">
        Need DERIBIT_CLIENT_ID/SECRET in <code>.env</code> to load live status.
        Read-only views (closed trades, cumulative PnL) still work below.
      </div>`;
      return;
    }
    const activeBooks = Object.keys(status?.portfolio?.equity_by_book || {}).map((book) => String(book).toUpperCase()).filter((book) => CORE_BOOKS.includes(book));
    const books = activeBooks.length ? activeBooks : CORE_BOOKS;
    const html = books.map((book) => bookCardHtml(book, status)).join("");
    root.innerHTML = html;
  }
  function renderAccountCards(health, status) {
    const root = document.getElementById("account-cards");
    if (!root) return;
    const configured = health?.accounts || status?.dashboard_accounts || [];
    const byName = new Map((status?.account_statuses || []).map((row) => [String(row.name || ""), row]));
    const accounts = configured.length ? configured : status?.account_statuses || [];
    if (!accounts.length) {
      root.innerHTML = `
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm md:col-span-3">
        No dashboard account metadata yet.
      </div>`;
      return;
    }
    root.innerHTML = accounts.map((account) => {
      const name = String(account.name || "");
      const row = byName.get(name) || account;
      const portfolio = row.portfolio || {};
      const totalEquity = num(portfolio.total_equity_usdc);
      const dayStart = num(portfolio.day_start_equity_usdc);
      const dayPnl = portfolioDayPnlUsdForDisplay(portfolio, totalEquity, dayStart);
      const regime = portfolio.regime || "\u2014";
      const openCount = num(row.trade_group_count);
      const credsOk = account.has_private_creds;
      const strategy = row.option_strategy || account.option_strategy || "";
      const env = row.env || account.env || "";
      const stateFile = account.state_file || row.state_file || "";
      const chips = [
        strategy ? strategyChipHtml(strategy) : "",
        credsOk === void 0 ? "" : `<span class="chip ${credsOk ? "chip-ok" : "chip-bad"}">creds ${credsOk ? "ok" : "missing"}</span>`
      ].filter(Boolean);
      return `
        <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 shadow">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h3 class="text-sm font-semibold tracking-wide text-slate-100">${escapeHtml(name || "account")}</h3>
              <p class="text-xs text-slate-500 mt-1 break-all">${escapeHtml(env)} \xB7 ${escapeHtml(stateFile)}</p>
            </div>
            <div class="flex flex-wrap justify-end gap-1 flex-shrink-0">${chips.join("")}</div>
          </div>
          <div class="stat-grid mt-4">
            <div class="stat-tile">
              <div class="label">Equity</div>
              <div class="value">${fmtUsd(totalEquity)}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Day P&amp;L</div>
              <div class="value ${pnlClass(dayPnl)}">${fmtUsd(dayPnl)}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Open groups</div>
              <div class="value">${openCount ?? "\u2014"}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Regime</div>
              <div class="value">${escapeHtml(regime)}</div>
            </div>
          </div>
        </div>
      `;
    }).join("");
  }
  function renderAggregate(status, report) {
    const root = document.getElementById("aggregate-card");
    if (!root) return;
    const { portfolio, source } = resolvedPortfolio();
    const summary = report?.summary;
    if (!portfolio && !summary) {
      if (INVESTOR && !STATE.investorReady) {
        root.innerHTML = aggregateSkeletonHtml();
      } else {
        root.innerHTML = `<p class="text-sm text-slate-400">${i18n(
          "No status / report data yet.",
          "\u5C1A\u7121\u5373\u6642\u5E33\u6236\u6216\u7E3E\u6548\u6458\u8981\u8CC7\u6599\u3002"
        )}</p>`;
      }
      return;
    }
    const totalEquity = num(portfolio?.total_equity_usdc);
    const dayStart = num(portfolio?.day_start_equity_usdc);
    const dayPnl = portfolioDayPnlUsdForDisplay(portfolio, totalEquity, dayStart);
    const dayDrawdown = num(portfolio?.day_drawdown_pct);
    const openRows = currentOpenRows(status, STATE.groups);
    const openCredit = openRows.reduce(
      (sum, g) => sum + (openRowEntryCreditUsd(g, status, STATE.groups) || 0),
      0
    );
    const creditByStrategy = sumOpenCreditByStrategy(openRows, status, STATE.groups);
    const lifetimePnl = num(summary?.realized_pnl_usdc);
    const lifetimeApr = num(summary?.lifetime_realized_apr);
    const winRate = num(summary?.realized_win_rate);
    const avgHolding = num(summary?.avg_holding_days);
    const closedCount = num(summary?.realized_closed_group_count);
    const windowDays = num(summary?.window_days_used);
    const windowPnl = num(summary?.window_realized_pnl_usdc);
    const windowApr = num(summary?.window_realized_apr);
    const lifetimeStartMs = lifetimePerformanceStartMs(report, STATE.groups);
    const lifetimeNativeByBook = sumLifetimeRealizedPnlNativeByBook(report, STATE.groups, status);
    const windowLabelDays = windowDays ?? 30;
    const windowNativeByBook = sumWindowRealizedPnlNativeByBook(
      report,
      STATE.groups,
      status,
      windowLabelDays
    );
    const equityNativeByBook = bookEquityNativeByBook(status);
    const sinceLine = lifetimeStartMs !== null ? `${i18n("since", "\u81EA")} ${fmtDate(lifetimeStartMs)}` : i18n("no realized history yet", "\u5C1A\u7121\u5DF2\u5BE6\u73FE\u7D00\u9304");
    const freshnessNote = source === "snapshot" && INVESTOR ? `<p class="text-xs text-amber-200/80 mt-3">${i18n(
      "Equity from last snapshot; live sync continues in background.",
      "\u6B0A\u76CA\u4F86\u81EA\u6700\u8FD1\u5FEB\u7167\uFF1B\u5373\u6642\u540C\u6B65\u65BC\u80CC\u666F\u9032\u884C\u4E2D\u3002"
    )}</p>` : source === "live" && INVESTOR ? `<p class="text-xs text-emerald-200/70 mt-3">${i18n("Live Deribit sync", "\u5DF2\u540C\u6B65 Deribit \u5373\u6642\u8CC7\u6599")}</p>` : "";
    const overviewCtx = {
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
      equityNativeByBook
    };
    const desktopOverview = overviewMetricsGridHtml(overviewCtx);
    if (INVESTOR) {
      root.innerHTML = `
      <div class="investor-view-desktop">${desktopOverview}</div>
      <div class="investor-view-mobile">${investorOverviewHtml(overviewCtx)}</div>
      ${freshnessNote}`;
    } else {
      root.innerHTML = `${desktopOverview}${freshnessNote}`;
    }
    renderDataFreshnessBadge();
  }
  function emptyStrategySummary(id) {
    return {
      id,
      openCount: 0,
      closedCount: 0,
      wins: 0,
      openEntryCredit: 0,
      unrealizedUsd: 0,
      realizedPnl: 0,
      annualizedSum: 0,
      annualizedCount: 0,
      annualizedWeightedSum: 0,
      annualizedWeight: 0,
      aprPnlUsdSum: 0,
      aprCapitalDays: 0,
      holdingSum: 0,
      holdingCount: 0,
      books: /* @__PURE__ */ new Set()
    };
  }
  function ensureStrategySummary(map, ids, id) {
    const key = id || "";
    ids.add(key);
    if (!map.has(key)) map.set(key, emptyStrategySummary(key));
    return map.get(key);
  }
  function closedAnnualizedCapitalDaysWeight(g, status, holding) {
    if (holding === null || holding <= 0) return null;
    const capital = tradeGroupAprCapitalBase(g, status);
    if (capital === null || capital <= 0) return null;
    const book = tradeGroupAprBook(g);
    if (book === "USDC") return capital * holding;
    const spot = num(status?.underlying_index_usd?.[book]) ?? num(STATE.lastSpotUsd?.[book]);
    if (spot === null || spot <= 0) return null;
    return capital * spot * holding;
  }
  function strategyAggregateRealizedApr(summary) {
    if (summary.aprCapitalDays > 0) {
      return summary.aprPnlUsdSum / summary.aprCapitalDays * 365;
    }
    return null;
  }
  function buildStrategySummaries(status, report, groups) {
    const ids = new Set(STRATEGIES.map((s) => s.id));
    const summaries = /* @__PURE__ */ new Map();
    for (const id of ids) summaries.set(id, emptyStrategySummary(id));
    const openRows = currentOpenRows(status, groups);
    for (const g of openRows) {
      const id = strategyId(g);
      if (!STRATEGY_BY_ID[id]) continue;
      const s = ensureStrategySummary(summaries, ids, id);
      s.openCount += 1;
      const credit = openRowEntryCreditUsd(g, status, groups);
      if (credit !== null) s.openEntryCredit += credit;
      const unrealized = openRowDisplayUnrealizedUsd(g, status, groups);
      if (unrealized !== null) s.unrealizedUsd += unrealized;
      const book = openRowBookCollateralUpper(g);
      if (book) s.books.add(book);
    }
    const closedRows = closedRowsForStrategyStats(report, groups);
    for (const g of closedRows) {
      const id = strategyId(g);
      if (!STRATEGY_BY_ID[id]) continue;
      const s = ensureStrategySummary(summaries, ids, id);
      s.closedCount += 1;
      const pnl = realizedPnlDisplayUsdc(g, status);
      if (pnl !== null) {
        s.realizedPnl += pnl;
        if (pnl > 0) s.wins += 1;
      }
      const holding = groupHoldingDays(g);
      if (holding !== null) {
        s.holdingSum += holding;
        s.holdingCount += 1;
      }
      const capital = tradeGroupAprCapitalBase(g, status);
      if (pnl !== null && capital !== null && capital > 0 && holding !== null && holding > 0) {
        const book2 = tradeGroupAprBook(g);
        let capitalUsd = capital;
        if (book2 === "BTC" || book2 === "ETH") {
          const spot = collateralBookSpotUsd(g, status);
          if (spot === null || spot <= 0) capitalUsd = null;
          else capitalUsd = capital * spot;
        }
        if (capitalUsd !== null) {
          s.aprPnlUsdSum += pnl;
          s.aprCapitalDays += capitalUsd * holding;
        }
      }
      const ann = groupRealizedApr(g, status);
      if (ann !== null) {
        s.annualizedSum += ann;
        s.annualizedCount += 1;
        const weight = closedAnnualizedCapitalDaysWeight(g, status, holding);
        if (weight !== null) {
          s.annualizedWeightedSum += ann * weight;
          s.annualizedWeight += weight;
        }
      }
      const book = String(g.collateral_currency || g.currency || "").toUpperCase();
      if (book) s.books.add(book);
    }
    const ordered = strategyOrder(ids);
    return ordered.map((id) => summaries.get(id) || emptyStrategySummary(id));
  }
  function strategySummaryCardHtml(summary) {
    const info = strategyInfo(summary.id);
    const winRate = summary.closedCount > 0 ? summary.wins / summary.closedCount : null;
    const weightedAnn = strategyAggregateRealizedApr(summary);
    const avgHolding = summary.holdingCount > 0 ? summary.holdingSum / summary.holdingCount : null;
    const books = Array.from(summary.books).sort().join(" / ") || "\u2014";
    return `
    <div class="rounded-2xl border ${info.accentClass} bg-slate-900/60 p-4 shadow">
      <div class="flex items-start justify-between gap-3 mb-2">
        <div>
          <h3 class="text-sm font-semibold tracking-wide text-slate-100">${escapeHtml(info.title)}</h3>
          <p class="text-xs text-slate-500 mt-1">${escapeHtml(info.description)}</p>
        </div>
        ${strategyChipHtml(summary.id)}
      </div>
      <div class="stat-grid mt-4">
        <div class="stat-tile">
          <div class="label">${i18n("Open groups", "\u6301\u5009\u7B46\u6578")}</div>
          <div class="value">${summary.openCount}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${i18n("Realized APR", "\u5DF2\u5BE6\u73FE\u5E74\u5316\uFF08\u52A0\u6B0A\uFF09")}</div>
          <div class="value">${fmtPct(weightedAnn, 1)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${i18n("Unrealized P&amp;L", "\u672A\u5BE6\u73FE\u640D\u76CA")}</div>
          <div class="value ${pnlClass(summary.unrealizedUsd)}">${fmtUsd(summary.unrealizedUsd)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${i18n("Realized P&amp;L", "\u5DF2\u5BE6\u73FE\u640D\u76CA")}</div>
          <div class="value ${pnlClass(summary.realizedPnl)}">${fmtUsd(summary.realizedPnl)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${i18n("Win rate", "\u52DD\u7387")}</div>
          <div class="value">${fmtPct(winRate, 1)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${i18n("Avg holding", "\u5E73\u5747\u6301\u6709")}</div>
          <div class="value">${avgHolding === null ? "\u2014" : fmtNum(avgHolding, 2) + (INVESTOR_ZH ? " \u5929" : "d")}</div>
        </div>
      </div>
      <div class="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <span>${summary.closedCount} ${i18n("closed \xB7 books", "\u7B46\u5DF2\u5E73 \xB7 \u5E33\u672C")} ${escapeHtml(books)}</span>
        <span>${i18n("weighted annualized", "\u52A0\u6B0A\u5E74\u5316")} ${fmtPct(weightedAnn, 1)}</span>
      </div>
    </div>
  `;
  }
  function openPositionStrategyClass(id) {
    const key = normalizeStrategyId(id);
    if (key === "covered_call") return "open-position-call";
    if (key === "bull_put_spread") return "open-position-spread";
    return "open-position-put";
  }
  function openPositionToneClass(value) {
    const n = num(value);
    if (n === null || Math.abs(n) < 5e-3) return "open-position-flat";
    return n > 0 ? "open-position-profit" : "open-position-loss";
  }
  function openPositionStatusLabel(value) {
    const n = num(value);
    if (n === null || Math.abs(n) < 5e-3) return i18n("Flat", "\u6301\u5E73");
    return n > 0 ? i18n("In profit", "\u6D6E\u76C8") : i18n("Underwater", "\u6D6E\u8667");
  }
  function creditCaptureBarHtml(value) {
    const pct = num(value);
    const width = pct === null ? 0 : Math.max(0, Math.min(100, pct * 100));
    const tone = pct === null ? "bar-muted" : pct >= 0.5 ? "bar-ok" : pct >= 0.15 ? "bar-warn" : "bar-bad";
    return `<span class="credit-capture-bar"><span class="${tone}" style="width:${width}%"></span></span>`;
  }
  function openPositionMetricHtml(label, valueHtml, extraClass = "", { secondary = false } = {}) {
    const secondaryClass = secondary ? " open-position-kpi-secondary" : "";
    return `
    <div class="open-position-metric${secondaryClass} ${extraClass}">
      <span class="open-position-label">${label}</span>
      <span class="open-position-value">${valueHtml}</span>
    </div>`;
  }
  function openPositionTitle(g) {
    const ccy = String(g.currency || "").toUpperCase() || "Option";
    const id = strategyId(g);
    if (id === "bull_put_spread") {
      return INVESTOR_ZH ? `${ccy} \u8CE3\u6B0A\u50F9\u5DEE` : `${ccy} put spread`;
    }
    const side = optionPutCallLabel(g);
    if (INVESTOR_ZH) {
      const sideZh = side.toLowerCase() === "call" ? "\u8CB7\u6B0A" : "\u8CE3\u6B0A";
      return `${ccy} \u8CE3\u51FA${sideZh}`;
    }
    return `${ccy} short ${side.toLowerCase()}`;
  }
  function openPositionLegCardHtml(g, status, groups, role) {
    const isShort = role === "short";
    const side = optionPutCallLabel(g);
    const title = isShort ? INVESTOR_ZH ? `\u8CE3\u51FA${side === "Call" ? "\u8CB7\u6B0A" : "\u8CE3\u6B0A"}` : `Short ${side}` : i18n("Long protection", "\u4FDD\u8B77\u8CB7\u817F");
    const instrument = openRowLegInstrumentName(g, role);
    const amount = openRowLegSignedSizeForDisplay(g, status, role);
    const strike = openRowLegStrike(g, role);
    const avg = openRowLegFieldValue(g, status, role, "average_price");
    const mark = openRowLegFieldValue(g, status, role, "mark_price");
    const legPnlUsd = openRowLegPnlUsd(status, g, groups, role);
    const coll = openRowBookCollateralUpper(g) || g.collateral_currency || "";
    const badgeClass = isShort ? "chip-warn" : "chip-ok";
    return `
    <div class="open-position-leg ${isShort ? "leg-short" : "leg-long"}">
      <div class="open-position-leg-head">
        <span class="chip ${badgeClass}">${title}</span>
        <span class="open-position-leg-amount">${amount === null ? "\u2014" : fmtNum(amount, 4)}</span>
      </div>
      <div class="open-position-leg-instrument">${escapeHtml(instrument || "\u2014")}</div>
      <div class="open-position-leg-metrics">
        ${openPositionMetricHtml(i18n("Strike", "\u5C65\u7D04\u50F9"), fmtStrike(strike))}
        ${openPositionMetricHtml(i18n("Entry", "\u9032\u5834\u50F9"), fmtDeribitPriceCell(avg, coll))}
        ${openPositionMetricHtml(i18n("Mark", "\u6A19\u8A18\u50F9"), fmtDeribitPriceCell(mark, coll))}
        ${openPositionMetricHtml(
      i18n("Leg PNL", "\u55AE\u817F\u640D\u76CA"),
      legPnlUsd === null ? "\u2014" : fmtUsd(legPnlUsd),
      pnlClass(legPnlUsd)
    )}
      </div>
    </div>`;
  }
  function openPositionDetailHtml(g, status, groups) {
    const id = strategyId(g);
    const coll = openRowBookCollateralUpper(g) || g.collateral_currency || "";
    if (id === "bull_put_spread") {
      const strikeWidth = bullPutSpreadWidth(g);
      const entryGap = openRowLegPriceGap(g, status, "average_price");
      const markGap = openRowLegPriceGap(g, status, "mark_price");
      return `
      <span>${i18n("Width", "\u50F9\u5DEE\u5BEC\u5EA6")} ${fmtStrike(strikeWidth)}</span>
      <span>${i18n("Entry gap", "\u9032\u5834\u50F9\u5DEE")} ${fmtDeribitPriceCell(entryGap, coll)}</span>
      <span>${i18n("Mark gap", "\u5E02\u50F9\u50F9\u5DEE")} ${fmtDeribitPriceCell(markGap, coll)}</span>`;
    }
    return `
    <span>${i18n("Strike", "\u5C65\u7D04\u50F9")} ${fmtStrike(openRowLegStrike(g, "short"))}</span>
    <span>${escapeHtml(strategyLegDetail(g))}</span>`;
  }
  function openPositionCardInvestorHtml(g, status, groups) {
    const id = strategyId(g);
    const isBullPutSpread = id === "bull_put_spread";
    const dteVal = openRowDteDays(g);
    const pnlUsd = openRowDisplayUnrealizedUsd(g, status, groups);
    const nativeUnr = openRowDisplayNativeUnrealizedValue(g, status, groups);
    const coll = openRowBookCollateralUpper(g) || g.collateral_currency || "";
    const creditKept = num(g.profit_capture);
    const entryCredit = openRowEntryCreditUsd(g, status, groups);
    const entryCreditNative = groupEntryCreditNative(g, status);
    const longLeg = openRowLegInstrumentName(g, "long");
    const shortAmt = openRowLegSignedSizeForDisplay(g, status, "short");
    const longAmt = openRowLegSignedSizeForDisplay(g, status, "long");
    const fmtLegAmount = (amt) => amt === null ? "" : ` \xB7 ${fmtNum(amt, 4)}`;
    const strategyClass = openPositionStrategyClass(id);
    const toneClass = openPositionToneClass(pnlUsd);
    const statusLabel = openPositionStatusLabel(pnlUsd);
    const entryUsd = entryCredit === null ? "\u2014" : fmtUsd(entryCredit);
    const entryNative = entryCreditNative === null ? "" : `<span class="inv-pos-metric-sub font-mono">${fmtNativeUnrealizedDisplay(entryCreditNative, coll)}</span>`;
    const entryApr = groupEntryNetApr(g, status);
    const entryAprText = entryApr === null ? "\u2014" : fmtPct(entryApr, 1);
    let detailTags = "";
    if (isBullPutSpread) {
      const strikeWidth = bullPutSpreadWidth(g);
      const entryGap = openRowLegPriceGap(g, status, "average_price");
      detailTags = `
      <span class="inv-pos-tag">${i18n("Width", "\u50F9\u5DEE")} ${fmtStrike(strikeWidth)}</span>
      <span class="inv-pos-tag">${i18n("Entry gap", "\u9032\u5834")} ${fmtDeribitPriceCell(entryGap, coll)}</span>`;
    } else {
      detailTags = `
      <span class="inv-pos-tag">${i18n("Strike", "\u5C65\u7D04")} ${fmtStrike(openRowLegStrike(g, "short"))}</span>
      <span class="inv-pos-tag">${escapeHtml(strategyLegDetail(g))}</span>`;
    }
    return `
    <article class="inv-position ${strategyClass} ${toneClass}">
      <header class="inv-position-head">
        <div class="inv-position-main">
          <div class="inv-position-titleline">
            ${strategyChipHtml(id, { compact: true })}
            <h3 class="inv-position-name">${escapeHtml(openPositionTitle(g))}</h3>
          </div>
          <p class="inv-position-contract font-mono">${escapeHtml(g.short_instrument_name || "\u2014")}<span class="inv-position-size tabular-nums">${fmtLegAmount(shortAmt)}</span></p>
          ${isBullPutSpread && longLeg ? `<p class="inv-position-contract font-mono inv-position-contract--long">${i18n("Long", "\u8CB7\u817F")} ${escapeHtml(longLeg)}<span class="inv-position-size tabular-nums">${fmtLegAmount(longAmt)}</span></p>` : ""}
          <div class="inv-position-tags">
            <span class="inv-pos-tag">${escapeHtml(coll)}</span>
            <span class="inv-pos-tag inv-pos-tag--status">${escapeHtml(statusLabel)}</span>
            ${detailTags}
          </div>
        </div>
        <div class="inv-position-pnl">
          <span class="inv-position-pnl-label">${i18n("Unrealized", "\u672A\u5BE6\u73FE")}</span>
          <span class="inv-position-pnl-value font-mono tabular-nums ${pnlClass(pnlUsd)}">${pnlUsd === null ? "\u2014" : fmtUsd(pnlUsd)}</span>
          <span class="inv-position-pnl-native font-mono tabular-nums ${pnlClass(nativeUnr)}">${fmtNativeUnrealizedDisplay(nativeUnr, coll)}</span>
        </div>
      </header>
      <div class="inv-position-strip" role="list">
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${i18n("DTE", "\u5230\u671F")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${dteVal !== null ? `${fmtNum(dteVal, 1)}${INVESTOR_ZH ? "\u5929" : "d"}` : "\u2014"}</span>
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${i18n("Credit kept", "\u6B0A\u5229\u91D1")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${fmtPct(creditKept, 1)}</span>
          ${creditCaptureBarHtml(creditKept)}
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${i18n("Entry", "\u9032\u5834")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${entryUsd}</span>
          ${entryNative}
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${i18n("Entry APR", "\u9032\u5834\u5E74\u5316")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums ${entryApr !== null && entryApr >= 0.15 ? "pnl-pos" : ""}">${entryAprText}</span>
        </div>
      </div>
    </article>`;
  }
  function openPositionCardDesktopHtml(g, status, groups) {
    const id = strategyId(g);
    const isBullPutSpread = id === "bull_put_spread";
    const dteVal = openRowDteDays(g);
    const pnlUsd = openRowDisplayUnrealizedUsd(g, status, groups);
    const nativeUnr = openRowDisplayNativeUnrealizedValue(g, status, groups);
    const coll = openRowBookCollateralUpper(g) || g.collateral_currency || "";
    const creditKept = num(g.profit_capture);
    const entryCredit = openRowEntryCreditUsd(g, status, groups);
    const entryCreditNative = groupEntryCreditNative(g, status);
    const entryFee = groupEntryFeeUsd(g);
    const entryFeeNative = groupEntryFeeNative(g, status);
    const closeFee = groupCloseFeeUsd(g);
    const closeFeeNative = groupCloseFeeNative(g, status);
    const longLeg = openRowLegInstrumentName(g, "long");
    const account = !INVESTOR && accountHint(g) ? accountHint(g) : "";
    const strategyClass = openPositionStrategyClass(id);
    const toneClass = openPositionToneClass(pnlUsd);
    const bookPill = INVESTOR ? i18n(`${coll} book`, `${coll} \u5E33\u672C`) : `${coll} book`;
    return `
    <article class="open-position-card ${strategyClass} ${toneClass}">
      <div class="open-position-glow"></div>
      <div class="open-position-header">
        <div class="open-position-main">
          <div class="open-position-title-row">
            ${strategyChipHtml(id)}
            <h3>${escapeHtml(openPositionTitle(g))}</h3>
            <span class="open-book-pill">${escapeHtml(bookPill)}</span>
            <span class="open-status-pill">${openPositionStatusLabel(pnlUsd)}</span>
          </div>
          <div class="open-position-instruments">
            <span>${escapeHtml(g.short_instrument_name || "\u2014")}</span>
            ${isBullPutSpread && longLeg ? `<span>${i18n("Long", "\u8CB7\u5165\u4FDD\u8B77")} ${escapeHtml(longLeg)}</span>` : ""}
          </div>
          <div class="open-position-detail-row">
            ${openPositionDetailHtml(g, status, groups)}
            ${account ? `<span>${escapeHtml(account)}</span>` : ""}
          </div>
        </div>
        <div class="open-position-pnl-panel">
          <span class="open-position-label"${isBullPutSpread ? ` title="${i18n(
      "Sum of leg mark MTM when both legs load; otherwise engine entry\u2212debit (bid/ask close est.).",
      "\u5169\u817F\u7686\u8F09\u5165\u6642\u70BA\u6A19\u8A18\u640D\u76CA\u52A0\u7E3D\uFF1B\u5426\u5247\u70BA\u5F15\u64CE\u9032\u5834\u6536\u6582\u8207\u73FE\u4F30\u5E73\u5009\u5DEE\u984D\u3002"
    )}"` : ""}>${i18n("Unrealized PNL", "\u672A\u5BE6\u73FE\u640D\u76CA")}</span>
          <strong class="${pnlClass(pnlUsd)}">${pnlUsd === null ? "\u2014" : fmtUsd(pnlUsd)}</strong>
          <span class="open-position-native ${pnlClass(nativeUnr)}">${fmtNativeUnrealizedDisplay(nativeUnr, coll)}</span>
        </div>
      </div>
      <div class="open-position-kpis open-position-kpis-extended">
        ${openPositionMetricHtml(
      i18n("DTE", "\u8DDD\u5230\u671F\u5929\u6578"),
      dteVal !== null ? `${fmtNum(dteVal, 2)}${INVESTOR_ZH ? " \u5929" : "d"}` : "\u2014"
    )}
        ${openPositionMetricHtml(
      i18n("Credit kept", "\u5DF2\u6536\u6B0A\u5229\u91D1\u6BD4\u4F8B"),
      `${fmtPct(creditKept, 1)}${creditCaptureBarHtml(creditKept)}`
    )}
        ${openPositionMetricHtml(
      i18n("Entry credit", "\u9032\u5834\u6536\u6582"),
      entryCredit === null ? "\u2014" : fmtUsdNativeBookStackHtml(entryCredit, entryCreditNative, coll)
    )}
        ${(() => {
      const entryApr = groupEntryNetApr(g, status);
      const aprClass = entryApr !== null && entryApr >= 0.15 ? "pnl-pos" : "";
      return openPositionMetricHtml(
        i18n("Entry net APR", "\u9032\u5834\u6DE8\u5E74\u5316"),
        entryApr === null ? "\u2014" : fmtPct(entryApr, 1),
        aprClass
      );
    })()}
        ${openPositionMetricHtml(
      i18n("Entry fee", "\u9032\u5834\u624B\u7E8C\u8CBB"),
      entryFee === null ? "\u2014" : fmtUsdNativeBookStackHtml(entryFee, entryFeeNative, coll)
    )}
        ${openPositionMetricHtml(
      i18n("Est. close fee", "\u9810\u4F30\u5E73\u5009\u8CBB"),
      closeFee === null ? "\u2014" : fmtUsdNativeBookStackHtml(closeFee, closeFeeNative, coll)
    )}
      </div>
      <div class="open-position-legs ${isBullPutSpread ? "has-two-legs" : "has-one-leg"}">
        ${openPositionLegCardHtml(g, status, groups, "short")}
        ${isBullPutSpread ? openPositionLegCardHtml(g, status, groups, "long") : ""}
      </div>
    </article>`;
  }
  function openPositionCardHtml(g, status, groups) {
    const desktop = openPositionCardDesktopHtml(g, status, groups);
    if (!INVESTOR) return desktop;
    return `<div class="investor-view-desktop">${desktop}</div><div class="investor-view-mobile">${openPositionCardInvestorHtml(g, status, groups)}</div>`;
  }
  function strategyOpenGroupHtml(id, rows, status, groups) {
    const normalizedId = normalizeStrategyId(id) || id;
    const info = strategyInfo(normalizedId);
    const cardsHtml = rows.map((g) => openPositionCardHtml(g, status, groups)).join("");
    return `
    <div class="rounded-2xl border ${info.accentClass} bg-slate-900/60 shadow overflow-hidden">
      <div class="flex flex-wrap items-baseline justify-between gap-3 px-4 py-3 border-b border-slate-800 bg-slate-950/40">
        <div class="flex flex-wrap items-center gap-2 min-w-0">
          <h3 class="text-sm font-semibold text-slate-200">${escapeHtml(info.title)}</h3>
          ${strategyChipHtml(normalizedId)}
        </div>
        <span class="text-xs text-slate-500">${rows.length} ${i18n("open", "\u7B46\u6301\u5009")}</span>
      </div>
      <div class="p-4">
        <div class="open-position-list">
          ${cardsHtml}
        </div>
      </div>
    </div>`;
  }
  function renderStrategyGroups(status, report, groups) {
    const cardsRoot = document.getElementById("strategy-cards");
    const openRoot = document.getElementById("strategy-open-groups");
    if (!cardsRoot && !openRoot) return;
    const summaries = buildStrategySummaries(status, report, groups);
    const openRows = currentOpenRows(status, groups);
    const totalOpen = openRows.length;
    const totalClosed = closedRowsForStrategyStats(report, groups).length;
    const activeStrategies = summaries.filter((s) => s.openCount || s.closedCount).length;
    setText(
      "strategy-meta",
      INVESTOR ? i18n(
        `${totalOpen} open \xB7 ${totalClosed} closed \xB7 ${activeStrategies || 0} active strategy groups`,
        `${totalOpen} \u7B46\u6301\u5009 \xB7 ${totalClosed} \u7B46\u5DF2\u5E73 \xB7 ${activeStrategies || 0} \u985E\u7B56\u7565`
      ) : `${totalOpen} open \xB7 ${totalClosed} closed \xB7 ${activeStrategies || 0} active strategy groups`
    );
    if (cardsRoot) {
      cardsRoot.innerHTML = summaries.map(strategySummaryCardHtml).join("");
    }
    if (!openRoot) return;
    if (!openRows.length) {
      openRoot.innerHTML = `
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-sm text-slate-400">
        ${i18n("No open strategy positions.", "\u76EE\u524D\u6C92\u6709\u958B\u5009\u4E2D\u7684\u7B56\u7565\u90E8\u4F4D\u3002")}
      </div>`;
      return;
    }
    const byStrategy = /* @__PURE__ */ new Map();
    const ids = new Set(STRATEGIES.map((s) => s.id));
    for (const g of openRows) {
      const id = strategyId(g);
      if (!STRATEGY_BY_ID[id]) continue;
      if (!byStrategy.has(id)) byStrategy.set(id, []);
      byStrategy.get(id).push(g);
    }
    openRoot.innerHTML = strategyOrder(ids).filter((id) => byStrategy.has(id)).map((id) => strategyOpenGroupHtml(id, byStrategy.get(id), status, groups)).join("");
  }
  function chartCommonOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        legend: {
          labels: { color: "rgb(203 213 225)", boxWidth: 12, padding: 8 }
        },
        tooltip: {
          backgroundColor: "rgba(15,23,42,0.95)",
          borderColor: "rgb(51,65,85)",
          borderWidth: 1,
          titleColor: "rgb(226,232,240)",
          bodyColor: "rgb(226,232,240)"
        }
      },
      scales: {
        x: {
          type: "time",
          time: { tooltipFormat: "yyyy-LL-dd HH:mm" },
          grid: { color: "rgba(51,65,85,0.4)" },
          ticks: { color: "rgb(148,163,184)" }
        },
        y: {
          grid: { color: "rgba(51,65,85,0.4)" },
          ticks: { color: "rgb(148,163,184)" }
        }
      }
    };
  }
  function destroyChart(key) {
    const chart = STATE.charts[key];
    if (!chart) return;
    const canvas = chart.canvas;
    chart.destroy();
    STATE.charts[key] = null;
    if (canvas) {
      canvas.removeAttribute("width");
      canvas.removeAttribute("height");
      canvas.style.width = "";
      canvas.style.height = "";
    }
  }
  function chartCanvasContext(canvasId) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    return canvas.getContext("2d");
  }
  function resizeAllCharts() {
    Object.values(STATE.charts).forEach((chart) => {
      try {
        chart?.resize?.();
      } catch (_) {
      }
    });
  }
  function scheduleChartResizeAll() {
    requestAnimationFrame(() => {
      resizeAllCharts();
      window.setTimeout(resizeAllCharts, 80);
      window.setTimeout(resizeAllCharts, 320);
    });
  }
  var chartResizeObserversAttached = false;
  function attachChartResizeObservers() {
    if (chartResizeObserversAttached || typeof ResizeObserver === "undefined") return;
    chartResizeObserversAttached = true;
    document.querySelectorAll(".chart-panel-canvas").forEach((shell) => {
      const canvas = shell.querySelector("canvas");
      if (!canvas?.id) return;
      new ResizeObserver(() => resizeAllCharts()).observe(shell);
    });
  }
  function aprEffectiveCapitalUsdc() {
    const eq = num(STATE.status?.portfolio?.total_equity_usdc);
    return eq !== null && eq > 0 ? eq : null;
  }
  function aprSeriesUrl() {
    let url = `/api/apr_series?window_days=${STATE.aprWindow}`;
    const cap = aprEffectiveCapitalUsdc();
    if (cap !== null) {
      url += `&effective_capital_usdc=${encodeURIComponent(String(cap))}`;
    }
    return url;
  }
  function defaultEmptyChartTimeBounds() {
    const end = luxon.DateTime.now().toUTC().startOf("day");
    const start = end.minus({ days: Math.max(STATE.aprWindow, 30) });
    return { min: start.toMillis(), max: end.toMillis() };
  }
  function chartPanelShell(canvasId) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    return canvas.closest(".chart-panel-canvas") || canvas.parentElement;
  }
  function setChartPanelEmpty(canvasId, { empty, message = "" } = {}) {
    const shell = chartPanelShell(canvasId);
    if (!shell) return;
    let overlay = shell.querySelector(".chart-empty-overlay");
    if (!empty) {
      overlay?.remove();
      shell.classList.remove("chart-panel-canvas--empty");
      return;
    }
    shell.classList.add("chart-panel-canvas--empty");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "chart-empty-overlay";
      shell.appendChild(overlay);
    }
    overlay.textContent = message;
  }
  var EMPTY_CHART_COPY = {
    realized: {
      en: "No closed positions yet \u2014 this chart fills in after the first close.",
      zh: "\u5C1A\u7121\u5E73\u5009\u7D00\u9304 \u2014 \u9996\u6B21\u5E73\u5009\u5F8C\u6B64\u5716\u8868\u624D\u6703\u958B\u59CB\u7D2F\u7A4D\u3002"
    },
    apr: {
      en: "Rolling APR needs closed trades and daily equity snapshots.",
      zh: "\u6EFE\u52D5\u5E74\u5316\u9700\u6709\u5E73\u5009\u7D00\u9304\u8207\u6BCF\u65E5\u6B0A\u76CA\u5FEB\u7167\u3002"
    }
  };
  function emptyChartMessage(kind) {
    const copy = EMPTY_CHART_COPY[kind] || EMPTY_CHART_COPY.realized;
    return i18n(copy.en, copy.zh);
  }
  function emptyChartScaleOptions({ yPercent = false, chartType = "line" } = {}) {
    const xBounds = defaultEmptyChartTimeBounds();
    const base = chartCommonOptions();
    const yMin = yPercent ? -0.1 : -50;
    const yMax = yPercent ? 0.1 : 50;
    return {
      ...base,
      plugins: {
        ...base.plugins,
        legend: { display: false },
        tooltip: { enabled: false }
      },
      scales: {
        x: {
          ...base.scales.x,
          ...xBounds,
          display: true,
          offset: chartType === "bar",
          time: {
            unit: "day",
            round: "day",
            tooltipFormat: "yyyy-LL-dd"
          }
        },
        y: {
          ...base.scales.y,
          display: true,
          min: yMin,
          max: yMax,
          ticks: {
            ...base.scales.y.ticks,
            maxTicksLimit: 6,
            ...yPercent ? { callback: (v) => fmtPct(v, 1) } : {}
          }
        }
      }
    };
  }
  function mountEmptyTimeSeriesChart(canvasId, key, { yPercent = false, chartType = "line", messageKind = "realized" } = {}) {
    const ctx = chartCanvasContext(canvasId);
    if (!ctx) return;
    destroyChart(key);
    setChartPanelEmpty(canvasId, {
      empty: true,
      message: emptyChartMessage(messageKind)
    });
    const xBounds = defaultEmptyChartTimeBounds();
    const placeholder = [
      { x: xBounds.min, y: 0 },
      { x: xBounds.max, y: 0 }
    ];
    STATE.charts[key] = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [
          {
            label: i18n("No realized history yet", "\u5C1A\u7121\u5DF2\u5BE6\u73FE\u7D00\u9304"),
            data: placeholder,
            borderWidth: 1,
            pointRadius: 0,
            borderColor: "rgba(148, 163, 184, 0.35)",
            backgroundColor: "transparent"
          }
        ]
      },
      options: emptyChartScaleOptions({ yPercent, chartType })
    });
  }
  function visibleBooks() {
    if (STATE.bookFilter === "ALL") return CORE_BOOKS;
    return [STATE.bookFilter];
  }
  function sumOpenCreditByStrategy(openRows, status, groups) {
    const out = Object.fromEntries(STRATEGIES.map((s) => [s.id, 0]));
    for (const g of openRows || []) {
      const id = strategyId(g);
      if (!STRATEGY_BY_ID[id]) continue;
      const credit = openRowEntryCreditUsd(g, status, groups);
      if (credit === null) continue;
      out[id] += credit;
    }
    return out;
  }
  function fmtOpenCreditStrategyBreakdown(byStrategy) {
    const rows = strategyOrder(new Set(STRATEGIES.map((s) => s.id))).map((id) => {
      const short = escapeHtml(strategyInfo(id).short);
      const n = num(byStrategy[id]);
      const text = n === null ? "\u2014" : fmtUsd(n);
      return `<div class="open-credit-row"><span class="open-credit-label text-slate-500">${short}</span><span class="open-credit-value font-mono tabular-nums text-slate-300">${text}</span></div>`;
    });
    return `<div class="open-credit-breakdown">${rows.join("")}</div>`;
  }
  function lifetimeRealizedClosedRows(report, groups, status = null) {
    const st = status ?? STATE.status;
    return dedupeTradeGroups([
      ...groups?.closed || [],
      ...report?.recent_closed_trades || []
    ]).filter((g) => isDisplayableClosedTradeGroup(g, st, groups)).filter((g) => num(g?.realized_pnl) !== null);
  }
  function sumLifetimeRealizedPnlNativeByBook(report, groups, status) {
    const out = { BTC: 0, ETH: 0, USDC: 0 };
    for (const g of lifetimeRealizedClosedRows(report, groups)) {
      const book = tradeGroupAprBook(g);
      if (book !== "BTC" && book !== "ETH" && book !== "USDC") continue;
      const native = realizedPnlInAprBookNative(g, status);
      if (native === null) continue;
      out[book] += native;
    }
    return out;
  }
  function sumWindowRealizedPnlNativeByBook(report, groups, status, windowDays) {
    const out = { BTC: 0, ETH: 0, USDC: 0 };
    const days = windowDays ?? 30;
    const cutoffMs = Date.now() - days * 24 * 3600 * 1e3;
    for (const g of lifetimeRealizedClosedRows(report, groups)) {
      const closedMs = closedTimestampMs(g);
      if (closedMs === null || closedMs < cutoffMs) continue;
      const book = tradeGroupAprBook(g);
      if (book !== "BTC" && book !== "ETH" && book !== "USDC") continue;
      const native = realizedPnlInAprBookNative(g, status);
      if (native === null) continue;
      out[book] += native;
    }
    return out;
  }
  function bookEquityNativeByBook(status) {
    const accounts = status?.accounts || {};
    const out = {};
    let any = false;
    for (const book of CORE_BOOKS) {
      out[book] = num(accounts[book]?.equity);
      if (out[book] !== null) any = true;
    }
    if (!any) {
      const { portfolio } = resolvedPortfolio();
      for (const book of CORE_BOOKS) {
        out[book] = num(portfolio?.equity_by_book?.[book]);
      }
    }
    return out;
  }
  function fmtNativeBookBreakdown(byBook, { places = { BTC: 5, ETH: 4, USDC: 2 }, pnl = false } = {}) {
    const symbols = { BTC: "\u20BF", ETH: "\u2666", USDC: "($)" };
    const items = ["BTC", "ETH", "USDC"].map((book) => {
      const n = num(byBook[book]);
      const text = n === null ? "\u2014" : fmtNum(n, places[book] ?? 4);
      const cls = pnl ? ` ${pnlClass(byBook[book])}` : "";
      return `<span class="native-book-item"><span class="native-book-symbol text-slate-500">${symbols[book]}</span> <span class="font-mono tabular-nums${cls}">${text}</span></span>`;
    });
    return `<span class="native-book-breakdown">${items.join("")}</span>`;
  }
  function fmtLifetimeRealizedNativeBreakdown(byBook) {
    return fmtNativeBookBreakdown(byBook, { pnl: true });
  }
  function fmtBookEquityNativeBreakdown(byBook) {
    return fmtNativeBookBreakdown(byBook);
  }
  function riskBarChartBaseOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          labels: { color: "rgb(203 213 225)", boxWidth: 12, padding: 8 }
        },
        tooltip: {
          backgroundColor: "rgba(15,23,42,0.95)",
          borderColor: "rgb(51,65,85)",
          borderWidth: 1,
          titleColor: "rgb(226,232,240)",
          bodyColor: "rgb(226,232,240)"
        }
      },
      scales: {
        x: {
          grid: { color: "rgba(51,65,85,0.4)" },
          ticks: { color: "rgb(148,163,184)" }
        },
        y: {
          beginAtZero: true,
          grid: { color: "rgba(51,65,85,0.4)" },
          ticks: {
            color: "rgb(148,163,184)",
            maxTicksLimit: 8
          }
        }
      }
    };
  }
  function renderBookEquityChart() {
    const ctx = chartCanvasContext("chart-risk-capital");
    if (!ctx) return;
    destroyChart("riskCapital");
    const books = visibleBooks();
    const portfolio = STATE.status?.portfolio;
    const equityBars = books.map((b) => {
      const v = bookEquityUsdForDisplay(b, STATE.status);
      return v !== null ? v : 0;
    });
    const totEq = num(portfolio?.total_equity_usdc);
    const sumBars = equityBars.reduce((a, b) => a + b, 0);
    let meta = i18n(`Total ${fmtUsd(totEq)}`, `\u5408\u8A08 ${fmtUsd(totEq)}`);
    if (totEq !== null && sumBars > 0 && Math.abs(sumBars - totEq) > 1) {
      meta += i18n(" \xB7 bars sum may differ from headline", " \xB7 \u5404\u5E33\u52A0\u7E3D\u53EF\u80FD\u8207\u7E3D\u89BD\u7565\u6709\u5DEE\u7570");
    } else if (!STATE.status) {
      meta = i18n("Awaiting live snapshot", "\u7B49\u5F85\u5373\u6642\u5FEB\u7167");
    }
    setText("risk-capital-meta", meta);
    setText(
      "risk-capital-hint",
      i18n(
        "Per-book equity in USDC equivalent from the live snapshot (or last saved snapshot).",
        "\u5404\u5E33\u672C\u6B0A\u76CA\u4EE5 USDC \u7D04\u7576\u986F\u793A\uFF0C\u4F86\u81EA\u5373\u6642\u6216\u6700\u8FD1\u5FEB\u7167\u3002"
      )
    );
    const barColors = books.map((b) => BOOK_COLORS[b] || "#94a3b8");
    const baseOpts = riskBarChartBaseOptions();
    setChartPanelEmpty("chart-risk-capital", { empty: false });
    STATE.charts.riskCapital = new Chart(ctx, {
      type: "bar",
      data: {
        labels: books,
        datasets: [
          {
            label: i18n("Book equity (USDC eq.)", "\u5E33\u672C\u6B0A\u76CA\uFF08USDC \u7D04\u7576\uFF09"),
            data: equityBars,
            backgroundColor: barColors.map((c) => c + "cc"),
            borderColor: barColors,
            borderWidth: 1
          }
        ]
      },
      options: {
        ...baseOpts,
        plugins: {
          ...baseOpts.plugins,
          tooltip: {
            ...baseOpts.plugins.tooltip,
            callbacks: {
              afterBody(items) {
                if (!items?.length) return "";
                const i = items[0].dataIndex;
                if (i === void 0) return "";
                const eq = equityBars[i] ?? 0;
                const share = totEq > 0 ? eq / totEq : null;
                const lines = [
                  `${i18n("Share of total: ", "\u4F54\u7E3D\u6B0A\u76CA\uFF1A")}${fmtPct(share, 2)}`
                ];
                return lines;
              }
            }
          }
        }
      }
    });
  }
  var MS_PER_DAY = 864e5;
  function dateToMs(dateStr) {
    const dt = luxon.DateTime.fromISO(String(dateStr || "").trim(), { zone: "utc" });
    if (!dt.isValid) return NaN;
    return dt.toMillis();
  }
  function finalizeCumulativeLineData(rawPoints) {
    const pts = rawPoints.filter((p) => Number.isFinite(p.x) && p.y !== null && Number.isFinite(p.y)).sort((a, b) => a.x - b.x);
    if (pts.length === 0) return [];
    if (pts.length === 1) {
      const p = pts[0];
      return [
        { x: p.x - MS_PER_DAY, y: 0 },
        { x: p.x, y: p.y },
        { x: p.x + MS_PER_DAY, y: p.y }
      ];
    }
    return pts;
  }
  function filterValidTimePoints(rawPoints) {
    return rawPoints.filter((p) => Number.isFinite(p.x) && p.y !== null && Number.isFinite(p.y)).sort((a, b) => a.x - b.x);
  }
  function finalizeSimpleLineData(rawPoints) {
    const pts = filterValidTimePoints(rawPoints);
    if (pts.length === 0) return [];
    if (pts.length === 1) {
      const p = pts[0];
      return [p, { x: p.x + MS_PER_DAY, y: p.y }];
    }
    return pts;
  }
  function suggestTimeScaleMinMax(flatPoints) {
    const xs = (flatPoints || []).map((p) => p.x).filter(Number.isFinite);
    if (!xs.length) return {};
    const lo = Math.min(...xs);
    const hi = Math.max(...xs);
    const span = hi - lo;
    const pad = MS_PER_DAY;
    if (xs.length === 1 || span < pad * 0.25) {
      return { min: lo - pad, max: hi + pad };
    }
    return {};
  }
  function renderCumulativePnlChart() {
    const ctx = chartCanvasContext("chart-cum-pnl");
    if (!ctx) return;
    destroyChart("cumPnl");
    const series = STATE.cumulativePnl;
    const closedMeta = series?.realized_count ? `${series.realized_count} closed groups` : i18n("no closed groups", "\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44");
    setText("cum-pnl-meta", closedMeta);
    if (!series) {
      mountEmptyTimeSeriesChart("chart-cum-pnl", "cumPnl");
      return;
    }
    const datasets = [];
    const books = visibleBooks();
    for (const book of books) {
      const rows = series.cumulative_by_book?.[book] || [];
      if (rows.length) {
        const data = finalizeCumulativeLineData(
          rows.map((r) => ({ x: dateToMs(r.date), y: num(r.pnl_usdc) }))
        );
        if (data.length) {
          datasets.push({
            label: `${book} cum. PnL`,
            data,
            borderColor: BOOK_COLORS[book],
            backgroundColor: BOOK_COLORS[book] + "22",
            stepped: true,
            pointRadius: 0,
            borderWidth: 2
          });
        }
      }
    }
    if (STATE.bookFilter === "ALL" && series.cumulative_total?.length) {
      const data = finalizeCumulativeLineData(
        series.cumulative_total.map((r) => ({
          x: dateToMs(r.date),
          y: num(r.pnl_usdc)
        }))
      );
      if (data.length) {
        datasets.push({
          label: "Total cum. PnL",
          data,
          borderColor: BOOK_COLORS.TOTAL,
          backgroundColor: BOOK_COLORS.TOTAL + "22",
          stepped: true,
          pointRadius: 0,
          borderWidth: 2,
          borderDash: [4, 4]
        });
      }
    }
    if (!datasets.length) {
      mountEmptyTimeSeriesChart("chart-cum-pnl", "cumPnl");
      return;
    }
    setChartPanelEmpty("chart-cum-pnl", { empty: false });
    STATE.charts.cumPnl = new Chart(ctx, {
      type: "line",
      data: { datasets },
      options: chartCommonOptions()
    });
  }
  function compactNonZeroDailyBars(points) {
    return points.filter((p) => Math.abs(p.y) > 1e-12);
  }
  var DAILY_PNL_PROFIT_FILL = "rgba(52, 211, 153, 0.67)";
  var DAILY_PNL_PROFIT_BORDER = "#34d399";
  var DAILY_PNL_LOSS_FILL = "rgba(251, 113, 133, 0.67)";
  var DAILY_PNL_LOSS_BORDER = "#fb7185";
  function dailyPnlBarFillColors(points) {
    return points.map((p) => {
      const y = num(p.y) ?? 0;
      if (y > 0) return DAILY_PNL_PROFIT_FILL;
      if (y < 0) return DAILY_PNL_LOSS_FILL;
      return "rgba(148, 163, 184, 0.4)";
    });
  }
  function dailyPnlBarBorderColors(points) {
    return points.map((p) => {
      const y = num(p.y) ?? 0;
      if (y > 0) return DAILY_PNL_PROFIT_BORDER;
      if (y < 0) return DAILY_PNL_LOSS_BORDER;
      return "#94a3b8";
    });
  }
  function renderDailyPnlChart() {
    const ctx = chartCanvasContext("chart-daily-pnl");
    if (!ctx) return;
    destroyChart("dailyPnl");
    const MA_WINDOW = 30;
    const series = STATE.cumulativePnl;
    if (!series) {
      setText("daily-pnl-meta", i18n("no closed groups", "\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44"));
      mountEmptyTimeSeriesChart("chart-daily-pnl", "dailyPnl", { chartType: "bar" });
      return;
    }
    const books = visibleBooks();
    const validDaily = (series.daily_total || []).filter((r) => Number.isFinite(dateToMs(r.date)));
    let meta = series?.daily_total?.length ? `${series.daily_total.length} ${i18n("active days", "\u500B\u6709\u6548\u4EA4\u6613\u65E5")}` : i18n("no closed groups", "\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44");
    if (STATE.bookFilter === "ALL" && validDaily.length >= MA_WINDOW) {
      meta += " \xB7 30d SMA";
    }
    setText("daily-pnl-meta", meta);
    const mapDay = (r) => ({ x: dateToMs(r.date), y: num(r.pnl_usdc) });
    let datasets = [];
    if (STATE.bookFilter === "ALL") {
      const barData = filterValidTimePoints((series.daily_total || []).map(mapDay));
      if (barData.length) {
        datasets.push({
          type: "bar",
          label: i18n("Daily total", "\u6BCF\u65E5\u5408\u8A08"),
          data: barData,
          order: 1,
          backgroundColor: dailyPnlBarFillColors(barData),
          borderColor: dailyPnlBarBorderColors(barData),
          borderWidth: 1
        });
      }
    } else {
      for (const book of books) {
        const rows = series.daily_by_book?.[book] || [];
        let barData = filterValidTimePoints(rows.map(mapDay));
        barData = compactNonZeroDailyBars(barData);
        if (barData.length) {
          datasets.push({
            type: "bar",
            label: `${book} ${i18n("daily", "\u6BCF\u65E5")}`,
            data: barData,
            order: 1,
            backgroundColor: dailyPnlBarFillColors(barData),
            borderColor: dailyPnlBarBorderColors(barData),
            borderWidth: 1
          });
        }
      }
    }
    if (STATE.bookFilter === "ALL" && validDaily.length >= MA_WINDOW) {
      const maPoints = [];
      for (let i = MA_WINDOW - 1; i < validDaily.length; i++) {
        let sum = 0;
        for (let j = i - MA_WINDOW + 1; j <= i; j++) {
          sum += num(validDaily[j].pnl_usdc) || 0;
        }
        maPoints.push({
          x: dateToMs(validDaily[i].date),
          y: sum / MA_WINDOW
        });
      }
      const maData = finalizeSimpleLineData(filterValidTimePoints(maPoints));
      if (maData.length) {
        datasets.push({
          type: "line",
          label: `30d SMA (${MA_WINDOW}-day realized avg.)`,
          data: maData,
          order: 2,
          borderColor: "#f472b6",
          backgroundColor: "#f472b633",
          tension: 0.15,
          pointRadius: 0,
          borderWidth: 2
        });
      }
    }
    if (!datasets.length) {
      mountEmptyTimeSeriesChart("chart-daily-pnl", "dailyPnl", { chartType: "bar" });
      return;
    }
    setChartPanelEmpty("chart-daily-pnl", { empty: false });
    const flatPoints = datasets.flatMap((d) => d.data || []);
    const xBounds = suggestTimeScaleMinMax(flatPoints);
    const base = chartCommonOptions();
    STATE.charts.dailyPnl = new Chart(ctx, {
      type: "bar",
      data: { datasets },
      options: {
        ...base,
        scales: {
          x: {
            ...base.scales.x,
            ...xBounds,
            offset: true,
            time: { unit: "day", tooltipFormat: "yyyy-LL-dd" }
          },
          y: {
            ...base.scales.y,
            ticks: {
              ...base.scales.y.ticks,
              maxTicksLimit: 10
            }
          }
        }
      }
    });
  }
  function renderAprChart() {
    const ctx = chartCanvasContext("chart-apr");
    if (!ctx) return;
    destroyChart("apr");
    const rows = STATE.aprSeries?.rows || [];
    const data = finalizeSimpleLineData(
      filterValidTimePoints(rows.map((r) => ({ x: dateToMs(r.date), y: num(r.apr) })))
    );
    if (!data.length) {
      mountEmptyTimeSeriesChart("chart-apr", "apr", { yPercent: true, messageKind: "apr" });
      return;
    }
    setChartPanelEmpty("chart-apr", { empty: false });
    const xBounds = suggestTimeScaleMinMax(data);
    const base = chartCommonOptions();
    STATE.charts.apr = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [
          {
            label: `Rolling APR (${STATE.aprWindow}d)`,
            data,
            borderColor: "#facc15",
            backgroundColor: "rgba(250,204,21,0.15)",
            tension: 0.25,
            pointRadius: 0,
            borderWidth: 2,
            fill: true
          }
        ]
      },
      options: {
        ...base,
        scales: {
          x: {
            ...base.scales.x,
            ...xBounds,
            time: { unit: "day", tooltipFormat: "yyyy-LL-dd" }
          },
          y: {
            ...base.scales.y,
            ticks: {
              ...base.scales.y.ticks,
              callback: (v) => fmtPct(v, 1)
            }
          }
        }
      }
    });
  }
  function renderRecentActivityList(root, rows, status, groups, emptyLabel) {
    if (!root) return;
    if (!rows.length) {
      root.innerHTML = `<li class="activity-empty">${escapeHtml(emptyLabel)}</li>`;
      return;
    }
    const html = [];
    for (const g of rows) {
      try {
        html.push(activityLifecycleCardHtml(g, status, groups));
      } catch (err) {
        console.warn("activity card skipped", g?.group_id, err);
      }
    }
    root.innerHTML = html.length ? html.join("") : `<li class="activity-empty">${escapeHtml(emptyLabel)}</li>`;
  }
  function renderRecentActivity(status, report, groups) {
    const openRoot = document.getElementById("activity-open-list");
    const closedRoot = document.getElementById("activity-closed-list");
    if (!openRoot && !closedRoot) return;
    const openAll = activityOpenRows(status, groups);
    const closedAll = activityClosedRows(status, report, groups);
    const openPage = paginateRows(openAll, STATE.activityOpenPage, ACTIVITY_PAGE_SIZE);
    const closedPage = paginateRows(closedAll, STATE.activityClosedPage, ACTIVITY_PAGE_SIZE);
    STATE.activityOpenPage = openPage.page;
    STATE.activityClosedPage = closedPage.page;
    setText(
      "activity-meta",
      i18n(
        `${openAll.length} open \xB7 ${closedAll.length} closed`,
        `${openAll.length} \u6301\u5009\u4E2D \xB7 ${closedAll.length} \u5DF2\u5E73\u5009`
      )
    );
    renderRecentActivityList(
      openRoot,
      openPage.rows,
      status,
      groups,
      i18n("No open positions", "\u5C1A\u7121\u6301\u5009")
    );
    renderRecentActivityList(
      closedRoot,
      closedPage.rows,
      status,
      groups,
      i18n("No closed trades", "\u5C1A\u7121\u5DF2\u5E73\u5009\u7D00\u9304")
    );
    const openPagRoot = document.getElementById("activity-open-pagination");
    const closedPagRoot = document.getElementById("activity-closed-pagination");
    if (openPagRoot) {
      openPagRoot.innerHTML = activityPaginationHtml("open", openPage);
      openPagRoot.hidden = !openPagRoot.innerHTML;
    }
    if (closedPagRoot) {
      closedPagRoot.innerHTML = activityPaginationHtml("closed", closedPage);
      closedPagRoot.hidden = !closedPagRoot.innerHTML;
    }
  }
  function closedTimestampMs(g) {
    const ms = num(g.closed_timestamp_ms);
    if (ms !== null) return ms;
    if (g.closed_timestamp) {
      const dt = luxon.DateTime.fromISO(String(g.closed_timestamp), { zone: "utc" });
      if (dt.isValid) return dt.toMillis();
    }
    return null;
  }
  function stressSections(stress) {
    const grouped = Array.isArray(stress?.strategy_stresses) ? stress.strategy_stresses.filter(Boolean) : [];
    return grouped.length ? grouped : [stress];
  }
  function renderStressSection(stress, sectionCount) {
    const equity = stress.equity_usdc_by_book || {};
    const analysis = stress.strategy_analysis || {};
    const strategy = normalizeStrategyId(stress.option_strategy || analysis.label || "naked_short");
    const totalEquity = Object.values(equity).reduce(
      (s, v) => s + (num(v) || 0),
      0
    );
    const accountNames = (stress.accounts || []).map((a) => a?.name).filter(Boolean).join(", ");
    const actions = Array.isArray(analysis.actions) ? analysis.actions : [];
    const equityRow = CORE_BOOKS.map(
      (b) => `
        <div class="rounded-xl bg-slate-800/40 px-3 py-2">
          <div class="text-[11px] text-slate-400 uppercase tracking-wide">${b} book</div>
          <div class="font-mono text-sm">${fmtUsd(equity[b])}</div>
        </div>`
    ).join("");
    const scenarioRows = (stress.scenarios || []).map((s) => {
      const total = num(s.loss_usdc_total);
      const pct = num(s.loss_usdc_pct_of_total_equity);
      const byBook = s.loss_by_book_usdc || {};
      return `
        <tr>
          <td class="px-3 py-2 font-mono">${fmtPct(num(s.shock), 0)}</td>
          <td class="px-3 py-2 font-mono">${fmtPct(num(s.slippage), 0)}</td>
          <td class="px-3 py-2 text-right font-mono ${pnlClass(total)}">${fmtUsd(total)}</td>
          <td class="px-3 py-2 text-right font-mono">${fmtPct(pct, 2)}</td>
          <td class="px-3 py-2 text-right font-mono ${pnlClass(num(byBook.BTC))}">${fmtUsd(byBook.BTC)}</td>
          <td class="px-3 py-2 text-right font-mono ${pnlClass(num(byBook.ETH))}">${fmtUsd(byBook.ETH)}</td>
          <td class="px-3 py-2 text-right font-mono ${pnlClass(num(byBook.USDC))}">${fmtUsd(byBook.USDC)}</td>
        </tr>`;
    }).join("");
    const actionList = actions.length ? `<ul class="mt-2 list-disc list-inside text-xs text-slate-500 space-y-1">
        ${actions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>` : "";
    return `
    <div class="${sectionCount > 1 ? "rounded-2xl border border-slate-800 bg-slate-900/40 p-4" : ""}">
      <div class="rounded-xl bg-slate-800/40 px-3 py-3 mb-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div class="text-[11px] text-slate-400 uppercase tracking-wide">Strategy black swan read</div>
            <div class="mt-1 flex items-center gap-2 text-sm text-slate-200">
              <span>${escapeHtml(strategyTitle(strategy))}</span>
              ${strategyChipHtml(strategy)}
            </div>
          </div>
          <div class="text-[11px] text-slate-500">
            ${escapeHtml(accountNames || `${stress.scenarios?.length || 0} scenarios \xB7 ${stress.positions?.length || 0} legs`)}
          </div>
        </div>
        <p class="mt-2 text-xs text-slate-400">${escapeHtml(analysis.summary || "")}</p>
        <p class="mt-1 text-xs text-slate-500">${escapeHtml(analysis.focus || "")}</p>
        ${actionList}
      </div>
      <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
        ${equityRow}
        <div class="rounded-xl bg-slate-800/40 px-3 py-2">
          <div class="text-[11px] text-slate-400 uppercase tracking-wide">Total equity (USDC)</div>
          <div class="font-mono text-sm">${fmtUsd(totalEquity)}</div>
        </div>
      </div>
      <div class="overflow-x-auto rounded-xl border border-slate-800">
        <table class="w-full text-sm">
          <thead class="bg-slate-900/80 text-slate-400">
            <tr>
              <th class="text-left px-3 py-2">Spot shock</th>
              <th class="text-left px-3 py-2">Slippage</th>
              <th class="text-right px-3 py-2">Total loss</th>
              <th class="text-right px-3 py-2">% of equity</th>
              <th class="text-right px-3 py-2">BTC book</th>
              <th class="text-right px-3 py-2">ETH book</th>
              <th class="text-right px-3 py-2">USDC book</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-800">
            ${scenarioRows || `<tr><td colspan="7" class="px-3 py-4 text-center text-slate-500">No stress scenarios.</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>
  `;
  }
  function renderStress(stress) {
    if (INVESTOR) return;
    const root = document.getElementById("stress-card");
    if (!root) return;
    if (!stress) {
      root.innerHTML = `<p class="text-sm text-slate-400">Set DERIBIT_CLIENT_ID and DERIBIT_CLIENT_SECRET to load live stress data.</p>`;
      setText("stress-meta", "\u2014");
      return;
    }
    const sections = stressSections(stress);
    const scenarioCount = sections.reduce((sum, item) => sum + (item.scenarios?.length || 0), 0);
    const legCount = sections.reduce((sum, item) => sum + (item.positions?.length || 0), 0);
    setText(
      "stress-meta",
      `${sections.length} strategy view${sections.length === 1 ? "" : "s"} \xB7 ${scenarioCount} scenarios \xB7 ${legCount} legs`
    );
    root.innerHTML = `
    <div class="space-y-4">
      ${sections.map((section) => renderStressSection(section, sections.length)).join("")}
    </div>
    <p class="text-xs text-slate-500 mt-3">
      Per-book loss is capped at that book's equity (liquidation-style floor). Spot shock is a negative index move.
      For bull put spread, long option legs are netted when present; for covered call, BTC/ETH spot cover drawdown is included.
    </p>
  `;
  }
  function updateHeaderSpotDom() {
    const elBtc = document.getElementById("header-spot-btc");
    const elEth = document.getElementById("header-spot-eth");
    const b = STATE.lastSpotUsd.BTC;
    const e = STATE.lastSpotUsd.ETH;
    if (elBtc) elBtc.textContent = b !== null && b > 0 ? `BTC ${fmt.usd2.format(b)}` : "BTC \u2014";
    if (elEth) elEth.textContent = e !== null && e > 0 ? `ETH ${fmt.usd2.format(e)}` : "ETH \u2014";
  }
  function renderPerformanceCharts() {
    const chartFns = [
      ["risk-capital", renderBookEquityChart],
      ["cum-pnl", renderCumulativePnlChart],
      ["daily-pnl", renderDailyPnlChart],
      ["apr", renderAprChart]
    ];
    for (const [name, fn] of chartFns) {
      try {
        fn();
      } catch (err) {
        console.error(`${name} chart render failed`, err);
      }
    }
    scheduleChartResizeAll();
  }
  function renderDashboard() {
    updateUnderlyingIndexCache(STATE.status, STATE.groups);
    renderRegime(STATE.status);
    renderTopBar(STATE.health);
    updateHeaderSpotDom();
    renderAccountCards(STATE.health, STATE.status);
    renderBookCards(STATE.status);
    renderAggregate(STATE.status, STATE.report);
    renderStrategyGroups(STATE.status, STATE.report, STATE.groups);
    renderPerformanceCharts();
    renderRecentActivity(STATE.status, STATE.report, STATE.groups);
    renderStress(STATE.stress);
  }
  var INVESTOR_LOAD_STEPS = {
    spot: {
      en: "Fetching BTC / ETH market prices\u2026",
      zh: "\u6B63\u5728\u53D6\u5F97 BTC / ETH \u5373\u6642\u5831\u50F9\u2026"
    },
    snapshot: {
      en: "Loading last equity snapshot\u2026",
      zh: "\u6B63\u5728\u8B80\u53D6\u6700\u8FD1\u6B0A\u76CA\u5FEB\u7167\u2026"
    },
    health: {
      en: "Checking account connection\u2026",
      zh: "\u6B63\u5728\u78BA\u8A8D\u5E33\u6236\u9023\u7DDA\u2026"
    },
    groups: {
      en: "Loading open positions and spreads\u2026",
      zh: "\u6B63\u5728\u8B80\u53D6\u6301\u5009\u8207\u50F9\u5DEE\u90E8\u4F4D\u2026"
    },
    cumulative: {
      en: "Loading realized P&L history\u2026",
      zh: "\u6B63\u5728\u8F09\u5165\u5DF2\u5BE6\u73FE\u640D\u76CA\u6B77\u53F2\u2026"
    },
    apr: {
      en: "Calculating rolling performance (APR)\u2026",
      zh: "\u6B63\u5728\u8A08\u7B97\u6EFE\u52D5\u5E74\u5316\u5831\u916C\u2026"
    },
    status: {
      en: "Syncing live equity and margin\u2026",
      zh: "\u6B63\u5728\u540C\u6B65\u5373\u6642\u6B0A\u76CA\u8207\u4FDD\u8B49\u91D1\u2026"
    },
    summary: {
      en: "Loading performance summary from local records\u2026",
      zh: "\u6B63\u5728\u5F9E\u672C\u5730\u7D00\u9304\u8F09\u5165\u7E3E\u6548\u6458\u8981\u2026"
    },
    render: {
      en: "Preparing your dashboard\u2026",
      zh: "\u6B63\u5728\u6574\u7406\u5100\u8868\u677F\u986F\u793A\u2026"
    },
    done: {
      en: "Done",
      zh: "\u5B8C\u6210"
    }
  };
  function investorLoadLabel(stepKey) {
    const s = INVESTOR_LOAD_STEPS[stepKey];
    return s ? i18n(s.en, s.zh) : "";
  }
  function investorLoadStepCount(hasPrivateCreds, { includeCharts = true } = {}) {
    let steps = 2 + 1 + (hasPrivateCreds ? 2 : 0) + 1;
    if (includeCharts) steps += 2;
    return steps;
  }
  function setInvestorLoadProgress(ratio, stepKey) {
    const pct = Math.min(100, Math.max(0, Math.round(ratio * 100)));
    const fill = document.getElementById("investor-load-bar-fill");
    if (fill) fill.style.width = `${pct}%`;
    const pctEl = document.querySelector("[data-investor-load-pct]");
    if (pctEl) pctEl.textContent = `${pct}%`;
    const stepEl = document.querySelector("[data-investor-load-step]");
    if (stepEl && stepKey) stepEl.textContent = investorLoadLabel(stepKey);
  }
  function applyInvestorLoadCopy() {
    if (!INVESTOR) return;
    const set = (attr, en, zh) => {
      const el = document.querySelector(`[data-investor-load-${attr}]`);
      if (el) el.textContent = i18n(en, zh);
    };
    set("eyebrow", "Please wait", "\u8ACB\u7A0D\u5019");
    set("title", "Loading your portfolio", "\u6B63\u5728\u8F09\u5165\u60A8\u7684\u6295\u8CC7\u7D44\u5408");
    set(
      "hint",
      "Showing snapshot first; live positions and P&L sync in the background.",
      "\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\uFF1B\u6301\u5009\u8207\u640D\u76CA\u65BC\u80CC\u666F\u540C\u6B65\u4E2D\u3002"
    );
  }
  function beginInvestorLoad({ blocking = true } = {}) {
    if (!INVESTOR) return;
    STATE.investorLoadDone = 0;
    STATE.investorLoadTotal = investorLoadStepCount(false);
    document.body.classList.toggle("investor-blocking-load", blocking);
    const overlay = document.getElementById("investor-load-overlay");
    if (overlay) {
      overlay.classList.remove("hidden");
      overlay.classList.toggle("investor-load-overlay--refresh", !blocking);
      overlay.setAttribute("aria-busy", "true");
    }
    const refreshBtn = document.getElementById("refresh-now");
    if (refreshBtn) refreshBtn.disabled = true;
    setInvestorLoadProgress(0, "spot");
  }
  function advanceInvestorLoad(stepKey) {
    if (!INVESTOR) return;
    STATE.investorLoadDone = Math.min(
      STATE.investorLoadTotal || 1,
      STATE.investorLoadDone + 1
    );
    const ratio = STATE.investorLoadTotal > 0 ? STATE.investorLoadDone / STATE.investorLoadTotal : 0;
    setInvestorLoadProgress(ratio, stepKey);
  }
  function setInvestorPageReady(ready) {
    if (!INVESTOR) return;
    if (!ready) {
      beginInvestorLoad({ blocking: !STATE.investorReady });
      return;
    }
    setInvestorLoadProgress(1, "done");
    STATE.investorReady = true;
    document.body.classList.remove("investor-blocking-load");
    document.body.classList.add("investor-ready");
    const overlay = document.getElementById("investor-load-overlay");
    if (overlay) {
      overlay.classList.add("hidden");
      overlay.classList.remove("investor-load-overlay--refresh");
      overlay.setAttribute("aria-busy", "false");
    }
    const refreshBtn = document.getElementById("refresh-now");
    if (refreshBtn) refreshBtn.disabled = false;
    scheduleChartResizeAll();
  }
  async function tickHeaderSpot({ renderDependentViews = true, updateDom = true } = {}) {
    try {
      const d = await fetchJson("/api/spot");
      STATE.lastSpotUsd.BTC = num(d.BTC);
      STATE.lastSpotUsd.ETH = num(d.ETH);
      if (updateDom) {
        updateHeaderSpotDom();
        if (renderDependentViews) {
          renderStrategyGroups(STATE.status, STATE.report, STATE.groups);
          renderRecentActivity(STATE.status, STATE.report, STATE.groups);
        }
      }
    } catch (_) {
    }
  }
  function chartsSectionOpen() {
    const el = document.getElementById("charts-section");
    return Boolean(el?.open);
  }
  async function fetchPortfolioSnapshot() {
    try {
      const d = await fetchJson("/api/portfolio/snapshot");
      STATE.portfolioSnapshot = d;
      if (d?.source === "ledger") {
        STATE.dataFreshness.source = "snapshot";
        STATE.dataFreshness.snapshotMs = num(d.freshness_ms);
        STATE.dataFreshness.live = false;
      }
    } catch (_) {
    }
  }
  async function fetchStatusWithTimeout() {
    const timeoutMs = INVESTOR_STATUS_TIMEOUT_MS;
    let timedOut = false;
    const timeoutPromise = delay(timeoutMs).then(() => {
      timedOut = true;
      throw new Error("status timeout");
    });
    try {
      const d = await Promise.race([fetchJson("/api/status"), timeoutPromise]);
      STATE.status = d;
      STATE.statusErrorOnce = false;
      STATE.dataFreshness.source = "live";
      STATE.dataFreshness.live = true;
      STATE.dataFreshness.statusMs = 0;
      return d;
    } catch (err) {
      if (timedOut && STATE.portfolioSnapshot?.portfolio) {
        if (!STATE.statusErrorOnce) {
          showToast(
            i18n(
              "Live sync is slow; showing last snapshot.",
              "\u5373\u6642\u540C\u6B65\u8F03\u6162\uFF0C\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\u3002"
            )
          );
          STATE.statusErrorOnce = true;
        }
        fetchJson("/api/status").then((d) => {
          STATE.status = d;
          STATE.dataFreshness.source = "live";
          STATE.dataFreshness.live = true;
          renderDashboard();
        }).catch(() => {
        });
        return null;
      }
      STATE.status = null;
      if (!STATE.statusErrorOnce) {
        showToast(`status: ${err.message}`);
        STATE.statusErrorOnce = true;
      }
      return null;
    }
  }
  async function fetchDashboardBundle({ backgroundOnTimeout = false } = {}) {
    const timeoutMs = INVESTOR_STATUS_TIMEOUT_MS;
    let timedOut = false;
    const bundleRequest = fetchJson(dashboardBundleUrl(30));
    const raced = INVESTOR ? Promise.race([
      bundleRequest,
      delay(timeoutMs).then(() => {
        timedOut = true;
        throw new Error("dashboard bundle timeout");
      })
    ]) : bundleRequest;
    try {
      applyDashboardBundlePayload(await raced);
      return true;
    } catch (err) {
      if (INVESTOR && timedOut && STATE.portfolioSnapshot?.portfolio) {
        if (!STATE.statusErrorOnce) {
          showToast(
            i18n(
              "Live sync is slow; showing last snapshot.",
              "\u5373\u6642\u540C\u6B65\u8F03\u6162\uFF0C\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\u3002"
            )
          );
          STATE.statusErrorOnce = true;
        }
        if (backgroundOnTimeout) {
          fetchJson(dashboardBundleUrl(30)).then((d) => {
            applyDashboardBundlePayload(d);
            renderDashboard();
          }).catch(() => {
          });
        }
        return false;
      }
      if (!INVESTOR || !timedOut) {
        showToast(`dashboard bundle: ${err.message}`);
      }
      return false;
    }
  }
  async function loadChartDataIfNeeded({ force = false, investorFetchWrap = null } = {}) {
    if (!force && STATE.chartsDataLoaded) {
      renderPerformanceCharts();
      return;
    }
    if (STATE.chartsLoadInFlight) return;
    STATE.chartsLoadInFlight = true;
    try {
      const fetchCumulative = () => fetchJson("/api/cumulative_pnl_series").then((d) => {
        STATE.cumulativePnl = d;
      }).catch((err) => showToast(`cumulative pnl: ${err.message}`));
      const fetchApr = () => fetchJson(aprSeriesUrl()).then((d) => {
        STATE.aprSeries = d;
      }).catch((err) => showToast(`apr series: ${err.message}`));
      if (investorFetchWrap) {
        await Promise.all([
          investorFetchWrap("cumulative", fetchCumulative),
          investorFetchWrap("apr", fetchApr)
        ]);
      } else {
        await Promise.all([fetchCumulative(), fetchApr()]);
      }
      STATE.chartsDataLoaded = true;
      renderPerformanceCharts();
    } finally {
      STATE.chartsLoadInFlight = false;
    }
  }
  function refreshWaitMs() {
    if (!STATE.lastRefreshStartedMs) return 0;
    return Math.max(0, FRONTEND_REFRESH_INTERVAL_MS - (Date.now() - STATE.lastRefreshStartedMs));
  }
  async function refreshAll({ force = false, silentIfLimited = false } = {}) {
    if (STATE.refreshInFlight) {
      if (!silentIfLimited) showToast(i18n("refresh already running", "\u5DF2\u6709\u66F4\u65B0\u6B63\u5728\u9032\u884C"));
      return;
    }
    const waitMs = refreshWaitMs();
    if (!force && waitMs > 0) {
      if (!silentIfLimited)
        showToast(
          i18n(
            `refresh rate limited; wait ${Math.ceil(waitMs / 1e3)}s`,
            `\u8ACB\u7A0D\u5019 ${Math.ceil(waitMs / 1e3)} \u79D2\u5F8C\u518D\u8A66`
          )
        );
      return;
    }
    STATE.refreshInFlight = true;
    STATE.lastRefreshStartedMs = Date.now();
    const investorFirstLoad = INVESTOR && !STATE.investorReady;
    if (investorFirstLoad) {
      beginInvestorLoad({ blocking: true });
    } else if (INVESTOR) {
      setInvestorProgressBar(true, { indeterminate: true });
    }
    try {
      let scheduleRender = function() {
        if (renderScheduled) return;
        renderScheduled = true;
        requestAnimationFrame(() => {
          renderScheduled = false;
          renderDashboard();
        });
      }, investorFetch = function(stepKey, run) {
        if (!investorFirstLoad) return run();
        return run().finally(() => advanceInvestorLoad(stepKey));
      }, fetchGroups = function() {
        return fetchJson("/api/groups").then((d) => {
          STATE.groups = d;
          scheduleRender();
        }).catch((err) => {
          showToast(`groups: ${err.message}`);
        });
      }, fetchSummary = function() {
        return fetchJson(realizedSummaryUrl(30)).then((d) => {
          STATE.report = d;
          scheduleRender();
        }).catch((err) => showToast(`realized summary: ${err.message}`));
      }, fetchStatusOp = function() {
        return fetchJson("/api/status").then((d) => {
          STATE.status = d;
          STATE.statusErrorOnce = false;
          scheduleRender();
        }).catch((err) => {
          STATE.status = null;
          if (!STATE.statusErrorOnce) {
            showToast(`status: ${err.message}`);
            STATE.statusErrorOnce = true;
          }
        });
      }, fetchStress = function() {
        return fetchJson("/api/stress?shocks=0.1,0.2,0.3,0.4,0.5").then((d) => {
          STATE.stress = d;
          scheduleRender();
        }).catch((err) => showToast(`stress: ${err.message}`));
      };
      let renderScheduled = false;
      try {
        const spotPromise = investorFetch(
          "spot",
          () => tickHeaderSpot({
            renderDependentViews: !INVESTOR,
            updateDom: true
          })
        );
        const healthPromise = investorFetch(
          "health",
          () => fetchJson("/api/health").then((d) => {
            STATE.health = d;
          })
        );
        await Promise.all([spotPromise, healthPromise]);
        renderTopBar(STATE.health);
      } catch (err) {
        showToast(`health failed: ${err.message}`);
      }
      const hasPrivateCreds = Boolean(STATE.health?.has_private_creds);
      let snapshotFetchedThisRefresh = false;
      if (INVESTOR && investorFirstLoad) {
        try {
          await Promise.race([
            investorFetch("snapshot", fetchPortfolioSnapshot),
            delay(INVESTOR_OVERLAY_MAX_MS)
          ]);
        } catch (_) {
        }
        snapshotFetchedThisRefresh = true;
        setInvestorPageReady(true);
        setInvestorProgressBar(true, { indeterminate: true });
        scheduleRender();
      }
      const investorFetchWrap = investorFirstLoad ? (stepKey, run) => investorFetch(stepKey, run) : null;
      const wrapStep = (stepKey, run) => investorFetchWrap ? investorFetchWrap(stepKey, run) : run();
      async function fetchPortfolioDataIndividual() {
        await wrapStep("groups", fetchGroups);
        if (INVESTOR) {
          await wrapStep("status", () => fetchStatusWithTimeout().then(() => scheduleRender()));
          await wrapStep("summary", fetchSummary);
        } else {
          await fetchStatusOp();
          await fetchSummary();
        }
      }
      async function fetchPortfolioData() {
        if (!hasPrivateCreds) {
          await wrapStep("groups", fetchGroups);
          STATE.status = null;
          STATE.report = null;
          STATE.stress = null;
          return;
        }
        if (USE_DASHBOARD_BUNDLE) {
          const ok = await fetchDashboardBundle({ backgroundOnTimeout: INVESTOR });
          if (ok) {
            if (investorFirstLoad) {
              advanceInvestorLoad("groups");
              advanceInvestorLoad("status");
              advanceInvestorLoad("summary");
            }
            scheduleRender();
            return;
          }
        }
        await fetchPortfolioDataIndividual();
      }
      const wave = [() => fetchPortfolioData()];
      if (hasPrivateCreds && !INVESTOR) {
        wave.push(() => fetchStress());
      }
      if (INVESTOR && !snapshotFetchedThisRefresh) {
        wave.push(() => wrapStep("snapshot", fetchPortfolioSnapshot));
      }
      const chartsNeeded = !INVESTOR || chartsSectionOpen();
      if (chartsNeeded) {
        wave.push(
          () => loadChartDataIfNeeded({
            force: !INVESTOR,
            investorFetchWrap
          })
        );
      }
      await promisePool(wave, FRONTEND_API_CONCURRENCY);
      if (INVESTOR) {
        STATE.stress = null;
      }
      if (!chartsNeeded) {
        renderBookEquityChart();
      }
      if (!INVESTOR || STATE.investorReady) {
        renderDashboard();
      }
      setInvestorProgressBar(false);
      setText(
        "last-refresh",
        `${i18n("last refresh:", "\u4E0A\u6B21\u66F4\u65B0\uFF1A")} ${luxon.DateTime.now().toFormat("HH:mm:ss")}`
      );
    } finally {
      STATE.refreshInFlight = false;
      setInvestorProgressBar(false);
    }
  }
  function setBookFilter(book) {
    STATE.bookFilter = book;
    const filterRoot = document.querySelector("#book-filter");
    if (filterRoot) {
      filterRoot.querySelectorAll("button[data-book]").forEach((btn) => {
        btn.classList.toggle("filter-active", btn.dataset.book === book);
      });
    }
    renderBookEquityChart();
    renderCumulativePnlChart();
    renderDailyPnlChart();
  }
  function attachAutoRefresh() {
    const checkbox = document.getElementById("auto-refresh");
    if (!checkbox) return;
    function reset() {
      if (STATE.autoRefreshHandle) {
        clearInterval(STATE.autoRefreshHandle);
        STATE.autoRefreshHandle = null;
      }
      if (checkbox.checked) {
        STATE.autoRefreshHandle = setInterval(
          () => refreshAll({ silentIfLimited: true }),
          FRONTEND_REFRESH_INTERVAL_MS
        );
      }
    }
    checkbox.addEventListener("change", reset);
    reset();
  }
  function attachControls() {
    document.getElementById("refresh-now")?.addEventListener("click", () => refreshAll());
    document.getElementById("book-filter")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-book]");
      if (btn) setBookFilter(btn.dataset.book);
    });
    document.getElementById("activity-section")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button.activity-page-btn");
      if (!btn || btn.disabled) return;
      const section = btn.dataset.activitySection;
      const dir = btn.dataset.direction === "next" ? 1 : -1;
      if (section === "open") STATE.activityOpenPage += dir;
      else if (section === "closed") STATE.activityClosedPage += dir;
      renderRecentActivity(STATE.status, STATE.report, STATE.groups);
    });
    document.getElementById("apr-window")?.addEventListener("change", async (e) => {
      STATE.aprWindow = parseInt(e.target.value, 10) || 30;
      try {
        STATE.aprSeries = await fetchJson(aprSeriesUrl());
      } catch (err) {
        showToast(`apr series: ${err.message}`);
      }
      renderAprChart();
    });
  }
  function attachExpandableSections() {
    document.querySelectorAll("details.collapsible-section").forEach((details) => {
      details.addEventListener("toggle", () => {
        if (!details.open) return;
        scheduleChartResizeAll();
        if (INVESTOR && details.id === "charts-section") {
          loadChartDataIfNeeded();
        }
      });
    });
  }
  function initDashboard() {
    document.addEventListener("DOMContentLoaded", () => {
      applyInvestorLoadCopy();
      attachChartResizeObservers();
      attachControls();
      attachExpandableSections();
      attachAutoRefresh();
      refreshAll({ force: true });
    });
  }

  // src/main.js
  initDashboard();
})();
