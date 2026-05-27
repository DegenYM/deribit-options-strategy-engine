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
import { accountHint, activityClosedRows, activityLifecycleCardHtml, activityOpenRows, activityPaginationHtml, aggregateSkeletonHtml, annualizedAprOnPositionCapital, bookDayPnlUsdForDisplay, bookEquityNative, bookEquityUsdByBook, bookEquityUsdForDisplay, bullPutSpreadWidth, closedRowsForStrategyStats, closedTimestampMs, collateralBookSpotUsd, currentOpenRows, escapeHtml, fmtDate, fmtDeribitPriceCell, fmtNativeUnrealizedDisplay, fmtNum, fmtPct, fmtStrike, fmtUsd, fmtUsdNativeBookStackHtml, groupCloseFeeNative, groupCloseFeeUsd, groupEntryCreditNative, groupEntryFeeNative, groupEntryFeeUsd, groupEntryNetApr, groupHoldingDays, groupRealizedApr, hasOwn, investorOverviewHtml, lifetimePerformanceStartMs, normalizeStrategyId, num, openPositionTitle, openRowBookCollateralUpper, openRowDisplayNativeUnrealizedValue, openRowDisplayUnrealizedUsd, openRowDteDays, openRowEntryCreditUsd, openRowLegFieldValue, openRowLegInstrumentName, openRowLegPnlUsd, openRowLegPriceGap, openRowLegSignedSizeForDisplay, openRowLegStrike, optionPutCallLabel, overviewMetricsGridHtml, paginateRows, pnlClass, portfolioDayPnlUsdForDisplay, realizedPnlDisplayUsdc, realizedPnlInAprBookNative, renderDataFreshnessBadge, resolvedPortfolio, setText, strategyChipHtml, strategyId, strategyInfo, strategyLegDetail, strategyOrder, strategyTitle, tradeGroupAprBook, tradeGroupAprCapitalBase } from "./domain.js";
import { bookEquityNativeByBook, sumLifetimeRealizedPnlNativeByBook, sumOpenCreditByStrategy, sumWindowRealizedPnlNativeByBook } from "./charts.js";
import { strategiesSectionOpen } from "./sections.js";
export function renderInvestorHeaderIdentity(health) {
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
export function envBadgeToneClass(env) {
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

export function renderTopBar(health) {
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

export function renderRegime(status) {
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

export function bookCardHtml(book, status) {
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

export function renderBookCards(status) {
  const root = document.getElementById("book-cards");
  if (!root) return;
  if (!document.getElementById("books-section")?.open) return;
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

export function renderAccountCards(health, status) {
  const root = document.getElementById("account-cards");
  if (!root) return;
  if (!document.getElementById("account-section")?.open) return;
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

export function renderAggregate(status, report) {
  const root = document.getElementById("aggregate-card");
  if (!root) return;
  const { portfolio, source } = resolvedPortfolio();
  const summary = report?.summary;

  if (!portfolio && !summary) {
    if (INVESTOR && (STATE.refreshInFlight || !STATE.investorReady)) {
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
  const lifetimeStartMs = summary ? lifetimePerformanceStartMs(report, STATE.groups) : null;
  const lifetimeNativeByBook = summary
    ? sumLifetimeRealizedPnlNativeByBook(report, STATE.groups, status)
    : null;
  const windowLabelDays = windowDays ?? 30;
  const windowNativeByBook = summary
    ? sumWindowRealizedPnlNativeByBook(report, STATE.groups, status, windowLabelDays)
    : null;
  const equityNativeByBook = bookEquityNativeByBook(status);
  const equityUsdByBook = bookEquityUsdByBook(status);
  const sinceLine =
    lifetimeStartMs !== null
      ? `${i18n("since", "自")} ${fmtDate(lifetimeStartMs)}`
      : i18n("no realized history yet", "尚無已實現紀錄");
  const freshnessNote =
    source === "snapshot"
      ? `<p class="text-xs text-amber-200/80 mt-3">${i18n(
          INVESTOR
            ? "Equity from last snapshot; live sync continues in background."
            : "Equity from last snapshot; live Deribit sync in progress.",
          INVESTOR
            ? "權益來自最近快照；即時同步於背景進行中。"
            : "權益來自最近快照；Deribit 即時同步進行中。"
        )}</p>`
      : source === "live" && INVESTOR
      ? `<p class="text-xs text-emerald-200/70 mt-3">${i18n("Live Deribit sync", "已同步 Deribit 即時資料")}</p>`
      : source === "live" && STATE.summaryLoadPending
      ? `<p class="text-xs text-amber-200/80 mt-3">${i18n(
          "Performance summary still syncing…",
          "績效摘要背景同步中…"
        )}</p>`
      : source === "live" && !INVESTOR && STATE.refreshInFlight
      ? `<p class="text-xs text-slate-500 mt-3">${i18n("Refreshing…", "更新中…")}</p>`
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
    equityUsdByBook,
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

export function emptyStrategySummary(id) {
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

export function ensureStrategySummary(map, ids, id) {
  const key = id || "";
  ids.add(key);
  if (!map.has(key)) map.set(key, emptyStrategySummary(key));
  return map.get(key);
}

export function closedBookEquityUsd(status, book) {
  const b = String(book || "USDC").toUpperCase();
  const fromPortfolio = num(status?.portfolio?.equity_by_book?.[b]);
  if (fromPortfolio !== null && fromPortfolio > 0) return fromPortfolio;
  const native = bookEquityNative(status, b);
  if (native === null) return null;
  if (b === "USDC") return native;
  const spot = num(status?.underlying_index_usd?.[b]) ?? num(STATE.lastSpotUsd?.[b]);
  if (spot === null || spot <= 0) return null;
  return native * spot;
}

export function closedAnnualizedCapitalDaysWeight(g, status, holding) {
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
export function strategyAggregateRealizedApr(summary) {
  if (summary.aprCapitalDays > 0) {
    return (summary.aprPnlUsdSum / summary.aprCapitalDays) * 365;
  }
  return null;
}

export function buildStrategySummaries(status, report, groups) {
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

export function strategySummaryCardHtml(summary) {
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

export function openPositionStrategyClass(id) {
  const key = normalizeStrategyId(id);
  if (key === "covered_call") return "open-position-call";
  if (key === "bull_put_spread") return "open-position-spread";
  return "open-position-put";
}

export function openPositionToneClass(value) {
  const n = num(value);
  if (n === null || Math.abs(n) < 0.005) return "open-position-flat";
  return n > 0 ? "open-position-profit" : "open-position-loss";
}

export function openPositionStatusLabel(value) {
  const n = num(value);
  if (n === null || Math.abs(n) < 0.005) return i18n("Flat", "持平");
  return n > 0 ? i18n("In profit", "浮盈") : i18n("Underwater", "浮虧");
}

export function creditCaptureBarHtml(value) {
  const pct = num(value);
  const width = pct === null ? 0 : Math.max(0, Math.min(100, pct * 100));
  const tone = pct === null ? "bar-muted" : pct >= 0.5 ? "bar-ok" : pct >= 0.15 ? "bar-warn" : "bar-bad";
  return `<span class="credit-capture-bar"><span class="${tone}" style="width:${width}%"></span></span>`;
}

export function openPositionMetricHtml(label, valueHtml, extraClass = "", { secondary = false } = {}) {
  const secondaryClass = secondary ? " open-position-kpi-secondary" : "";
  return `
    <div class="open-position-metric${secondaryClass} ${extraClass}">
      <span class="open-position-label">${label}</span>
      <span class="open-position-value">${valueHtml}</span>
    </div>`;
}

export function openPositionLegCardHtml(g, status, groups, role) {
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

export function openPositionDetailHtml(g, status, groups) {
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

export function openPositionCardInvestorHtml(g, status, groups) {
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

export function openPositionCardDesktopHtml(g, status, groups) {
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

export function openPositionCardHtml(g, status, groups) {
  const desktop = openPositionCardDesktopHtml(g, status, groups);
  if (!INVESTOR) return desktop;
  return `<div class="investor-view-desktop">${desktop}</div><div class="investor-view-mobile">${openPositionCardInvestorHtml(g, status, groups)}</div>`;
}

/** One strategy playbook: header + stacked open-position cards (avoids repeating the same trades as tables + flat list). */
export function strategyOpenGroupHtml(id, rows, status, groups) {
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

function countActiveStrategyIds(openRows, report, groups) {
  const ids = new Set();
  for (const g of openRows) {
    const id = strategyId(g);
    if (STRATEGY_BY_ID[id]) ids.add(id);
  }
  const noteClosed = (g) => {
    const id = strategyId(g);
    if (STRATEGY_BY_ID[id]) ids.add(id);
  };
  for (const g of groups?.closed || []) noteClosed(g);
  for (const g of report?.recent_closed_trades || []) noteClosed(g);
  return ids.size;
}

export function updateStrategyMeta(status, report, groups, { openRows = null } = {}) {
  const meta = document.getElementById("strategy-meta");
  if (!meta) return;
  const rows = openRows ?? currentOpenRows(status, groups);
  const totalOpen = rows.length;
  const summaryClosed = num(report?.summary?.realized_closed_group_count);
  const totalClosed =
    summaryClosed !== null ? summaryClosed : closedRowsForStrategyStats(report, groups).length;
  const activeStrategies = countActiveStrategyIds(rows, report, groups);
  meta.textContent = INVESTOR
    ? i18n(
        `${totalOpen} open · ${totalClosed} closed · ${activeStrategies || 0} active strategy groups`,
        `${totalOpen} 筆持倉 · ${totalClosed} 筆已平 · ${activeStrategies || 0} 類策略`
      )
    : `${totalOpen} open · ${totalClosed} closed · ${activeStrategies || 0} active strategy groups`;
}

export function renderStrategyGroups(status, report, groups) {
  const openRows = currentOpenRows(status, groups);
  updateStrategyMeta(status, report, groups, { openRows });
  if (!strategiesSectionOpen()) return;

  const cardsRoot = document.getElementById("strategy-cards");
  const openRoot = document.getElementById("strategy-open-groups");
  if (!cardsRoot && !openRoot) return;

  const summaries = buildStrategySummaries(status, report, groups);
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
export function renderRecentActivityList(root, rows, status, groups, emptyLabel) {
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

export function renderRecentActivity(status, report, groups) {
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

/** Realized PnL in the book collateral native unit (USDC pnl ÷ index for inverse books). */
export function closedPnlInBookNativeUnits(g, status) {
  return realizedPnlInAprBookNative(g, status);
}

/** Realized PnL annualized on position collateral (not whole-book equity). */
export function closedAnnualizedReturnOnEquity(g, status) {
  return annualizedAprOnPositionCapital(g, status);
}

// ---------- stress card ----------

export function stressSections(stress) {
  const grouped = Array.isArray(stress?.strategy_stresses)
    ? stress.strategy_stresses.filter(Boolean)
    : [];
  return grouped.length ? grouped : [stress];
}

export function renderStressSection(stress, sectionCount) {
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

export function renderStress(stress) {
  if (INVESTOR) return;
  const root = document.getElementById("stress-card");
  if (!root) return;
  const stressOpen = Boolean(document.getElementById("stress-section")?.open);
  if (!stress && !STATE.stressDataLoaded && !stressOpen) return;
  if (!stress) {
    if (STATE.stressLoadInFlight || STATE.health?.has_private_creds) {
      root.innerHTML = `<p class="text-slate-500 text-sm">Loading…</p>`;
    } else {
      root.innerHTML = `<p class="text-sm text-slate-400">Set DERIBIT_CLIENT_ID and DERIBIT_CLIENT_SECRET to load live stress data.</p>`;
    }
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
