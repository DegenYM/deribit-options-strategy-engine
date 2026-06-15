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
  USE_DASHBOARD_BUNDLE,
  fmt,
} from "../shared/config.js";
import { STATE } from "../shared/state.js";
import { accountHint, activeHedgeSummaryRows, activityClosedRows, activityLifecycleCardHtml, activityOpenRows, activityPaginationHtml, aggregateSkeletonHtml, annualizedAprOnPositionCapital, bookDayPnlUsdForDisplay, bookEquityNative, bookEquityUsdByBook, bookEquityUsdForDisplay, bullPutSpreadWidth, closedRowsForStrategyStats, closedTimestampMs, collateralBookSpotUsd, currentOpenRows, dashboardStrategyIds, escapeHtml, fmtDate, fmtDeribitPriceCell, fmtNativeBookAmount, fmtNativeUnrealizedDisplay, fmtNum, fmtPct, fmtStrike, fmtTime, fmtUsd, fmtUsdNativeBookStackHtml, groupCloseFeeNative, groupCloseFeeUsd, groupEntryCreditNative, groupEntryFeeNative, groupEntryFeeUsd, groupEntryNetApr, groupHoldingDays, groupRealizedApr, hasOwn, investorOverviewHtml, isDashboardStrategy, isInvestorOverviewDisplayReady, lifetimePerformanceStartMs, normalizeStrategyId, num, openPositionTitle, openRowBookCollateralUpper, openRowDisplayNativeUnrealizedValue, openRowDisplayUnrealizedUsd, openRowDteDays, openRowEntryCreditUsd, openRowLegFieldValue, openRowLegInstrumentName, openRowLegPnlUsd, openRowLegPriceGap, openRowLegSignedSizeForDisplay, openRowLegStrike, optionPutCallLabel, overviewDesktopContentHtml, overviewEquityBreakdown, paginateRows, pnlClass, portfolioDayPnlUsdForDisplay, realizedPnlDisplayUsdc, realizedPnlInAprBookNative, renderDataFreshnessBadge, resolvedPortfolio, setText, strategyChipHtml, strategyId, strategyInfo, strategyLegDetail, strategyOrder, strategyTitle, tradeGroupAprBook, tradeGroupAprCapitalBase } from "./domain.js";
import { aggregateProfitDisposition, computeLifetimeRealizedApr, computeWindowRealizedApr, profitCompositionByBook, sumLifetimeRealizedPnlNativeByBook, sumLifetimeRealizedPnlUsdcAtSpot, sumOpenCreditByStrategy, sumWindowRealizedPnlNativeByBook, sumWindowRealizedPnlUsdcAtSpot } from "./charts.js";
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

