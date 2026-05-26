// Local dashboard logic.
// Hits the FastAPI endpoints exposed by deribit_demo/frontend_server.py and
// renders cards / tables / Chart.js charts. No build step.

(() => {
  const DASHBOARD_MODE =
    typeof window !== "undefined" && window.__DASHBOARD_MODE__ === "investor" ? "investor" : "ops";
  const INVESTOR = DASHBOARD_MODE === "investor";

  const INVESTOR_LOCALE = (() => {
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
  const INVESTOR_ZH = INVESTOR && INVESTOR_LOCALE === "zh";

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

  /** Prefix relative ``/api/...`` URLs when static HTML is hosted away from the FastAPI dashboard. */
  function resolveApiUrl(path) {
    if (/^https?:\/\//i.test(path)) return path;
    const fromWindow =
      typeof window !== "undefined" && window.__API_BASE__
        ? String(window.__API_BASE__).trim()
        : "";
    const base = (fromWindow || readApiBaseFromMeta()).replace(/\/$/, "");
    const p = path.startsWith("/") ? path : `/${path}`;
    return base ? `${base}${p}` : p;
  }

  const fmt = {
    usd0: new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    }),
    usd2: new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    }),
    num4: new Intl.NumberFormat("en-US", {
      maximumFractionDigits: 4,
    }),
    num8: new Intl.NumberFormat("en-US", {
      maximumFractionDigits: 8,
    }),
    pct2: new Intl.NumberFormat("en-US", {
      style: "percent",
      maximumFractionDigits: 2,
      minimumFractionDigits: 2,
    }),
    pct1: new Intl.NumberFormat("en-US", {
      style: "percent",
      maximumFractionDigits: 1,
      minimumFractionDigits: 1,
    }),
  };

  const BOOK_COLORS = {
    BTC: "#fb923c",
    ETH: "#818cf8",
    USDC: "#38bdf8",
    TOTAL: "#a3e635",
  };
  const CORE_BOOKS = ["BTC", "ETH", "USDC"];
  /** Auto-refresh cadence; longer interval reduces backend / Deribit fan-out under load. */
  const FRONTEND_REFRESH_INTERVAL_MS = 180_000;
  /** Max concurrent /api/* fetches per refresh wave (after spot + health). */
  const FRONTEND_API_CONCURRENCY = INVESTOR ? 6 : 3;
  /** One /api/dashboard_bundle replaces groups + status + summary when creds exist. */
  const USE_DASHBOARD_BUNDLE = true;
  const INVESTOR_STATUS_TIMEOUT_MS = 45_000;
  /** Full-screen overlay dismissed after health + snapshot (or this cap). */
  const INVESTOR_OVERLAY_MAX_MS = 3_000;
  const FETCH_JSON_RETRYABLE_STATUS = new Set([502, 503, 504]);
  const FETCH_JSON_MAX_RETRIES = 2;
  const FETCH_JSON_RETRY_BASE_MS = 450;
  const ACTIVITY_PAGE_SIZE = 10;

  const STRATEGIES = [
    {
      id: "covered_call",
      title: "Covered Call",
      titleZh: "備兌買權",
      short: "Covered Call",
      shortZh: "備兌",
      chipShort: "CC",
      chipShortZh: "備兌",
      accentClass: "strategy-card-call",
      description: "Short call backed by existing BTC/ETH spot collateral.",
      descriptionZh: "在持有現貨擔保下賣出買權，以權利金增強收益。",
    },
    {
      id: "naked_short",
      title: "Naked Short",
      titleZh: "單賣選擇權（裸賣）",
      short: "Naked Short",
      shortZh: "裸賣",
      chipShort: "Naked",
      chipShortZh: "裸賣",
      accentClass: "strategy-card-put",
      description: "Single-leg short option (put / call / both) with uncapped tail risk on the chosen side.",
      descriptionZh: "單邊賣出買／賣權；在對應方向具尾部風險，需嚴格風控。",
    },
    {
      id: "bull_put_spread",
      title: "Bull Put Spread",
      titleZh: "牛勢賣權價差",
      short: "Put Spread",
      shortZh: "賣權價差",
      chipShort: "Spread",
      chipShortZh: "價差",
      accentClass: "strategy-card-spread",
      description: "Short put paired with a lower-strike long put protection leg.",
      descriptionZh: "賣出較高履約價賣權，並買入較低履約價賣權作保護。",
    },
  ];

  const STRATEGY_BY_ID = Object.fromEntries(STRATEGIES.map((s) => [s.id, s]));

  const STATE = {
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
    /** Investor portal: first full refresh completed (enables content + ends overlay). */
    investorReady: false,
    investorLoadTotal: 0,
    investorLoadDone: 0,
    lastRefreshStartedMs: 0,
    statusErrorOnce: false,
    /** Last known positive BTC/ETH index (USD) for native unrealized fallback. */
    lastUnderlyingIndexUsd: {},
    /** Latest ``/api/spot`` (BTC/ETH USD index) for header + PNL USD fallback. */
    lastSpotUsd: { BTC: null, ETH: null },
    activityOpenPage: 1,
    activityClosedPage: 1,
  };

  // ---------- helpers ----------

  function num(value) {
    if (value === null || value === undefined || value === "") return null;
    const n = typeof value === "number" ? value : Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function fmtUsd(value, places = 2) {
    const n = num(value);
    if (n === null) return "—";
    return places === 0 ? fmt.usd0.format(n) : fmt.usd2.format(n);
  }

  function fmtPct(value, decimals = 2) {
    const n = num(value);
    if (n === null) return "—";
    return decimals === 1 ? fmt.pct1.format(n) : fmt.pct2.format(n);
  }

  function resolvedPortfolio() {
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

  function fmtFreshnessMinutes(ms) {
    const n = num(ms);
    if (n === null || n < 0) return null;
    const mins = Math.max(1, Math.round(n / 60_000));
    return mins;
  }

  function dataFreshnessBadgeHtml() {
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
      equityNativeByBook,
    } = ctx;
    return `
      <div class="overview-metrics-grid">
        <div class="overview-metric-cell">
          <div class="text-xs text-slate-400">${i18n("Total equity", "總權益（USDC 約當）")}</div>
          <div class="text-2xl font-mono">${fmtUsd(totalEquity)}</div>
          <div class="overview-metric-meta">
            <div class="overview-metric-line">${fmtBookEquityNativeBreakdown(equityNativeByBook)}</div>
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

  function investorNativeChipsHtml(byBook, { pnl = false, places = { BTC: 5, ETH: 4, USDC: 2 } } = {}) {
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

  function investorOpenCreditMiniHtml(byStrategy) {
    return strategyOrder(new Set(STRATEGIES.map((s) => s.id)))
      .map((id) => {
        const short = escapeHtml(strategyInfo(id).short);
        const n = num(byStrategy[id]);
        const text = n === null ? "—" : fmtUsd(n);
        return `<div class="inv-mini-row"><span class="inv-mini-label">${short}</span><span class="inv-mini-value font-mono tabular-nums">${text}</span></div>`;
      })
      .join("");
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
      equityNativeByBook,
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
            <span class="inv-kpi-foot">${i18n("day-start", "日初")} ${fmtUsd(dayStart)}</span>
          </div>
          <div class="inv-kpi">
            <span class="inv-kpi-label">${i18n("Day P&L", "本日損益")}</span>
            <span class="inv-kpi-value font-mono tabular-nums ${pnlClass(dayPnl)}">${fmtUsd(dayPnl)}</span>
            <span class="inv-kpi-foot">${i18n("drawdown", "回撤")} ${fmtPct(dayDrawdown)}</span>
          </div>
        </div>
        <div class="inv-chips-row">${investorNativeChipsHtml(equityNativeByBook)}</div>
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
  function fmtDeribitPriceCell(value, collateralCurrency) {
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
  function openRowPositionSignedSizeForDisplay(p) {
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
    if (q === null) return "—";
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

  /** Open groups on the same account sharing one exchange instrument (aggregated position). */
  function countOpenGroupsSharingLeg(status, groups, g, role) {
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
  function parseExpiryMsUtc(g) {
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

  /** Prefer ``/api/status`` (same refresh as portfolio), then ``/api/groups``, then cache. */
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

  /**
   * 帳本幣別（大寫）：持久化列有時缺 ``collateral_currency``，由代號推斷。
   * ``BTC_USDC-`` / ``ETH_USDC-`` 為 USDC 線性；``BTC-`` / ``ETH-`` 逆線為 BTC/ETH。
   */
  function openRowBookCollateralUpper(g) {
    let c = String(g.collateral_currency || "").toUpperCase();
    if (c === "BTC" || c === "ETH" || c === "USDC") return c;
    const n = String(g.short_instrument_name || "");
    if (n.includes("_USDC-")) return "USDC";
    if (n.startsWith("BTC-")) return "BTC";
    if (n.startsWith("ETH-")) return "ETH";
    return String(g.currency || "").toUpperCase() || "BTC";
  }

  /** Spot USD key for merged ``underlying_index_usd``（僅 BTC/ETH）。 */
  function underlyingIndexKeyForGroup(g) {
    const book = openRowBookCollateralUpper(g);
    if (book === "BTC" || book === "ETH") return book;
    return String(g.currency || "BTC").toUpperCase();
  }

  /** 同帳任選一筆部位列的 ``index_price``（逆線合併指數掛掉時的退路）。 */
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

  /**
   * Spot：當前 BTC／ETH 的 USD 指數（與後端 ``underlying_index_usd``／Deribit ``get_index_price`` 同源）。
   * 優先 merged 指數 → 該腿 ``index_price`` → 同帳其他列 ``index_price``。
   */
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

  /** USDC 帳本 spot 乘數為 1；逆線用 ``openRowSpotIndexUsdForPnl``。 */
  function openRowSpotUsdScalarForBook(g, status, groups) {
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
      if (v !== null && v !== undefined && v !== "") return v;
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
  function openRowUnrealizedUsdPreferDeribit(g, status, groups) {
    const calc = openRowPositionPnlUsd(status, g, groups);
    if (calc !== null) return calc;
    if (g.short_has_floating_profit_loss_usd) {
      const v = num(g.short_floating_profit_loss_usd);
      if (v !== null) return v;
    }
    return null;
  }

  /** Engine USDC unrealized: `entry_credit − current_debit`, 2dp + Deribit `($)` style. */
  function fmtUsdcUnrealizedDeribit(usdEstimate) {
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

  /** Bull put: leg PNL 加總（與卡片上兩腿一致）；兩腿缺一則 null，避免與單腿加 0 混淆。 */
  function openRowSpreadLegMtmUsdSumStrict(status, g, groups) {
    const shortPnl = openRowLegPnlUsd(status, g, groups, "short");
    const longPnl = openRowLegPnlUsd(status, g, groups, "long");
    if (shortPnl === null || longPnl === null) return null;
    return shortPnl + longPnl;
  }

  function openRowDisplayUnrealizedUsd(g, status, groups) {
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

  function openRowEntryCreditUsd(g, status, groups) {
    const credit = num(g.entry_credit);
    // Persisted TradeGroup.entry_credit is already USDC-equivalent for both
    // linear USDC and inverse BTC/ETH options.
    return credit;
  }

  /** PNL 欄：優先 Deribit ``floating_profit_loss``，否則 ``(mark−avg)×signed_size``（結算幣，不乘 spot）。 */
  function fmtUnrealizedCoinNativeDisplay(g, status) {
    const coll = openRowBookCollateralUpper(g);
    const mtm = openRowNativeUnrealizedDisplayValue(g, status);
    return fmtNativeUnrealizedDisplay(mtm, coll);
  }

  function fmtNativeUnrealizedDisplay(mtm, collateralCurrency) {
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
    if (n === null) return "—";
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

  // Match USDC equity / day-start: include spot MTM (and exclude external flows).
  // ``day_pnl_usdc_ex_flow_ex_spot`` is for native-book risk gates only.
  function portfolioDayPnlUsdForDisplay(portfolio, totalEquity, dayStart) {
    const netFlow = num(portfolio?.day_net_flow_usdc);
    return (
      num(portfolio?.day_pnl_usdc_ex_flow) ??
      num(portfolio?.day_pnl_usdc_ex_flow_ex_spot) ??
      (totalEquity !== null && dayStart !== null
        ? totalEquity - dayStart - (netFlow ?? 0)
        : null)
    );
  }

  function bookDayPnlUsdForDisplay(book, status, equityUsdc, dayStartUsdc) {
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

  function pnlClass(value) {
    const n = num(value);
    if (n === null || n === 0) return "";
    return n > 0 ? "pnl-pos" : "pnl-neg";
  }

  function fmtTime(msOrIso) {
    if (msOrIso === null || msOrIso === undefined) return "—";
    let dt;
    if (typeof msOrIso === "number") dt = luxon.DateTime.fromMillis(msOrIso, { zone: "utc" });
    else dt = luxon.DateTime.fromISO(String(msOrIso), { zone: "utc" });
    if (!dt.isValid) return "—";
    return dt.toLocal().toFormat("yyyy-LL-dd HH:mm");
  }

  function fmtDate(msOrIso) {
    if (msOrIso === null || msOrIso === undefined) return "—";
    let dt;
    if (typeof msOrIso === "number") dt = luxon.DateTime.fromMillis(msOrIso, { zone: "utc" });
    else dt = luxon.DateTime.fromISO(String(msOrIso), { zone: "utc" });
    if (!dt.isValid) return "—";
    return dt.toLocal().toFormat("yyyy-LL-dd");
  }

  /** Report window: rolling close filter + fixed N-day APR denominator (see realized_summary). */
  function rollingWindowProfitLabel(days) {
    const n = Math.round(days ?? 30);
    return i18n(`Total profit (rolling ${n}d)`, `已實現損益（滾動 ${n} 日視窗）`);
  }

  function rollingWindowAprLabel(days) {
    const n = Math.round(days ?? 30);
    return i18n(`Realized APR (rolling ${n}d)`, `已實現年化（滾動 ${n} 日視窗）`);
  }

  function rollingWindowPnlHint(days) {
    const n = Math.round(days ?? 30);
    return i18n(`Closes in last ${n}d only`, `僅計最近 ${n} 日內平倉`);
  }

  function rollingWindowAprHint(days) {
    const n = Math.round(days ?? 30);
    return i18n(
      `Last ${n}d closes ÷ ledger total equity`,
      `近 ${n} 日平倉 ÷ 當日總權益`
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

  /** Earliest entry among realized closed groups (lifetime APR sample start). */
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
      coveredcall: "covered_call",
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

  function hasTradeGroupValue(v) {
    if (v === null || v === undefined || v === "") return false;
    if (typeof v === "number" && !Number.isFinite(v)) return false;
    return true;
  }

  /** Prefer ``groups.closed`` (a) enrich fields over ``report`` rows (b) when both exist. */
  function mergeTradeGroupRow(a, b) {
    const out = { ...b, ...a };
    for (const key of TRADE_GROUP_ENRICH_KEYS) {
      if (hasTradeGroupValue(a[key])) out[key] = a[key];
      else if (hasTradeGroupValue(b[key])) out[key] = b[key];
    }
    return out;
  }

  function dedupeTradeGroups(rows) {
    const byKey = new Map();
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

  /** ``reconciled_external`` glitch: same short leg still open within 5 minutes of entry. */
  const PHANTOM_RECONCILE_MAX_HOLDING_MS = 300_000;

  function openShortInstrumentNames(status, groups) {
    const names = new Set();
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

  function mergedClosedRows(report, groups, limit = 20, status = null) {
    const st = status ?? STATE.status;
    const rows = dedupeTradeGroups([
      ...(groups?.closed || []),
      ...(report?.recent_closed_trades || []),
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
    if (v === null) return "—";
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
    return Math.max(closed - entry, 0) / 86_400_000;
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
    return (exp - entry) / 86_400_000;
  }

  function bookEquityNative(status, book) {
    const b = String(book || "USDC").toUpperCase();
    const eq = num(status?.accounts?.[b]?.equity);
    if (eq === null || eq <= 0) return null;
    return eq;
  }

  /** APR 分母帳本：逆線 BTC/ETH、線性 USDC（與 ``openRowBookCollateralUpper`` 一致，非標的 ``currency``）。 */
  function tradeGroupAprBook(g) {
    return openRowBookCollateralUpper(g);
  }

  function accountStatusRow(status, accountName) {
    const name = String(accountName || "").trim();
    if (!name) return null;
    return (status?.account_statuses || []).find((r) => String(r.name || "") === name) || null;
  }

  /**
   * 該策略子帳戶在對應帳本上的 equity（原生單位：BTC 為 BTC 數量、USDC 為美元）。
   * ``portfolio.equity_by_book`` 對 BTC/ETH 是 USDC 市值，不可作 APR 分母。
   */
  function strategyBookEquityNative(g, status) {
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

  function collateralBookSpotUsd(g, status) {
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

  /** 逆線：USDC 標記 = 幣本位 × 現價（不用平倉指數）；USDC 帳本直接用 stored USDC。 */
  function realizedPnlDisplayUsdc(g, status) {
    const book = tradeGroupAprBook(g);
    if (book === "USDC") return num(g?.realized_pnl);
    const native = realizedPnlCoinNative(g, status);
    const spot = collateralBookSpotUsd(g, status);
    if (native !== null && spot !== null && spot > 0) return native * spot;
    return null;
  }

  /** 已實現損益換成 APR 帳本原生單位（優先幣本位，legacy 才 ÷ 指數）。 */
  function realizedPnlInAprBookNative(g, status) {
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
    return (pnlN / capital) * (365 / holding);
  }

  function annualizedAprOnBookEquity(g, status, equityNative) {
    return annualizedAprOnPositionCapital(g, status);
  }

  /** 逆線：USDC 標記 = 幣本位 × 現價；USDC 帳本直接用 stored USDC。 */
  function fmtRealizedPnlDisplay(g, status) {
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

  function fmtNativeBookAmount(native, book) {
    const n = num(native);
    if (n === null) return `— ${book || ""}`.trim();
    const body = new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 }).format(n);
    return `${body} ${book}`;
  }

  function fmtUsdWithNativeBookAmount(usd, native, book) {
    const usdStr = fmtUsd(usd);
    if (native === null || !book || book === "USDC") return usdStr;
    const nativeStr = fmtNativeBookAmount(native, book);
    return INVESTOR_ZH ? `${usdStr}（${nativeStr}）` : `${usdStr} (${nativeStr})`;
  }

  /** Open-position KPI tiles: USDC on first row, native book amount on second. */
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
    return (
      num(g?.entry_index_usd) ??
      num(status?.underlying_index_usd?.[book]) ??
      num(STATE.groups?.underlying_index_usd?.[book]) ??
      num(STATE.lastSpotUsd?.[book])
    );
  }

  function closeIndexUsdForGroup(g, status) {
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
  function usdcLinearUnderlyingIndexUsd(g, status) {
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
      const tol = Math.max(0.01, Math.abs(gross) * 0.001);
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

  function groupEntryCreditUsd(g, status, groups) {
    return openRowEntryCreditUsd(g, status, groups);
  }

  function groupEntryCreditNative(g, status) {
    return nativeFromUsdAtIndex(num(g?.entry_credit), entryIndexUsdForGroup(g, status));
  }

  function allTradeGroupsForActivity(status, groups) {
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

  function activityOpenRows(status, groups) {
    return dedupeTradeGroups(allTradeGroupsForActivity(status, groups))
      .filter((g) => isOpenTradeGroup(g))
      .sort((a, b) => (entryTimestampMs(b) || 0) - (entryTimestampMs(a) || 0));
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
      end: Math.min(startIdx + pageSize, total),
    };
  }

  function activityPaginationHtml(section, pageInfo) {
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
    return parts
      .filter((p) => p)
      .map((p) => {
        if (typeof p === "string") return `<span>${escapeHtml(p)}</span>`;
        return `<span>${escapeHtml(p[0])} <strong>${escapeHtml(String(p[1]))}</strong></span>`;
      })
      .join("");
  }

  function activityLifecycleCardHtml(g, status, groups) {
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
    showToast._t = setTimeout(() => el.classList.add("hidden"), 5000);
  }

  function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /**
   * Bounded parallelism for refresh bursts — avoids stacking many heavy endpoints at once.
   */
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
      } catch (_) {}
      if (FETCH_JSON_RETRYABLE_STATUS.has(res.status) && attempt < maxAttempts - 1) {
        await delay(FETCH_JSON_RETRY_BASE_MS * (attempt + 1));
        continue;
      }
      throw new Error(detail);
    }
  }

  // ---------- top bar ----------

  function renderInvestorHeaderIdentity(health) {
    if (!INVESTOR || !health) return;
    const name = String(health.investor_display_name || health.investor_id || "").trim();
    const h1 = document.querySelector(".app-header h1");
    if (h1 && name) {
      h1.textContent = `${name} · ${INVESTOR_ZH ? "投資組合總覽" : "Investor summary"}`;
    }
    const sub = document.querySelector(".app-header h1 + p");
    if (!sub) return;
    if (!sub.dataset.investorBaseCopy) {
      sub.dataset.investorBaseCopy = sub.textContent || "";
    }
    const base = sub.dataset.investorBaseCopy;
    const investorId = String(health.investor_id || "").trim();
    sub.textContent =
      investorId && investorId !== name
        ? `${i18n("Investor id", "投資人 ID")}: ${investorId} · ${base}`
        : base;
  }

  /** Ops dashboard: rose on mainnet = live-funds warning. Investor portal: neutral tones (not an error). */
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
    return env === "mainnet"
      ? "border-rose-500/50 bg-rose-500/10 text-rose-200"
      : "border-emerald-500/50 bg-emerald-500/10 text-emerald-200";
  }

  function renderTopBar(health) {
    if (!health) return;
    renderInvestorHeaderIdentity(health);
    const env = (health.env || "").toLowerCase();
    const envBadge = document.getElementById("env-badge");
    if (envBadge) {
      envBadge.textContent = INVESTOR
        ? env === "mainnet"
          ? i18n("Network: Mainnet", "網路：主網")
          : env === "multi"
          ? i18n("Network: Multi-account", "網路：多帳戶")
          : env === "test"
          ? i18n("Network: Test", "網路：測試")
          : `${i18n("Network:", "網路：")} ${env || "—"}`
        : `env: ${env || "?"}`;
      envBadge.className =
        "text-xs px-2 py-0.5 rounded-full border " + envBadgeToneClass(env);
    }

    const strategyBadge = document.getElementById("strategy-badge");
    if (strategyBadge) {
      const strategy = normalizeStrategyId(health.option_strategy || "");
      const accountCount = health.accounts?.length || 0;
      strategyBadge.textContent = health.multi_account
        ? i18n(`strategy: multi (${accountCount} accounts)`, `策略：多帳戶（${accountCount}）`)
        : INVESTOR
        ? `${i18n("Strategy:", "策略：")} ${strategy ? strategyTitle(strategy) : "—"}`
        : `strategy: ${strategy ? strategyTitle(strategy) : "?"}`;
      strategyBadge.className =
        "text-xs px-2 py-0.5 rounded-full border border-sky-500/50 bg-sky-500/10 text-sky-200";
    }

    const credsBadge = document.getElementById("creds-badge");
    if (credsBadge) {
      credsBadge.textContent = health.has_private_creds
        ? "creds: ok"
        : "creds: missing";
      credsBadge.className =
        "text-xs px-2 py-0.5 rounded-full border " +
        (health.has_private_creds
          ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-200"
          : "border-rose-500/50 bg-rose-500/10 text-rose-200");
    }

    const sched = document.getElementById("scheduler-badge");
    if (sched) {
      if (health.scheduler_running) {
        const sec = health.snapshot_interval_sec || 300;
        const min = Math.round(sec / 60);
        sched.textContent = i18n(`scheduler: on (every ${min} min)`, `快照排程：每 ${min} 分鐘`);
        sched.className =
          "text-xs px-2 py-0.5 rounded-full border border-emerald-500/50 bg-emerald-500/10 text-emerald-200";
      } else {
        sched.textContent = i18n("scheduler: off", "快照排程：關閉");
        sched.className =
          "text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-700/30 text-slate-300";
      }
    }
    renderDataFreshnessBadge();
  }

  function renderRegime(status) {
    const badge = document.getElementById("regime-badge");
    if (!badge) return;
    const regime = status?.portfolio?.regime || "?";
    const regKey = String(regime).toLowerCase();
    const regZh = { normal: "正常", elevated: "偏高", crisis: "警戒" };
    const regEn = { normal: "Normal", elevated: "Elevated", crisis: "Crisis" };
    badge.textContent = INVESTOR
      ? `${i18n("Risk posture:", "風控狀態：")} ${INVESTOR_ZH ? regZh[regKey] || regime : regEn[regKey] || regime}`
      : `regime: ${regime}`;
    const cls =
      regime === "normal"
        ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-200"
        : regime === "elevated"
        ? "border-amber-500/50 bg-amber-500/10 text-amber-200"
        : regime === "crisis"
        ? "border-rose-500/50 bg-rose-500/10 text-rose-200"
        : "border-slate-600 bg-slate-700/30 text-slate-300";
    badge.className = `text-xs px-2 py-0.5 rounded-full border ${cls}`;
  }

  // ---------- book cards ----------

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

    const accentClass =
      book === "BTC"
        ? "book-card-btc"
        : book === "ETH"
        ? "book-card-eth"
        : "book-card-usdc";

    const chips = [];
    if (!isRiskBook) {
      chips.push('<span class="chip chip-muted">not traded</span>');
    }
    if (regime && isRiskBook) {
      const cls =
        regime === "normal" ? "chip-ok" : regime === "elevated" ? "chip-warn" : "chip-bad";
      chips.push(`<span class="chip ${cls}">${regime}</span>`);
    }
    if (cooling) chips.push('<span class="chip chip-warn">cooling</span>');
    if (hardDerisk) chips.push('<span class="chip chip-bad">hard derisk</span>');
    if (haltEntries) chips.push('<span class="chip chip-warn">halt entries</span>');
    if (chips.length === 0) chips.push('<span class="chip chip-ok">healthy</span>');

    const imPct = imRatio !== null ? Math.min(1, Math.max(0, imRatio)) : 0;
    const imBarCls = imRatio === null
      ? "bar-ok"
      : imRatio >= 0.45
      ? "bar-bad"
      : imRatio >= 0.35
      ? "bar-warn"
      : "bar-ok";
    const mmPct = mmRatio !== null ? Math.min(1, Math.max(0, mmRatio)) : 0;
    const mmBarCls = mmRatio === null
      ? "bar-ok"
      : mmRatio >= 0.33
      ? "bar-bad"
      : mmRatio >= 0.22
      ? "bar-warn"
      : "bar-ok";

    return `
      <div class="rounded-2xl border ${accentClass} bg-slate-900/60 p-4 shadow">
        <div class="flex items-center justify-between mb-2">
          <h3 class="text-sm font-semibold tracking-wide text-slate-200">${book} BOOK</h3>
          <div class="flex flex-wrap gap-1">${chips.join("")}</div>
        </div>
        <div class="text-2xl font-mono">${fmtUsd(equityUsdc)}</div>
        <div class="text-xs text-slate-500 mb-3">
          ${equityNative !== null ? fmtNum(equityNative, 8) + " " + book : ""}
          ${dayStartUsdc !== null ? "· day-start " + fmtUsd(dayStartUsdc) : ""}
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
            <div class="mini-bar"><span class="${imBarCls}" style="width:${(
      imPct * 100
    ).toFixed(1)}%"></span></div>
          </div>
          <div>
            <div class="flex justify-between text-xs text-slate-400">
              <span>MM ratio</span><span class="font-mono">${fmtPct(mmRatio, 2)}</span>
            </div>
            <div class="mini-bar"><span class="${mmBarCls}" style="width:${(
      mmPct * 100
    ).toFixed(1)}%"></span></div>
          </div>
        </div>
        ${
          haltReasons.length
            ? `<p class="mt-3 text-xs text-rose-300">${haltReasons
                .map(escapeHtml)
                .join("<br>")}</p>`
            : ""
        }
      </div>
    `;
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]));
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
    const activeBooks = Object.keys(status?.portfolio?.equity_by_book || {})
      .map((book) => String(book).toUpperCase())
      .filter((book) => CORE_BOOKS.includes(book));
    const books = activeBooks.length ? activeBooks : CORE_BOOKS;
    const html = books
      .map((book) => bookCardHtml(book, status))
      .join("");
    root.innerHTML = html;
  }

  function renderAccountCards(health, status) {
    const root = document.getElementById("account-cards");
    if (!root) return;
    const configured = health?.accounts || status?.dashboard_accounts || [];
    const byName = new Map((status?.account_statuses || []).map((row) => [String(row.name || ""), row]));
    const accounts = configured.length ? configured : (status?.account_statuses || []);
    if (!accounts.length) {
      root.innerHTML = `
        <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm md:col-span-3">
          No dashboard account metadata yet.
        </div>`;
      return;
    }
    root.innerHTML = accounts
      .map((account) => {
        const name = String(account.name || "");
        const row = byName.get(name) || account;
        const portfolio = row.portfolio || {};
        const totalEquity = num(portfolio.total_equity_usdc);
        const dayStart = num(portfolio.day_start_equity_usdc);
        const dayPnl = portfolioDayPnlUsdForDisplay(portfolio, totalEquity, dayStart);
        const regime = portfolio.regime || "—";
        const openCount = num(row.trade_group_count);
        const credsOk = account.has_private_creds;
        const strategy = row.option_strategy || account.option_strategy || "";
        const env = row.env || account.env || "";
        const stateFile = account.state_file || row.state_file || "";
        const chips = [
          strategy ? strategyChipHtml(strategy) : "",
          credsOk === undefined
            ? ""
            : `<span class="chip ${credsOk ? "chip-ok" : "chip-bad"}">creds ${credsOk ? "ok" : "missing"}</span>`,
        ].filter(Boolean);
        return `
          <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 shadow">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <h3 class="text-sm font-semibold tracking-wide text-slate-100">${escapeHtml(name || "account")}</h3>
                <p class="text-xs text-slate-500 mt-1 break-all">${escapeHtml(env)} · ${escapeHtml(stateFile)}</p>
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
                <div class="value">${openCount ?? "—"}</div>
              </div>
              <div class="stat-tile">
                <div class="label">Regime</div>
                <div class="value">${escapeHtml(regime)}</div>
              </div>
            </div>
          </div>
        `;
      })
      .join("");
  }

  // ---------- aggregate card ----------

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
          "尚無即時帳戶或績效摘要資料。"
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
    const sinceLine =
      lifetimeStartMs !== null
        ? `${i18n("since", "自")} ${fmtDate(lifetimeStartMs)}`
        : i18n("no realized history yet", "尚無已實現紀錄");
    const freshnessNote =
      source === "snapshot" && INVESTOR
        ? `<p class="text-xs text-amber-200/80 mt-3">${i18n(
            "Equity from last snapshot; live sync continues in background.",
            "權益來自最近快照；即時同步於背景進行中。"
          )}</p>`
        : source === "live" && INVESTOR
        ? `<p class="text-xs text-emerald-200/70 mt-3">${i18n("Live Deribit sync", "已同步 Deribit 即時資料")}</p>`
        : "";

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
      equityNativeByBook,
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

  // ---------- strategy groups ----------

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
      books: new Set(),
    };
  }

  function ensureStrategySummary(map, ids, id) {
    const key = id || "";
    ids.add(key);
    if (!map.has(key)) map.set(key, emptyStrategySummary(key));
    return map.get(key);
  }

  function closedBookEquityUsd(status, book) {
    const b = String(book || "USDC").toUpperCase();
    const fromPortfolio = num(status?.portfolio?.equity_by_book?.[b]);
    if (fromPortfolio !== null && fromPortfolio > 0) return fromPortfolio;
    const native = closedBookTotalEquityNative(status, b);
    if (native === null) return null;
    if (b === "USDC") return native;
    const spot = num(status?.underlying_index_usd?.[b]) ?? num(STATE.lastSpotUsd?.[b]);
    if (spot === null || spot <= 0) return null;
    return native * spot;
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

  /** Strategy-level realized APR: total P&L / sum(position capital × holding days) × 365. */
  function strategyAggregateRealizedApr(summary) {
    if (summary.aprCapitalDays > 0) {
      return (summary.aprPnlUsdSum / summary.aprCapitalDays) * 365;
    }
    return null;
  }

  function buildStrategySummaries(status, report, groups) {
    const ids = new Set(STRATEGIES.map((s) => s.id));
    const summaries = new Map();
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
        const book = tradeGroupAprBook(g);
        let capitalUsd = capital;
        if (book === "BTC" || book === "ETH") {
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
    const avgHolding =
      summary.holdingCount > 0 ? summary.holdingSum / summary.holdingCount : null;
    const books = Array.from(summary.books).sort().join(" / ") || "—";

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
            <div class="label">${i18n("Open groups", "持倉筆數")}</div>
            <div class="value">${summary.openCount}</div>
          </div>
          <div class="stat-tile">
            <div class="label">${i18n("Realized APR", "已實現年化（加權）")}</div>
            <div class="value">${fmtPct(weightedAnn, 1)}</div>
          </div>
          <div class="stat-tile">
            <div class="label">${i18n("Unrealized P&amp;L", "未實現損益")}</div>
            <div class="value ${pnlClass(summary.unrealizedUsd)}">${fmtUsd(summary.unrealizedUsd)}</div>
          </div>
          <div class="stat-tile">
            <div class="label">${i18n("Realized P&amp;L", "已實現損益")}</div>
            <div class="value ${pnlClass(summary.realizedPnl)}">${fmtUsd(summary.realizedPnl)}</div>
          </div>
          <div class="stat-tile">
            <div class="label">${i18n("Win rate", "勝率")}</div>
            <div class="value">${fmtPct(winRate, 1)}</div>
          </div>
          <div class="stat-tile">
            <div class="label">${i18n("Avg holding", "平均持有")}</div>
            <div class="value">${avgHolding === null ? "—" : fmtNum(avgHolding, 2) + (INVESTOR_ZH ? " 天" : "d")}</div>
          </div>
        </div>
        <div class="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
          <span>${summary.closedCount} ${i18n("closed · books", "筆已平 · 帳本")} ${escapeHtml(books)}</span>
          <span>${i18n("weighted annualized", "加權年化")} ${fmtPct(weightedAnn, 1)}</span>
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
    if (n === null || Math.abs(n) < 0.005) return "open-position-flat";
    return n > 0 ? "open-position-profit" : "open-position-loss";
  }

  function openPositionStatusLabel(value) {
    const n = num(value);
    if (n === null || Math.abs(n) < 0.005) return i18n("Flat", "持平");
    return n > 0 ? i18n("In profit", "浮盈") : i18n("Underwater", "浮虧");
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
      return INVESTOR_ZH ? `${ccy} 賣權價差` : `${ccy} put spread`;
    }
    const side = optionPutCallLabel(g);
    if (INVESTOR_ZH) {
      const sideZh = side.toLowerCase() === "call" ? "買權" : "賣權";
      return `${ccy} 賣出${sideZh}`;
    }
    return `${ccy} short ${side.toLowerCase()}`;
  }

  function openPositionLegCardHtml(g, status, groups, role) {
    const isShort = role === "short";
    const side = optionPutCallLabel(g);
    const title = isShort
      ? INVESTOR_ZH
        ? `賣出${side === "Call" ? "買權" : "賣權"}`
        : `Short ${side}`
      : i18n("Long protection", "保護買腿");
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
          <span class="open-position-leg-amount">${amount === null ? "—" : fmtNum(amount, 4)}</span>
        </div>
        <div class="open-position-leg-instrument">${escapeHtml(instrument || "—")}</div>
        <div class="open-position-leg-metrics">
          ${openPositionMetricHtml(i18n("Strike", "履約價"), fmtStrike(strike))}
          ${openPositionMetricHtml(i18n("Entry", "進場價"), fmtDeribitPriceCell(avg, coll))}
          ${openPositionMetricHtml(i18n("Mark", "標記價"), fmtDeribitPriceCell(mark, coll))}
          ${openPositionMetricHtml(
            i18n("Leg PNL", "單腿損益"),
            legPnlUsd === null ? "—" : fmtUsd(legPnlUsd),
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
        <span>${i18n("Width", "價差寬度")} ${fmtStrike(strikeWidth)}</span>
        <span>${i18n("Entry gap", "進場價差")} ${fmtDeribitPriceCell(entryGap, coll)}</span>
        <span>${i18n("Mark gap", "市價價差")} ${fmtDeribitPriceCell(markGap, coll)}</span>`;
    }
    return `
      <span>${i18n("Strike", "履約價")} ${fmtStrike(openRowLegStrike(g, "short"))}</span>
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
    const fmtLegAmount = (amt) => (amt === null ? "" : ` · ${fmtNum(amt, 4)}`);
    const strategyClass = openPositionStrategyClass(id);
    const toneClass = openPositionToneClass(pnlUsd);
    const statusLabel = openPositionStatusLabel(pnlUsd);
    const entryUsd =
      entryCredit === null ? "—" : fmtUsd(entryCredit);
    const entryNative =
      entryCreditNative === null
        ? ""
        : `<span class="inv-pos-metric-sub font-mono">${fmtNativeUnrealizedDisplay(entryCreditNative, coll)}</span>`;
    const entryApr = groupEntryNetApr(g, status);
    const entryAprText = entryApr === null ? "—" : fmtPct(entryApr, 1);
    let detailTags = "";
    if (isBullPutSpread) {
      const strikeWidth = bullPutSpreadWidth(g);
      const entryGap = openRowLegPriceGap(g, status, "average_price");
      detailTags = `
        <span class="inv-pos-tag">${i18n("Width", "價差")} ${fmtStrike(strikeWidth)}</span>
        <span class="inv-pos-tag">${i18n("Entry gap", "進場")} ${fmtDeribitPriceCell(entryGap, coll)}</span>`;
    } else {
      detailTags = `
        <span class="inv-pos-tag">${i18n("Strike", "履約")} ${fmtStrike(openRowLegStrike(g, "short"))}</span>
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
            <p class="inv-position-contract font-mono">${escapeHtml(g.short_instrument_name || "—")}<span class="inv-position-size tabular-nums">${fmtLegAmount(shortAmt)}</span></p>
            ${isBullPutSpread && longLeg ? `<p class="inv-position-contract font-mono inv-position-contract--long">${i18n("Long", "買腿")} ${escapeHtml(longLeg)}<span class="inv-position-size tabular-nums">${fmtLegAmount(longAmt)}</span></p>` : ""}
            <div class="inv-position-tags">
              <span class="inv-pos-tag">${escapeHtml(coll)}</span>
              <span class="inv-pos-tag inv-pos-tag--status">${escapeHtml(statusLabel)}</span>
              ${detailTags}
            </div>
          </div>
          <div class="inv-position-pnl">
            <span class="inv-position-pnl-label">${i18n("Unrealized", "未實現")}</span>
            <span class="inv-position-pnl-value font-mono tabular-nums ${pnlClass(pnlUsd)}">${pnlUsd === null ? "—" : fmtUsd(pnlUsd)}</span>
            <span class="inv-position-pnl-native font-mono tabular-nums ${pnlClass(nativeUnr)}">${fmtNativeUnrealizedDisplay(nativeUnr, coll)}</span>
          </div>
        </header>
        <div class="inv-position-strip" role="list">
          <div class="inv-pos-metric" role="listitem">
            <span class="inv-pos-metric-k">${i18n("DTE", "到期")}</span>
            <span class="inv-pos-metric-v font-mono tabular-nums">${dteVal !== null ? `${fmtNum(dteVal, 1)}${INVESTOR_ZH ? "天" : "d"}` : "—"}</span>
          </div>
          <div class="inv-pos-metric" role="listitem">
            <span class="inv-pos-metric-k">${i18n("Credit kept", "權利金")}</span>
            <span class="inv-pos-metric-v font-mono tabular-nums">${fmtPct(creditKept, 1)}</span>
            ${creditCaptureBarHtml(creditKept)}
          </div>
          <div class="inv-pos-metric" role="listitem">
            <span class="inv-pos-metric-k">${i18n("Entry", "進場")}</span>
            <span class="inv-pos-metric-v font-mono tabular-nums">${entryUsd}</span>
            ${entryNative}
          </div>
          <div class="inv-pos-metric" role="listitem">
            <span class="inv-pos-metric-k">${i18n("Entry APR", "進場年化")}</span>
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
    const bookPill = INVESTOR ? i18n(`${coll} book`, `${coll} 帳本`) : `${coll} book`;
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
              <span>${escapeHtml(g.short_instrument_name || "—")}</span>
              ${isBullPutSpread && longLeg ? `<span>${i18n("Long", "買入保護")} ${escapeHtml(longLeg)}</span>` : ""}
            </div>
            <div class="open-position-detail-row">
              ${openPositionDetailHtml(g, status, groups)}
              ${account ? `<span>${escapeHtml(account)}</span>` : ""}
            </div>
          </div>
          <div class="open-position-pnl-panel">
            <span class="open-position-label"${
              isBullPutSpread
                ? ` title="${i18n(
                    "Sum of leg mark MTM when both legs load; otherwise engine entry−debit (bid/ask close est.).",
                    "兩腿皆載入時為標記損益加總；否則為引擎進場收斂與現估平倉差額。"
                  )}"`
                : ""
            }>${i18n("Unrealized PNL", "未實現損益")}</span>
            <strong class="${pnlClass(pnlUsd)}">${pnlUsd === null ? "—" : fmtUsd(pnlUsd)}</strong>
            <span class="open-position-native ${pnlClass(nativeUnr)}">${fmtNativeUnrealizedDisplay(nativeUnr, coll)}</span>
          </div>
        </div>
        <div class="open-position-kpis open-position-kpis-extended">
          ${openPositionMetricHtml(
            i18n("DTE", "距到期天數"),
            dteVal !== null ? `${fmtNum(dteVal, 2)}${INVESTOR_ZH ? " 天" : "d"}` : "—"
          )}
          ${openPositionMetricHtml(
            i18n("Credit kept", "已收權利金比例"),
            `${fmtPct(creditKept, 1)}${creditCaptureBarHtml(creditKept)}`
          )}
          ${openPositionMetricHtml(
            i18n("Entry credit", "進場收斂"),
            entryCredit === null ? "—" : fmtUsdNativeBookStackHtml(entryCredit, entryCreditNative, coll)
          )}
          ${(() => {
            const entryApr = groupEntryNetApr(g, status);
            const aprClass =
              entryApr !== null && entryApr >= 0.15 ? "pnl-pos" : "";
            return openPositionMetricHtml(
              i18n("Entry net APR", "進場淨年化"),
              entryApr === null ? "—" : fmtPct(entryApr, 1),
              aprClass
            );
          })()}
          ${openPositionMetricHtml(
            i18n("Entry fee", "進場手續費"),
            entryFee === null ? "—" : fmtUsdNativeBookStackHtml(entryFee, entryFeeNative, coll)
          )}
          ${openPositionMetricHtml(
            i18n("Est. close fee", "預估平倉費"),
            closeFee === null ? "—" : fmtUsdNativeBookStackHtml(closeFee, closeFeeNative, coll)
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

  /** One strategy playbook: header + stacked open-position cards (avoids repeating the same trades as tables + flat list). */
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
          <span class="text-xs text-slate-500">${rows.length} ${i18n("open", "筆持倉")}</span>
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
      INVESTOR
        ? i18n(
            `${totalOpen} open · ${totalClosed} closed · ${activeStrategies || 0} active strategy groups`,
            `${totalOpen} 筆持倉 · ${totalClosed} 筆已平 · ${activeStrategies || 0} 類策略`
          )
        : `${totalOpen} open · ${totalClosed} closed · ${activeStrategies || 0} active strategy groups`
    );

    if (cardsRoot) {
      cardsRoot.innerHTML = summaries.map(strategySummaryCardHtml).join("");
    }

    if (!openRoot) return;
    if (!openRows.length) {
      openRoot.innerHTML = `
        <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-sm text-slate-400">
          ${i18n("No open strategy positions.", "目前沒有開倉中的策略部位。")}
        </div>`;
      return;
    }

    const byStrategy = new Map();
    const ids = new Set(STRATEGIES.map((s) => s.id));
    for (const g of openRows) {
      const id = strategyId(g);
      if (!STRATEGY_BY_ID[id]) continue;
      if (!byStrategy.has(id)) byStrategy.set(id, []);
      byStrategy.get(id).push(g);
    }
    openRoot.innerHTML = strategyOrder(ids)
      .filter((id) => byStrategy.has(id))
      .map((id) => strategyOpenGroupHtml(id, byStrategy.get(id), status, groups))
      .join("");
  }

  // ---------- charts ----------

  function chartCommonOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        legend: {
          labels: { color: "rgb(203 213 225)", boxWidth: 12, padding: 8 },
        },
        tooltip: {
          backgroundColor: "rgba(15,23,42,0.95)",
          borderColor: "rgb(51,65,85)",
          borderWidth: 1,
          titleColor: "rgb(226,232,240)",
          bodyColor: "rgb(226,232,240)",
        },
      },
      scales: {
        x: {
          type: "time",
          time: { tooltipFormat: "yyyy-LL-dd HH:mm" },
          grid: { color: "rgba(51,65,85,0.4)" },
          ticks: { color: "rgb(148,163,184)" },
        },
        y: {
          grid: { color: "rgba(51,65,85,0.4)" },
          ticks: { color: "rgb(148,163,184)" },
        },
      },
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
        /* ignore */
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

  let chartResizeObserversAttached = false;

  function attachChartResizeObservers() {
    if (chartResizeObserversAttached || typeof ResizeObserver === "undefined") return;
    chartResizeObserversAttached = true;
    document.querySelectorAll(".chart-panel-canvas").forEach((shell) => {
      const canvas = shell.querySelector("canvas");
      if (!canvas?.id) return;
      new ResizeObserver(() => resizeAllCharts()).observe(shell);
    });
  }

  /** Live portfolio equity for rolling APR denominator (matches engine effective capital). */
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
    // Prefer explicit panel wrapper; fall back to canvas parent for older cached HTML.
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

  const EMPTY_CHART_COPY = {
    realized: {
      en: "No closed positions yet — this chart fills in after the first close.",
      zh: "尚無平倉紀錄 — 首次平倉後此圖表才會開始累積。",
    },
    apr: {
      en: "Rolling APR needs closed trades and daily equity snapshots.",
      zh: "滾動年化需有平倉紀錄與每日權益快照。",
    },
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
        tooltip: { enabled: false },
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
            tooltipFormat: "yyyy-LL-dd",
          },
        },
        y: {
          ...base.scales.y,
          display: true,
          min: yMin,
          max: yMax,
          ticks: {
            ...base.scales.y.ticks,
            maxTicksLimit: 6,
            ...(yPercent ? { callback: (v) => fmtPct(v, 1) } : {}),
          },
        },
      },
    };
  }

  function mountEmptyTimeSeriesChart(
    canvasId,
    key,
    { yPercent = false, chartType = "line", messageKind = "realized" } = {}
  ) {
    const ctx = chartCanvasContext(canvasId);
    if (!ctx) return;
    destroyChart(key);
    setChartPanelEmpty(canvasId, {
      empty: true,
      message: emptyChartMessage(messageKind),
    });
    const xBounds = defaultEmptyChartTimeBounds();
    const placeholder = [
      { x: xBounds.min, y: 0 },
      { x: xBounds.max, y: 0 },
    ];
    // Skeleton uses a line at y=0 so axes/grid render reliably (bar placeholders are invisible).
    STATE.charts[key] = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [
          {
            label: i18n("No realized history yet", "尚無已實現紀錄"),
            data: placeholder,
            borderWidth: 1,
            pointRadius: 0,
            borderColor: "rgba(148, 163, 184, 0.35)",
            backgroundColor: "transparent",
          },
        ],
      },
      options: emptyChartScaleOptions({ yPercent, chartType }),
    });
  }

  function visibleBooks() {
    if (STATE.bookFilter === "ALL") return CORE_BOOKS;
    return [STATE.bookFilter];
  }

  /** Collateral book for an open trade group (matches engine grouping). */
  function tradeGroupCollateral(g) {
    const c = String(g.collateral_currency || "").toUpperCase();
    if (c) return c;
    const ins = String(g.short_instrument_name || "");
    if (ins.includes("_USDC-")) return "USDC";
    return String(g.currency || "").toUpperCase() || "";
  }

  function sumOpenCreditByBook(openGroups) {
    const out = { BTC: 0, ETH: 0, USDC: 0 };
    for (const g of openGroups || []) {
      const book = tradeGroupCollateral(g);
      const credit = openRowEntryCreditUsd(g, STATE.status, STATE.groups);
      if (credit === null || credit <= 0) continue;
      if (book === "BTC" || book === "ETH" || book === "USDC") out[book] += credit;
    }
    return out;
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
      const text = n === null ? "—" : fmtUsd(n);
      return `<div class="open-credit-row"><span class="open-credit-label text-slate-500">${short}</span><span class="open-credit-value font-mono tabular-nums text-slate-300">${text}</span></div>`;
    });
    return `<div class="open-credit-breakdown">${rows.join("")}</div>`;
  }

  /** Closed groups with realized PnL (full ``groups.closed`` + report enrich). */
  function lifetimeRealizedClosedRows(report, groups, status = null) {
    const st = status ?? STATE.status;
    return dedupeTradeGroups([
      ...(groups?.closed || []),
      ...(report?.recent_closed_trades || []),
    ])
      .filter((g) => isDisplayableClosedTradeGroup(g, st, groups))
      .filter((g) => num(g?.realized_pnl) !== null);
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
    const cutoffMs = Date.now() - days * 24 * 3600 * 1000;
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
    const symbols = { BTC: "₿", ETH: "♦", USDC: "($)" };
    const items = ["BTC", "ETH", "USDC"].map((book) => {
      const n = num(byBook[book]);
      const text = n === null ? "—" : fmtNum(n, places[book] ?? 4);
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

  function openTradeGroupsForRisk() {
    const tg = STATE.status?.trade_groups;
    if (tg && tg.length) return tg;
    return STATE.groups?.open || [];
  }

  function riskBarChartBaseOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          labels: { color: "rgb(203 213 225)", boxWidth: 12, padding: 8 },
        },
        tooltip: {
          backgroundColor: "rgba(15,23,42,0.95)",
          borderColor: "rgb(51,65,85)",
          borderWidth: 1,
          titleColor: "rgb(226,232,240)",
          bodyColor: "rgb(226,232,240)",
        },
      },
      scales: {
        x: {
          grid: { color: "rgba(51,65,85,0.4)" },
          ticks: { color: "rgb(148,163,184)" },
        },
        y: {
          beginAtZero: true,
          grid: { color: "rgba(51,65,85,0.4)" },
          ticks: {
            color: "rgb(148,163,184)",
            maxTicksLimit: 8,
          },
        },
      },
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
    let meta = i18n(`Total ${fmtUsd(totEq)}`, `合計 ${fmtUsd(totEq)}`);
    if (totEq !== null && sumBars > 0 && Math.abs(sumBars - totEq) > 1) {
      meta += i18n(" · bars sum may differ from headline", " · 各帳加總可能與總覽略有差異");
    } else if (!STATE.status) {
      meta = i18n("Awaiting live snapshot", "等待即時快照");
    }

    setText("risk-capital-meta", meta);
    setText(
      "risk-capital-hint",
      i18n(
        "Per-book equity in USDC equivalent from the live snapshot (or last saved snapshot).",
        "各帳本權益以 USDC 約當顯示，來自即時或最近快照。"
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
            label: i18n("Book equity (USDC eq.)", "帳本權益（USDC 約當）"),
            data: equityBars,
            backgroundColor: barColors.map((c) => c + "cc"),
            borderColor: barColors,
            borderWidth: 1,
          },
        ],
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
                if (i === undefined) return "";
                const eq = equityBars[i] ?? 0;
                const share = totEq > 0 ? eq / totEq : null;
                const lines = [
                  `${i18n("Share of total: ", "佔總權益：")}${fmtPct(share, 2)}`,
                ];
                return lines;
              },
            },
          },
        },
      },
    });
  }

  const MS_PER_DAY = 86400000;

  function dateToMs(dateStr) {
    const dt = luxon.DateTime.fromISO(String(dateStr || "").trim(), { zone: "utc" });
    if (!dt.isValid) return NaN;
    return dt.toMillis();
  }

  /**
   * Cumulative PnL is bucketed by UTC day; a single day yields one xy pair. Chart.js time scale
   * then zooms to a ~1ms window, and stepped lines with pointRadius 0 draw nothing. Pad with a
   * zero baseline and a trailing flat point so the axis and polylines render.
   */
  function finalizeCumulativeLineData(rawPoints) {
    const pts = rawPoints
      .filter((p) => Number.isFinite(p.x) && p.y !== null && Number.isFinite(p.y))
      .sort((a, b) => a.x - b.x);
    if (pts.length === 0) return [];
    if (pts.length === 1) {
      const p = pts[0];
      return [
        { x: p.x - MS_PER_DAY, y: 0 },
        { x: p.x, y: p.y },
        { x: p.x + MS_PER_DAY, y: p.y },
      ];
    }
    return pts;
  }

  function filterValidTimePoints(rawPoints) {
    return rawPoints
      .filter((p) => Number.isFinite(p.x) && p.y !== null && Number.isFinite(p.y))
      .sort((a, b) => a.x - b.x);
  }

  /** APR / moving-average lines: one point does not draw a segment with pointRadius 0. */
  function finalizeSimpleLineData(rawPoints) {
    const pts = filterValidTimePoints(rawPoints);
    if (pts.length === 0) return [];
    if (pts.length === 1) {
      const p = pts[0];
      return [p, { x: p.x + MS_PER_DAY, y: p.y }];
    }
    return pts;
  }

  /** Single bucket or one timestamp → Chart.js time scale collapses to ms-wide window (bars + lines). */
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
    const closedMeta = series?.realized_count
      ? `${series.realized_count} closed groups`
      : i18n("no closed groups", "尚無已平倉組");
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
            borderWidth: 2,
          });
        }
      }
    }
    if (STATE.bookFilter === "ALL" && series.cumulative_total?.length) {
      const data = finalizeCumulativeLineData(
        series.cumulative_total.map((r) => ({
          x: dateToMs(r.date),
          y: num(r.pnl_usdc),
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
          borderDash: [4, 4],
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
      options: chartCommonOptions(),
    });
  }

  /** Optional: drop zero-height bars (per-book series has many explicit zeros). */
  function compactNonZeroDailyBars(points) {
    return points.filter((p) => Math.abs(p.y) > 1e-12);
  }

  const DAILY_PNL_PROFIT_FILL = "rgba(52, 211, 153, 0.67)";
  const DAILY_PNL_PROFIT_BORDER = "#34d399";
  const DAILY_PNL_LOSS_FILL = "rgba(251, 113, 133, 0.67)";
  const DAILY_PNL_LOSS_BORDER = "#fb7185";

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
      setText("daily-pnl-meta", i18n("no closed groups", "尚無已平倉組"));
      mountEmptyTimeSeriesChart("chart-daily-pnl", "dailyPnl", { chartType: "bar" });
      return;
    }
    const books = visibleBooks();
    const validDaily = (series.daily_total || []).filter((r) => Number.isFinite(dateToMs(r.date)));
    let meta = series?.daily_total?.length
      ? `${series.daily_total.length} ${i18n("active days", "個有效交易日")}`
      : i18n("no closed groups", "尚無已平倉組");
    if (STATE.bookFilter === "ALL" && validDaily.length >= MA_WINDOW) {
      meta += " · 30d SMA";
    }
    setText("daily-pnl-meta", meta);
    const mapDay = (r) => ({ x: dateToMs(r.date), y: num(r.pnl_usdc) });
    let datasets = [];
    if (STATE.bookFilter === "ALL") {
      const barData = filterValidTimePoints((series.daily_total || []).map(mapDay));
      if (barData.length) {
        datasets.push({
          type: "bar",
          label: i18n("Daily total", "每日合計"),
          data: barData,
          order: 1,
          backgroundColor: dailyPnlBarFillColors(barData),
          borderColor: dailyPnlBarBorderColors(barData),
          borderWidth: 1,
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
            label: `${book} ${i18n("daily", "每日")}`,
            data: barData,
            order: 1,
            backgroundColor: dailyPnlBarFillColors(barData),
            borderColor: dailyPnlBarBorderColors(barData),
            borderWidth: 1,
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
          y: sum / MA_WINDOW,
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
          borderWidth: 2,
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
            time: { unit: "day", tooltipFormat: "yyyy-LL-dd" },
          },
          y: {
            ...base.scales.y,
            ticks: {
              ...base.scales.y.ticks,
              maxTicksLimit: 10,
            },
          },
        },
      },
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
            fill: true,
          },
        ],
      },
      options: {
        ...base,
        scales: {
          x: {
            ...base.scales.x,
            ...xBounds,
            time: { unit: "day", tooltipFormat: "yyyy-LL-dd" },
          },
          y: {
            ...base.scales.y,
            ticks: {
              ...base.scales.y.ticks,
              callback: (v) => fmtPct(v, 1),
            },
          },
        },
      },
    });
  }

  // ---------- tables ----------

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
    root.innerHTML = html.length
      ? html.join("")
      : `<li class="activity-empty">${escapeHtml(emptyLabel)}</li>`;
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
        `${openAll.length} open · ${closedAll.length} closed`,
        `${openAll.length} 持倉中 · ${closedAll.length} 已平倉`
      )
    );

    renderRecentActivityList(
      openRoot,
      openPage.rows,
      status,
      groups,
      i18n("No open positions", "尚無持倉")
    );
    renderRecentActivityList(
      closedRoot,
      closedPage.rows,
      status,
      groups,
      i18n("No closed trades", "尚無已平倉紀錄")
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

  /** Realized PnL in the book collateral native unit (USDC pnl ÷ index for inverse books). */
  function closedPnlInBookNativeUnits(g, status) {
    return realizedPnlInAprBookNative(g, status);
  }

  /** Realized PnL annualized on position collateral (not whole-book equity). */
  function closedAnnualizedReturnOnEquity(g, status) {
    return annualizedAprOnPositionCapital(g, status);
  }

  // ---------- stress card ----------

  function stressSections(stress) {
    const grouped = Array.isArray(stress?.strategy_stresses)
      ? stress.strategy_stresses.filter(Boolean)
      : [];
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
    const accountNames = (stress.accounts || [])
      .map((a) => a?.name)
      .filter(Boolean)
      .join(", ");
    const actions = Array.isArray(analysis.actions) ? analysis.actions : [];

    const equityRow = CORE_BOOKS
      .map(
        (b) => `
          <div class="rounded-xl bg-slate-800/40 px-3 py-2">
            <div class="text-[11px] text-slate-400 uppercase tracking-wide">${b} book</div>
            <div class="font-mono text-sm">${fmtUsd(equity[b])}</div>
          </div>`
      )
      .join("");

    const scenarioRows = (stress.scenarios || [])
      .map((s) => {
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
      })
      .join("");

    const actionList = actions.length
      ? `<ul class="mt-2 list-disc list-inside text-xs text-slate-500 space-y-1">
          ${actions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
        </ul>`
      : "";

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
              ${escapeHtml(accountNames || `${stress.scenarios?.length || 0} scenarios · ${stress.positions?.length || 0} legs`)}
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
      setText("stress-meta", "—");
      return;
    }
    const sections = stressSections(stress);
    const scenarioCount = sections.reduce((sum, item) => sum + (item.scenarios?.length || 0), 0);
    const legCount = sections.reduce((sum, item) => sum + (item.positions?.length || 0), 0);
    setText(
      "stress-meta",
      `${sections.length} strategy view${sections.length === 1 ? "" : "s"} · ${scenarioCount} scenarios · ${legCount} legs`
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

  // ---------- data refresh ----------

  function updateHeaderSpotDom() {
    const elBtc = document.getElementById("header-spot-btc");
    const elEth = document.getElementById("header-spot-eth");
    const b = STATE.lastSpotUsd.BTC;
    const e = STATE.lastSpotUsd.ETH;
    if (elBtc) elBtc.textContent = b !== null && b > 0 ? `BTC ${fmt.usd2.format(b)}` : "BTC —";
    if (elEth) elEth.textContent = e !== null && e > 0 ? `ETH ${fmt.usd2.format(e)}` : "ETH —";
  }

  function renderPerformanceCharts() {
    const chartFns = [
      ["risk-capital", renderBookEquityChart],
      ["cum-pnl", renderCumulativePnlChart],
      ["daily-pnl", renderDailyPnlChart],
      ["apr", renderAprChart],
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

  const INVESTOR_LOAD_STEPS = {
    spot: {
      en: "Fetching BTC / ETH market prices…",
      zh: "正在取得 BTC / ETH 即時報價…",
    },
    snapshot: {
      en: "Loading last equity snapshot…",
      zh: "正在讀取最近權益快照…",
    },
    health: {
      en: "Checking account connection…",
      zh: "正在確認帳戶連線…",
    },
    groups: {
      en: "Loading open positions and spreads…",
      zh: "正在讀取持倉與價差部位…",
    },
    cumulative: {
      en: "Loading realized P&L history…",
      zh: "正在載入已實現損益歷史…",
    },
    apr: {
      en: "Calculating rolling performance (APR)…",
      zh: "正在計算滾動年化報酬…",
    },
    status: {
      en: "Syncing live equity and margin…",
      zh: "正在同步即時權益與保證金…",
    },
    summary: {
      en: "Loading performance summary from local records…",
      zh: "正在從本地紀錄載入績效摘要…",
    },
    render: {
      en: "Preparing your dashboard…",
      zh: "正在整理儀表板顯示…",
    },
    done: {
      en: "Done",
      zh: "完成",
    },
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
    set("eyebrow", "Please wait", "請稍候");
    set("title", "Loading your portfolio", "正在載入您的投資組合");
    set(
      "hint",
      "Showing snapshot first; live positions and P&L sync in the background.",
      "先顯示最近快照；持倉與損益於背景同步中。"
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
    const ratio =
      STATE.investorLoadTotal > 0 ? STATE.investorLoadDone / STATE.investorLoadTotal : 0;
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
      /* ignore */
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
      /* snapshot is optional for first paint */
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
              "即時同步較慢，先顯示最近快照。"
            )
          );
          STATE.statusErrorOnce = true;
        }
        fetchJson("/api/status")
          .then((d) => {
            STATE.status = d;
            STATE.dataFreshness.source = "live";
            STATE.dataFreshness.live = true;
            renderDashboard();
          })
          .catch(() => {});
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
    const raced = INVESTOR
      ? Promise.race([
          bundleRequest,
          delay(timeoutMs).then(() => {
            timedOut = true;
            throw new Error("dashboard bundle timeout");
          }),
        ])
      : bundleRequest;
    try {
      applyDashboardBundlePayload(await raced);
      return true;
    } catch (err) {
      if (INVESTOR && timedOut && STATE.portfolioSnapshot?.portfolio) {
        if (!STATE.statusErrorOnce) {
          showToast(
            i18n(
              "Live sync is slow; showing last snapshot.",
              "即時同步較慢，先顯示最近快照。"
            )
          );
          STATE.statusErrorOnce = true;
        }
        if (backgroundOnTimeout) {
          fetchJson(dashboardBundleUrl(30))
            .then((d) => {
              applyDashboardBundlePayload(d);
              renderDashboard();
            })
            .catch(() => {});
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
      const fetchCumulative = () =>
        fetchJson("/api/cumulative_pnl_series")
          .then((d) => {
            STATE.cumulativePnl = d;
          })
          .catch((err) => showToast(`cumulative pnl: ${err.message}`));

      const fetchApr = () =>
        fetchJson(aprSeriesUrl())
          .then((d) => {
            STATE.aprSeries = d;
          })
          .catch((err) => showToast(`apr series: ${err.message}`));

      if (investorFetchWrap) {
        await Promise.all([
          investorFetchWrap("cumulative", fetchCumulative),
          investorFetchWrap("apr", fetchApr),
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
      if (!silentIfLimited) showToast(i18n("refresh already running", "已有更新正在進行"));
      return;
    }
    const waitMs = refreshWaitMs();
    if (!force && waitMs > 0) {
      if (!silentIfLimited)
        showToast(
          i18n(
            `refresh rate limited; wait ${Math.ceil(waitMs / 1000)}s`,
            `請稍候 ${Math.ceil(waitMs / 1000)} 秒後再試`
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
      let renderScheduled = false;
      function scheduleRender() {
        if (renderScheduled) return;
        renderScheduled = true;
        requestAnimationFrame(() => {
          renderScheduled = false;
          renderDashboard();
        });
      }

      function investorFetch(stepKey, run) {
        if (!investorFirstLoad) return run();
        return run().finally(() => advanceInvestorLoad(stepKey));
      }

      try {
        const spotPromise = investorFetch("spot", () =>
          tickHeaderSpot({
            renderDependentViews: !INVESTOR,
            updateDom: true,
          })
        );
        const healthPromise = investorFetch("health", () =>
          fetchJson("/api/health").then((d) => {
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

      // Investor first paint: race snapshot vs overlay cap, then unlock the page.
      if (INVESTOR && investorFirstLoad) {
        try {
          await Promise.race([
            investorFetch("snapshot", fetchPortfolioSnapshot),
            delay(INVESTOR_OVERLAY_MAX_MS),
          ]);
        } catch (_) {
          /* snapshot optional; always dismiss blocking overlay */
        }
        snapshotFetchedThisRefresh = true;
        setInvestorPageReady(true);
        setInvestorProgressBar(true, { indeterminate: true });
        scheduleRender();
      }

      const investorFetchWrap = investorFirstLoad
        ? (stepKey, run) => investorFetch(stepKey, run)
        : null;
      const wrapStep = (stepKey, run) =>
        investorFetchWrap ? investorFetchWrap(stepKey, run) : run();

      function fetchGroups() {
        return fetchJson("/api/groups")
          .then((d) => {
            STATE.groups = d;
            scheduleRender();
          })
          .catch((err) => {
            showToast(`groups: ${err.message}`);
          });
      }

      function fetchSummary() {
        return fetchJson(realizedSummaryUrl(30))
          .then((d) => {
            STATE.report = d;
            scheduleRender();
          })
          .catch((err) => showToast(`realized summary: ${err.message}`));
      }

      function fetchStatusOp() {
        return fetchJson("/api/status")
          .then((d) => {
            STATE.status = d;
            STATE.statusErrorOnce = false;
            scheduleRender();
          })
          .catch((err) => {
            STATE.status = null;
            if (!STATE.statusErrorOnce) {
              showToast(`status: ${err.message}`);
              STATE.statusErrorOnce = true;
            }
          });
      }

      function fetchStress() {
        return fetchJson("/api/stress?shocks=0.1,0.2,0.3,0.4,0.5")
          .then((d) => {
            STATE.stress = d;
            scheduleRender();
          })
          .catch((err) => showToast(`stress: ${err.message}`));
      }

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
        wave.push(() =>
          loadChartDataIfNeeded({
            force: !INVESTOR,
            investorFetchWrap,
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
        `${i18n("last refresh:", "上次更新：")} ${luxon.DateTime.now().toFormat("HH:mm:ss")}`
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

  function bootDashboard() {
    applyInvestorLoadCopy();
    attachChartResizeObservers();
    attachControls();
    attachExpandableSections();
    attachAutoRefresh();
    refreshAll({ force: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootDashboard);
  } else {
    bootDashboard();
  }
})();