/** Compact investor header chip: label · value on one line, no awkward wrapping. */
function setInvestorChip(el, label, value, tone = "neutral") {
  if (!el) return;
  el.hidden = false;
  el.innerHTML = `<span class="inv-chip__label">${escapeHtml(label)}</span><span class="inv-chip__value">${escapeHtml(value)}</span>`;
  el.className = `inv-chip inv-chip--${tone}`;
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
    if (INVESTOR) {
      const envValue =
        env === "mainnet"
          ? i18n("Mainnet", "主網")
          : env === "multi"
          ? i18n("Multi-acct", "多帳戶")
          : env === "test"
          ? i18n("Test", "測試")
          : env || "—";
      const envTone =
        env === "mainnet" ? "info" : env === "test" ? "warning" : "neutral";
      setInvestorChip(envBadge, i18n("Network", "網路"), envValue, envTone);
    } else {
      envBadge.textContent = `env: ${env || "?"}`;
      envBadge.className =
        "text-xs px-2 py-0.5 rounded-full border " + envBadgeToneClass(env);
    }
  }

  const strategyBadge = document.getElementById("strategy-badge");
  if (strategyBadge) {
    const strategy = normalizeStrategyId(health.option_strategy || "");
    const accountCount = health.accounts?.length || 0;
    if (INVESTOR) {
      const strategyValue = health.multi_account
        ? i18n(`Multi · ${accountCount}`, `多帳戶 · ${accountCount}`)
        : strategy
        ? strategyTitle(strategy)
        : "—";
      setInvestorChip(
        strategyBadge,
        i18n("Strategy", "策略"),
        strategyValue,
        "info"
      );
    } else {
      strategyBadge.textContent = health.multi_account
        ? i18n(`strategy: multi (${accountCount} accounts)`, `策略：多帳戶（${accountCount}）`)
        : `strategy: ${strategy ? strategyTitle(strategy) : "?"}`;
      strategyBadge.className =
        "text-xs px-2 py-0.5 rounded-full border border-sky-500/50 bg-sky-500/10 text-sky-200";
    }
  }

  const profitSweepBadge = document.getElementById("profit-sweep-badge");
  if (profitSweepBadge) {
    const showSweep =
      INVESTOR &&
      (health.covered_call_profit_sweep_enabled ||
        health.option_strategy === "covered_call" ||
        (health.accounts || []).some(
          (a) => a.option_strategy === "covered_call" && a.covered_call_profit_sweep_enabled
        ));
    if (showSweep) {
      const enabled = !!health.covered_call_profit_sweep_enabled;
      if (INVESTOR) {
        setInvestorChip(
          profitSweepBadge,
          i18n("Sweep", "兌 USDT"),
          enabled ? i18n("On", "開啟") : i18n("Off", "關閉"),
          enabled ? "success" : "neutral"
        );
      } else {
        profitSweepBadge.hidden = false;
        profitSweepBadge.textContent = enabled
          ? i18n("Profit → USDT: on", "獲利兌 USDT：開啟")
          : i18n("Profit → USDT: off", "獲利兌 USDT：關閉");
        profitSweepBadge.className =
          "text-xs px-2 py-0.5 rounded-full border " +
          (enabled
            ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-200"
            : "border-slate-600 bg-slate-700/30 text-slate-300");
      }
    } else {
      profitSweepBadge.hidden = true;
    }
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
  if (INVESTOR) {
    const regValue = INVESTOR_ZH ? regZh[regKey] || regime : regEn[regKey] || regime;
    const tone =
      regime === "normal"
        ? "success"
        : regime === "elevated"
        ? "warning"
        : regime === "crisis"
        ? "danger"
        : "neutral";
    setInvestorChip(badge, i18n("Risk", "風控"), regValue, tone);
    return;
  }
  badge.textContent = `regime: ${regime}`;
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

function bookCardAccentClass(book) {
  if (book === "BTC") return "book-card-btc";
  if (book === "ETH") return "book-card-eth";
  if (book === "USDT") return "book-card-usdt";
  return "book-card-usdc";
}

function regimeChipClass(regime) {
  if (regime === "normal") return "chip-ok";
  if (regime === "elevated") return "chip-warn";
  return "chip-bad";
}

/** USDC book trades BTC_USDC / ETH_USDC — show each underlying's risk regime, not ``regime_by_currency.USDC``. */
function bookRegimeChipsHtml(book, portfolio, isRiskBook) {
  if (!isRiskBook) return "";
  const regimeByCcy = portfolio?.regime_by_currency || {};
  const haltByCcy = portfolio?.halt_new_entries_by_currency || {};
  if (book === "USDC") {
    const lines = ["BTC", "ETH"]
      .filter((ccy) => regimeByCcy[ccy])
      .map((ccy) => {
        const regime = regimeByCcy[ccy];
        const halted = haltByCcy[ccy];
        const suffix = halted ? " · no entry" : "";
        return `<span class="chip ${regimeChipClass(regime)}">${ccy} ${regime}${suffix}</span>`;
      });
    return lines.join("");
  }
  const regime = regimeByCcy[book];
  if (!regime) return "";
  return `<span class="chip ${regimeChipClass(regime)}">${regime}</span>`;
}

function bookShowsUnderlyingEntryHalt(book, portfolio, isRiskBook) {
  if (!isRiskBook || book !== "USDC") return false;
  const haltByCcy = portfolio?.halt_new_entries_by_currency || {};
  return ["BTC", "ETH"].some((ccy) => haltByCcy[ccy]);
}

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
  const cooling = portfolio?.cooling_down_by_book?.[book];
  const hardDerisk = portfolio?.hard_derisk_by_book?.[book];
  const haltEntries = portfolio?.halt_entries_by_book?.[book];
  const underlyingEntryHalt = bookShowsUnderlyingEntryHalt(book, portfolio, isRiskBook);
  const haltReasons = portfolio?.halt_entry_reasons_by_book?.[book] || [];

  const accentClass = bookCardAccentClass(book);

  const chips = [];
  if (!isRiskBook) {
    chips.push('<span class="chip chip-muted">not traded</span>');
  }
  const regimeChips = bookRegimeChipsHtml(book, portfolio, isRiskBook);
  if (regimeChips) chips.push(regimeChips);
  if (cooling) chips.push('<span class="chip chip-warn">cooling</span>');
  if (hardDerisk) chips.push('<span class="chip chip-bad">hard derisk</span>');
  if (haltEntries) chips.push('<span class="chip chip-warn">halt entries</span>');
  if (underlyingEntryHalt && !haltEntries) {
    chips.push('<span class="chip chip-warn">underlying halt</span>');
  }
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

  const nativePlaces = book === "BTC" ? 8 : book === "ETH" ? 8 : 4;

  return `
    <div class="book-card-tile rounded-2xl border ${accentClass} bg-slate-900/60 p-4 shadow min-w-0">
      <div class="flex items-start justify-between gap-2 mb-2 min-w-0">
        <h3 class="text-sm font-semibold tracking-wide text-slate-200 shrink-0">${book} BOOK</h3>
        <div class="flex flex-wrap justify-end gap-1 min-w-0">${chips.join("")}</div>
      </div>
      <div class="text-2xl font-mono tabular-nums">${fmtUsd(equityUsdc)}</div>
      <div class="book-card-meta text-xs text-slate-500 mb-3">
        ${equityNative !== null ? fmtNum(equityNative, nativePlaces) + " " + book : ""}
        ${dayStartUsdc !== null ? "· day-start " + fmtUsd(dayStartUsdc) : ""}
      </div>
      <div class="kv"><span class="k">Day change</span><span class="v ${pnlClass(
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
      <div class="book-cards-strip-empty rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm">
        Need DERIBIT_CLIENT_ID/SECRET in <code>.env</code> to load live status.
        Read-only views (closed trades, cumulative PnL) still work below.
      </div>`;
    return;
  }
  const activeSet = new Set(
    Object.keys(status?.portfolio?.equity_by_book || {})
      .map((book) => String(book).toUpperCase())
      .filter((book) => CORE_BOOKS.includes(book))
  );
  const books = CORE_BOOKS.filter((book) => activeSet.size === 0 || activeSet.has(book));
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
              <div class="label">Day change</div>
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

  if (INVESTOR && !isInvestorOverviewDisplayReady()) {
    root.innerHTML = aggregateSkeletonHtml();
    renderDataFreshnessBadge();
    return;
  }

  const { portfolio, source } = resolvedPortfolio();
  const summary = report?.summary;

  if (!portfolio && !summary) {
    if (INVESTOR && (STATE.refreshInFlight || !STATE.investorReady)) {
      root.innerHTML = aggregateSkeletonHtml();
      renderDataFreshnessBadge();
    } else {
      root.innerHTML = `<div class="overview-panel-inner"><p class="text-sm text-slate-400">${i18n(
        "No status / report data yet.",
        "尚無即時帳戶或績效摘要資料。"
      )}</p><div id="overview-freshness-slot" class="overview-freshness-corner"></div></div>`;
    }
    renderDataFreshnessBadge();
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

  const lifetimePnlAtSpot = sumLifetimeRealizedPnlUsdcAtSpot(report, STATE.groups, status);
  const lifetimePnl = lifetimePnlAtSpot ?? num(summary?.realized_pnl_usdc);
  const lifetimeAprAtSpot = computeLifetimeRealizedApr(report, STATE.groups, status, summary);
  const lifetimeApr = lifetimeAprAtSpot ?? num(summary?.lifetime_realized_apr);
  const winRate = num(summary?.realized_win_rate);
  const avgHolding = num(summary?.avg_holding_days);
  const closedCount = num(summary?.realized_closed_group_count);
  const windowDays = num(summary?.window_days_used);
  const windowLabelDaysForPnl = windowDays ?? 30;
  const windowPnlAtSpot = sumWindowRealizedPnlUsdcAtSpot(
    report,
    STATE.groups,
    status,
    windowLabelDaysForPnl
  );
  const windowPnl = windowPnlAtSpot ?? num(summary?.window_realized_pnl_usdc);
  const windowAprAtSpot = computeWindowRealizedApr(
    report,
    STATE.groups,
    status,
    summary,
    windowLabelDaysForPnl
  );
  const windowApr = windowAprAtSpot ?? num(summary?.window_realized_apr);
  const lifetimeStartMs = summary ? lifetimePerformanceStartMs(report, STATE.groups) : null;
  const lifetimeNativeByBook = summary
    ? sumLifetimeRealizedPnlNativeByBook(report, STATE.groups, status)
    : null;
  const windowLabelDays = windowDays ?? 30;
  const windowNativeByBook = summary
    ? sumWindowRealizedPnlNativeByBook(report, STATE.groups, status, windowLabelDays)
    : null;
  const lifetimeProfitDisposition = summary
    ? aggregateProfitDisposition(report, STATE.groups, status)
    : null;
  const profitComposition = summary
    ? profitCompositionByBook(report, STATE.groups, status)
    : null;
  const windowProfitDisposition = summary
    ? aggregateProfitDisposition(report, STATE.groups, status, { windowDays: windowLabelDays })
    : null;
  const { equityNativeByBook, equityUsdByBook } = overviewEquityBreakdown(portfolio, status);
  const sinceLine =
    lifetimeStartMs !== null
      ? `${i18n("since", "自")} ${fmtDate(lifetimeStartMs)}`
      : i18n("no realized history yet", "尚無已實現紀錄");

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
    profitCompositionByBook: profitComposition,
    lifetimeProfitDisposition,
    closedCount,
    windowLabelDays,
    windowPnl,
    windowNativeByBook,
    windowProfitDisposition,
    lifetimeApr,
    windowApr,
    equityNativeByBook,
    equityUsdByBook,
  };
  const contentHtml = overviewDesktopContentHtml(overviewCtx);
  const overviewWrap = (inner) =>
    `<div class="overview-panel-inner">${inner}<div id="overview-freshness-slot" class="overview-freshness-corner"></div></div>`;

  if (INVESTOR) {
    root.innerHTML = overviewWrap(`
      <div class="investor-view-desktop">${contentHtml}</div>
      <div class="investor-view-mobile">${investorOverviewHtml(overviewCtx)}</div>`);
  } else {
    root.innerHTML = overviewWrap(contentHtml);
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
  const ids = new Set(dashboardStrategyIds());
  const summaries = new Map();
  for (const id of ids) summaries.set(id, emptyStrategySummary(id));

  const openRows = currentOpenRows(status, groups);
  for (const g of openRows) {
    const id = strategyId(g);
    if (!isDashboardStrategy(id)) continue;
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
    if (!isDashboardStrategy(id)) continue;
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
    <div class="open-position-metric${secondaryClass}">
      <span class="open-position-label">${label}</span>
      <span class="open-position-value ${extraClass}">${valueHtml}</span>
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

/** One per-currency perp-hedge row: shared across that book's option groups. */
function hedgeSummaryRowHtml(h) {
  const cur = h.currency;
  const isShort = h.side === "short";
  const sideChip = isShort
    ? `<span class="chip chip-warn">${cur} ${i18n("Short", "空")}</span>`
    : `<span class="chip chip-ok">${cur} ${i18n("Long", "多")}</span>`;
  const groupNote = i18n(
    `hedging ${h.optionGroupCount} option group${h.optionGroupCount === 1 ? "" : "s"}`,
    `對沖 ${h.optionGroupCount} 個選擇權群組`
  );
  return `
    <div class="rounded-xl border border-slate-800 bg-slate-800/30 p-3">
      <div class="flex flex-wrap items-center justify-between gap-2 min-w-0">
        <div class="flex items-center gap-2 min-w-0">
          ${sideChip}
          <span class="font-mono text-sm text-slate-300 truncate">${escapeHtml(h.instrumentName)}</span>
        </div>
        <span class="text-[11px] text-slate-500">${escapeHtml(groupNote)}</span>
      </div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2 mt-3">
        <div class="kv"><span class="k">${i18n("Hedge size", "避險部位")}</span><span class="v font-mono tabular-nums">${fmtNum(h.signedSize, 4)} ${cur}</span></div>
        <div class="kv"><span class="k">${i18n("Notional", "名目")}</span><span class="v font-mono tabular-nums">${fmtUsd(h.notionalUsd)}</span></div>
        <div class="kv"><span class="k">${i18n("Hedge PnL", "避險損益")}</span><span class="v font-mono tabular-nums ${pnlClass(h.pnlUsd)}">${h.pnlUsd === null ? "—" : fmtUsd(h.pnlUsd)}</span></div>
        <div class="kv"><span class="k">${i18n("Net incl. hedge", "含避險淨額")}</span><span class="v font-mono tabular-nums ${pnlClass(h.netPnlUsd)}">${h.netPnlUsd === null ? "—" : fmtUsd(h.netPnlUsd)}</span></div>
      </div>
    </div>`;
}

/** Perp-hedge rollup appended below the open-position groups (one row per hedged book). */
export function hedgeSummaryBlockHtml(status, openRows, groups) {
  const rows = activeHedgeSummaryRows(status, openRows, groups);
  if (!rows.length) return "";
  return `
    <div class="rounded-2xl border border-amber-500/30 bg-slate-900/60 shadow overflow-hidden mt-4">
      <div class="flex flex-wrap items-baseline justify-between gap-3 px-4 py-3 border-b border-slate-800 bg-slate-950/40">
        <h3 class="text-sm font-semibold text-slate-200">${i18n("Perp hedge", "永續避險")}</h3>
        <span class="text-xs text-slate-500">${i18n(
          "Shared per book · PnL merged into book strategy total",
          "依幣別共用 · 損益併入該幣別策略合計"
        )}</span>
      </div>
      <div class="p-4 space-y-3">
        ${rows.map(hedgeSummaryRowHtml).join("")}
      </div>
    </div>`;
}

function countActiveStrategyIds(openRows, report, groups) {
  const ids = new Set();
  for (const g of openRows) {
    const id = strategyId(g);
    if (isDashboardStrategy(id)) ids.add(id);
  }
  const noteClosed = (g) => {
    const id = strategyId(g);
    if (isDashboardStrategy(id)) ids.add(id);
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
  const ids = new Set(dashboardStrategyIds());
  for (const g of openRows) {
    const id = strategyId(g);
    if (!isDashboardStrategy(id)) continue;
    if (!byStrategy.has(id)) byStrategy.set(id, []);
    byStrategy.get(id).push(g);
  }
  const groupsHtml = strategyOrder(ids)
    .filter((id) => byStrategy.has(id))
    .map((id) => strategyOpenGroupHtml(id, byStrategy.get(id), status, groups))
    .join("");
  openRoot.innerHTML = groupsHtml + hedgeSummaryBlockHtml(status, openRows, groups);
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
    ? stress.strategy_stresses.filter(Boolean).filter((row) =>
        isDashboardStrategy(row?.option_strategy || row?.strategy_analysis?.label)
      )
    : [];
  if (grouped.length) return grouped;
  const single = stress && isDashboardStrategy(stress.option_strategy || stress.strategy_analysis?.label)
    ? stress
    : null;
  return single ? [single] : [];
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

function transferDirectionLabel(direction) {
  const d = String(direction || "").toLowerCase();
  if (d === "in") return i18n("In", "轉入");
  if (d === "out") return i18n("Out", "轉出");
  return "—";
}

function parseTransferInfoMeta(raw) {
  const text = String(raw || "").trim();
  if (!text.startsWith("{") || !text.endsWith("}")) return null;
  const pick = (field) => {
    const quoted = text.match(new RegExp(`['"]${field}['"]\\s*:\\s*['"]([^'"]*)['"]`, "i"));
    if (quoted) return quoted[1];
    const bare = text.match(new RegExp(`['"]${field}['"]\\s*:\\s*([^,}\\s]+)`, "i"));
    if (bare) return String(bare[1]).replace(/^['"]|['"]$/g, "");
    return "";
  };
  const meta = {
    note: pick("note"),
    otherUser: pick("other_user"),
    otherUserId: pick("other_user_id"),
    transferType: pick("transfer_type"),
  };
  if (!meta.note && !meta.otherUser && !meta.otherUserId && !meta.transferType) return null;
  return meta;
}

function transferInfoLabel(raw, direction) {
  const info = String(raw || "").trim();
  const d = String(direction || "").toLowerCase();
  const meta = parseTransferInfoMeta(info);
  if (meta) {
    const peer = String(meta.otherUser || "").trim();
    const extra = String(meta.note || "").trim();
    const peerId = String(meta.otherUserId || "").trim();
    let label = "";
    if (peer) {
      if (d === "in") label = i18n(`From “${peer}”`, `來自「${peer}」`);
      else if (d === "out") label = i18n(`To “${peer}”`, `轉至「${peer}」`);
      else label = i18n(`With “${peer}”`, `與「${peer}」劃轉`);
    } else if (peerId) {
      if (d === "in") label = i18n(`From sub-account #${peerId}`, `來自子帳戶 #${peerId}`);
      else if (d === "out") label = i18n(`To sub-account #${peerId}`, `轉至子帳戶 #${peerId}`);
      else label = i18n(`Sub-account transfer #${peerId}`, `子帳戶劃轉 #${peerId}`);
    } else if (String(meta.transferType || "").toLowerCase() === "user") {
      if (d === "in") label = i18n("From another sub-account", "來自其他子帳戶");
      else if (d === "out") label = i18n("To another sub-account", "轉至其他子帳戶");
      else label = i18n("Sub-account transfer", "子帳戶劃轉");
    }
    if (label) {
      if (extra) return i18n(`${label} — ${extra}`, `${label}（${extra}）`);
      return label;
    }
  }
  if (!info || info === "in" || info === "out") {
    if (d === "in") return i18n("Received into this account", "資金轉入此帳戶");
    if (d === "out") return i18n("Sent from this account", "資金從此帳戶轉出");
    return i18n("Internal transfer", "內部劃轉");
  }
  const lower = info.toLowerCase();
  if (/sub[- ]?account|subaccount/.test(lower)) {
    return i18n("Moved between sub-accounts", "子帳戶之間移動");
  }
  if (/main[- ]?account|\bmain account\b/.test(lower)) {
    return i18n("Moved with the main account", "與主帳戶之間移動");
  }
  if (/sweep|profit/.test(lower)) {
    return i18n("Profit sweep or internal move", "獲利兌換或內部移轉");
  }
  if (/\bfee\b/.test(lower)) {
    return i18n("Moved to or from the fee account", "與手續費帳戶之間移動");
  }
  if (/margin|cross[- ]?book|currency swap|book transfer/.test(lower)) {
    return i18n("Moved between margin books on the same account", "同一帳戶跨保證金帳本移轉");
  }
  if (/withdraw|deposit/.test(lower)) {
    return i18n("Linked to a deposit or withdrawal flow", "與入金／出金流程相關");
  }
  if (info.startsWith("{") && info.endsWith("}")) {
    return d === "in"
      ? i18n("Received into this account", "資金轉入此帳戶")
      : d === "out"
        ? i18n("Sent from this account", "資金從此帳戶轉出")
        : i18n("Internal transfer", "內部劃轉");
  }
  if (info.length <= 24 && !/\s/.test(info)) {
    return i18n(`Exchange note: ${info}`, `交易所備註：${info}`);
  }
  return info;
}

function transferAccountCardHtml(accountRow, payload) {
  const name = String(accountRow?.name || "account");
  const env = String(accountRow?.env || "");
  const strategy = String(accountRow?.option_strategy || "");
  const books = (accountRow?.books_scanned || accountRow?.traded_collaterals || []).join(", ");
  const transfers = Array.isArray(accountRow?.transfers) ? accountRow.transfers : [];
  const totalCount = num(accountRow?.transfer_count) ?? transfers.length;
  const days = num(payload?.days_requested) ?? 90;
  const truncated = totalCount > transfers.length;
  const rowsHtml = transfers
    .map((row) => {
      const book = String(row?.book || "").toUpperCase();
      const native = num(row?.amount_native);
      const note = transferInfoLabel(row?.info, row?.direction);
      return `
        <tr>
          <td class="px-3 py-2 whitespace-nowrap text-slate-300">${escapeHtml(fmtDate(row?.timestamp_ms))}<span class="block text-[11px] text-slate-500">${escapeHtml(fmtTime(row?.timestamp_ms))}</span></td>
          <td class="px-3 py-2"><span class="transfer-book-chip transfer-book-chip--${book.toLowerCase()}">${escapeHtml(book)}</span></td>
          <td class="px-3 py-2">${escapeHtml(transferDirectionLabel(row?.direction))}</td>
          <td class="px-3 py-2 text-right font-mono ${pnlClass(native)}">${escapeHtml(fmtNativeBookAmount(native, book))}</td>
          <td class="px-3 py-2 text-slate-300 text-xs leading-relaxed">${escapeHtml(note)}</td>
        </tr>`;
    })
    .join("");
  return `
    <article class="transfer-account-card rounded-2xl border border-slate-800 bg-slate-900/60 shadow overflow-hidden">
      <header class="transfer-account-card-head px-4 py-3 border-b border-slate-800/80">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h3 class="text-sm font-semibold tracking-wide text-slate-100">${escapeHtml(name)}</h3>
            <p class="text-xs text-slate-500 mt-1 break-all">${escapeHtml(env)}</p>
          </div>
          <div class="flex flex-wrap justify-end gap-1">${strategy ? strategyChipHtml(strategy) : ""}</div>
        </div>
        <p class="text-xs text-slate-500 mt-2">
          ${i18n("Tracked assets", "追蹤資產")}: ${escapeHtml(books || "—")}
          · ${i18n(`${totalCount} transfer${totalCount === 1 ? "" : "s"} in ${days}d`, `${days} 日內 ${totalCount} 筆劃轉`)}
          ${truncated ? i18n(` (showing ${transfers.length})`, `（顯示 ${transfers.length} 筆）`) : ""}
        </p>
      </header>
      <div class="overflow-x-auto">
        <table class="transfer-table w-full text-sm">
          <thead class="text-xs uppercase tracking-wide text-slate-500 bg-slate-950/40">
            <tr>
              <th class="text-left px-3 py-2">${i18n("Time", "時間")}</th>
              <th class="text-left px-3 py-2">${i18n("Asset", "資產")}</th>
              <th class="text-left px-3 py-2">${i18n("Direction", "方向")}</th>
              <th class="text-right px-3 py-2">${i18n("Amount", "數量")}</th>
              <th class="text-left px-3 py-2">${i18n("Note", "說明")}</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-800">
            ${rowsHtml || `<tr><td colspan="5" class="px-3 py-4 text-center text-slate-500">${i18n("No transfers in this window.", "此期間無劃轉紀錄。")}</td></tr>`}
          </tbody>
        </table>
      </div>
    </article>
  `;
}

export function renderTransferCards(payload) {
  const root = document.getElementById("transfer-cards");
  if (!root) return;
  if (!document.getElementById("transfers-section")?.open) return;
  const data = payload ?? STATE.transfers;
  if (!data) {
    if (STATE.transfersLoadInFlight || STATE.health?.has_private_creds) {
      root.innerHTML = `<p class="text-slate-500 text-sm">${i18n("Loading transfer history…", "正在載入劃轉紀錄…")}</p>`;
    } else {
      root.innerHTML = `<p class="text-sm text-slate-400">${i18n(
        "Set DERIBIT_CLIENT_ID and DERIBIT_CLIENT_SECRET to load transfer history.",
        "請設定 DERIBIT_CLIENT_ID 與 DERIBIT_CLIENT_SECRET 以載入劃轉紀錄。"
      )}</p>`;
    }
    return;
  }
  const accounts = Array.isArray(data.accounts) ? data.accounts : [];
  if (!accounts.length) {
    root.innerHTML = `<div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm">${i18n(
      "No dashboard accounts with API credentials.",
      "尚無具 API 憑證的儀表板帳戶。"
    )}</div>`;
    return;
  }
  root.innerHTML = accounts.map((row) => transferAccountCardHtml(row, data)).join("");
}
