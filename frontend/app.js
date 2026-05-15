// Local dashboard logic.
// Hits the FastAPI endpoints exposed by deribit_demo/frontend_server.py and
// renders cards / tables / Chart.js charts. No build step.

(() => {
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
  const FRONTEND_REFRESH_INTERVAL_MS = 60_000;
  /** Max concurrent /api/* fetches per refresh wave (after spot + health). */
  const FRONTEND_API_CONCURRENCY = 2;
  const FETCH_JSON_RETRYABLE_STATUS = new Set([502, 503, 504]);
  const FETCH_JSON_MAX_RETRIES = 2;
  const FETCH_JSON_RETRY_BASE_MS = 450;

  const STRATEGIES = [
    {
      id: "naked_short",
      title: "Naked Short",
      short: "Naked Short",
      accentClass: "strategy-card-put",
      description: "Single-leg short option (put / call / both) with uncapped tail risk on the chosen side.",
    },
    {
      id: "bull_put_spread",
      title: "Bull Put Spread",
      short: "Put Spread",
      accentClass: "strategy-card-spread",
      description: "Short put paired with a lower-strike long put protection leg.",
    },
    {
      id: "covered_call",
      title: "Covered Call",
      short: "Covered Call",
      accentClass: "strategy-card-call",
      description: "Short call backed by existing BTC/ETH spot collateral.",
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
    bookFilter: "ALL",
    aprWindow: 30,
    charts: {},
    autoRefreshHandle: null,
    refreshInFlight: false,
    lastRefreshStartedMs: 0,
    statusErrorOnce: false,
    /** Last known positive BTC/ETH index (USD) for native unrealized fallback. */
    lastUnderlyingIndexUsd: {},
    /** Latest ``/api/spot`` (BTC/ETH USD index) for header + PNL USD fallback. */
    lastSpotUsd: { BTC: null, ETH: null },
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
  function fmtShortAmountDisplay(g, status) {
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

  function enrichOpenGroupRow(status, g) {
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
        if (missingFpl) fpl = p.floating_profit_loss;
        if (hasFpl === undefined) hasFpl = p.has_floating_profit_loss;
        if (missingFplUsd) fplUsd = p.floating_profit_loss_usd;
        if (hasFplUsd === undefined) hasFplUsd = p.has_floating_profit_loss_usd;
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

  function openRowLegSignedSizeForDisplay(g, status, role) {
    const p = openRowLegPosition(status, g, role);
    const signed = openRowPositionSignedSizeForDisplay(p);
    if (signed !== null) return signed;
    const q = num(g.quantity);
    if (q === null) return null;
    return role === "short" ? -Math.abs(q) : Math.abs(q);
  }

  function openRowLegFieldValue(g, status, role, fieldName) {
    if (role === "short" && hasOwn(g, `short_${fieldName}`)) {
      const v = g[`short_${fieldName}`];
      if (v !== null && v !== undefined && v !== "") return v;
    }
    const p = openRowLegPosition(status, g, role);
    return p?.[fieldName] ?? null;
  }

  function openRowLegPremiumMtmNative(status, g, role) {
    const avg = num(openRowLegFieldValue(g, status, role, "average_price"));
    const mrk = num(openRowLegFieldValue(g, status, role, "mark_price"));
    const sz = openRowLegSignedSizeForDisplay(g, status, role);
    if (avg === null || mrk === null || sz === null) return null;
    return (mrk - avg) * sz;
  }

  function openRowLegPnlUsd(status, g, groups, role) {
    const native = openRowLegPremiumMtmNative(status, g, role);
    if (native === null) return null;
    const spot = openRowSpotUsdScalarForBook(g, status, groups);
    if (spot === null || spot <= 0) return null;
    return native * spot;
  }

  function openRowPositionPremiumMtmNative(status, g) {
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

  function portfolioDayPnlUsdForDisplay(portfolio, totalEquity, dayStart) {
    return (
      num(portfolio?.day_pnl_usdc_ex_flow_ex_spot) ??
      num(portfolio?.day_pnl_usdc_ex_flow) ??
      (totalEquity !== null && dayStart !== null ? totalEquity - dayStart : null)
    );
  }

  function bookDayPnlUsdForDisplay(book, status, equityUsdc, dayStartUsdc) {
    const b = String(book || "").toUpperCase();
    const portfolio = status?.portfolio || {};
    return (
      num(portfolio?.day_pnl_usdc_ex_flow_ex_spot_by_book?.[b]) ??
      num(portfolio?.day_pnl_usdc_ex_flow_by_book?.[b]) ??
      (equityUsdc !== null && dayStartUsdc !== null ? equityUsdc - dayStartUsdc : null)
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
    if (STRATEGY_BY_ID[key]) return STRATEGY_BY_ID[key];
    const label = key ? key.replaceAll("_", " ") : "—";
    return {
      id: key || "",
      title: label,
      short: label,
      accentClass: "border-slate-700",
      description: "",
    };
  }

  function strategyTitle(id) {
    return strategyInfo(id).title;
  }

  function strategyChipHtml(id) {
    const info = strategyInfo(id);
    const cls = STRATEGY_BY_ID[info.id] ? "chip-muted" : "chip-warn";
    return `<span class="chip ${cls}">${escapeHtml(info.short)}</span>`;
  }

  function tradeGroupKey(g) {
    return [
      String(g?.account_name || ""),
      String(g?.group_id || ""),
      String(g?.short_instrument_name || ""),
    ].join("\u0000");
  }

  function dedupeTradeGroups(rows) {
    const seen = new Set();
    const out = [];
    for (const g of rows || []) {
      const key = tradeGroupKey(g);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(g);
    }
    return out;
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
    return out.map((g) => enrichOpenGroupRow(status, g));
  }

  function mergedClosedRows(report, groups, limit = 20) {
    const rows = dedupeTradeGroups([
      ...(report?.recent_closed_trades || []),
      ...(groups?.closed || []),
    ]).filter(isClosedTradeGroup);
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
    if (longLeg) return `Long ${longLeg}`;
    const covered = num(g?.covered_underlying_quantity);
    if (covered !== null && covered > 0) {
      return `Covered ${fmtNum(covered, 4)} ${String(g.currency || "").toUpperCase()}`;
    }
    return "Single short leg";
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

  /** Entry net APR: persisted at open, else estimate from entry credit / book equity / entry DTE. */
  function groupEntryNetApr(g, status) {
    const stored = num(g?.entry_net_apr);
    if (stored !== null && stored > 0) return stored;
    const credit = num(g?.entry_credit);
    const dte = groupEntryDteDaysAtOpen(g);
    const book = String(g.collateral_currency || g.currency || "USDC").toUpperCase();
    const equity = bookEquityNative(status, book);
    if (credit === null || dte === null || dte <= 0 || equity === null || equity <= 0) return null;
    let netCredit = credit;
    let capital = equity;
    if (book !== "USDC") {
      const idx =
        num(status?.underlying_index_usd?.[book]) ?? num(STATE.groups?.underlying_index_usd?.[book]) ?? num(STATE.lastSpotUsd?.[book]);
      if (idx === null || idx <= 0) return null;
      netCredit = credit / idx;
      capital = equity;
    }
    return (netCredit / capital) * (365 / dte);
  }

  function groupEntryFeeUsd(g) {
    return num(g?.entry_fee);
  }

  function groupCloseFeeUsd(g) {
    const openEst = num(g?.current_close_fee);
    if (openEst !== null && openEst > 0) return openEst;
    return num(g?.realized_close_fee);
  }

  function groupEntryCreditUsd(g, status, groups) {
    return openRowEntryCreditUsd(g, status, groups);
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

  function recentOpenedRows(status, groups, limit = 10) {
    return dedupeTradeGroups(allTradeGroupsForActivity(status, groups))
      .slice()
      .sort((a, b) => (entryTimestampMs(b) || 0) - (entryTimestampMs(a) || 0))
      .slice(0, limit);
  }

  function recentClosedRows(report, groups, limit = 10) {
    return mergedClosedRows(report, groups, limit);
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

  function activityRowHtml(g, status, groups, { kind }) {
    const id = strategyId(g);
    const book = String(g.collateral_currency || g.currency || "—").toUpperCase();
    const entryApr = groupEntryNetApr(g, status);
    const entryFee = groupEntryFeeUsd(g);
    const closeFee = groupCloseFeeUsd(g);
    const credit = num(g.entry_credit);
    const ts =
      kind === "closed" ? closedTimestampMs(g) : entryTimestampMs(g);
    const tsLabel = kind === "closed" ? "Closed" : "Opened";
    const pnl = num(g.realized_pnl);
    const holding = groupHoldingDays(g);
    const title = tradeGroupActivityTitle(g);
    const meta = [
      [`${tsLabel}`, fmtTime(ts)],
      entryApr !== null ? ["Net APR", fmtPct(entryApr, 1)] : null,
      entryFee !== null ? ["Entry fee", fmtUsd(entryFee)] : null,
      closeFee !== null && kind === "closed" ? ["Close fee", fmtUsd(closeFee)] : null,
      closeFee !== null && kind === "open" ? ["Est. close fee", fmtUsd(closeFee)] : null,
      credit !== null ? ["Credit", fmtUsd(credit)] : null,
      kind === "closed" && pnl !== null ? ["PnL", fmtUsd(pnl)] : null,
      kind === "closed" && holding !== null ? ["Held", `${fmtNum(holding, 1)}d`] : null,
    ];
    return `
      <li class="activity-row">
        <div class="activity-row-head">
          ${strategyChipHtml(id)}
          <span class="activity-row-title">${escapeHtml(title)}</span>
          <span class="text-[11px] text-slate-500">${escapeHtml(book)}</span>
          ${
            accountHint(g)
              ? `<span class="text-[11px] text-slate-500">${escapeHtml(accountHint(g))}</span>`
              : ""
          }
        </div>
        <div class="activity-row-meta">${activityDetailLine(meta)}</div>
        <div class="font-mono text-[10px] text-slate-600 mt-1 break-all">${escapeHtml(
          g.short_instrument_name || ""
        )}</div>
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
    const maxAttempts = FETCH_JSON_MAX_RETRIES + 1;
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      let res;
      try {
        res = await fetch(url, options);
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

  function renderTopBar(health) {
    if (!health) return;
    const env = (health.env || "").toLowerCase();
    const envBadge = document.getElementById("env-badge");
    envBadge.textContent = `env: ${env || "?"}`;
    envBadge.className =
      "text-xs px-2 py-0.5 rounded-full border " +
      (env === "mainnet"
        ? "border-rose-500/50 bg-rose-500/10 text-rose-200"
        : "border-emerald-500/50 bg-emerald-500/10 text-emerald-200");

    const strategyBadge = document.getElementById("strategy-badge");
    if (strategyBadge) {
      const strategy = normalizeStrategyId(health.option_strategy || "");
      const accountCount = health.accounts?.length || 0;
      strategyBadge.textContent = health.multi_account
        ? `strategy: multi (${accountCount} accounts)`
        : `strategy: ${strategy ? strategyTitle(strategy) : "?"}`;
      strategyBadge.className =
        "text-xs px-2 py-0.5 rounded-full border border-sky-500/50 bg-sky-500/10 text-sky-200";
    }

    const credsBadge = document.getElementById("creds-badge");
    credsBadge.textContent = health.has_private_creds
      ? "creds: ok"
      : "creds: missing";
    credsBadge.className =
      "text-xs px-2 py-0.5 rounded-full border " +
      (health.has_private_creds
        ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-200"
        : "border-rose-500/50 bg-rose-500/10 text-rose-200");

    const sched = document.getElementById("scheduler-badge");
    if (health.scheduler_running) {
      const sec = health.snapshot_interval_sec || 300;
      const min = Math.round(sec / 60);
      sched.textContent = `scheduler: on (every ${min} min)`;
      sched.className =
        "text-xs px-2 py-0.5 rounded-full border border-emerald-500/50 bg-emerald-500/10 text-emerald-200";
    } else {
      sched.textContent = "scheduler: off";
      sched.className =
        "text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-700/30 text-slate-300";
    }
  }

  function renderRegime(status) {
    const badge = document.getElementById("regime-badge");
    const regime = status?.portfolio?.regime || "?";
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
    const portfolio = status?.portfolio;
    const summary = report?.summary;

    if (!portfolio && !summary) {
      root.innerHTML = `<p class="text-sm text-slate-400">No status / report data yet.</p>`;
      return;
    }

    const totalEquity = num(portfolio?.total_equity_usdc);
    const dayStart = num(portfolio?.day_start_equity_usdc);
    const dayPnl = portfolioDayPnlUsdForDisplay(portfolio, totalEquity, dayStart);
    const dayDrawdown = num(portfolio?.day_drawdown_pct);
    const projected = num(portfolio?.projected_max_profit_apr);
    const targetProgress = num(portfolio?.target_progress_ratio);
    const openRows = currentOpenRows(status, STATE.groups);
    const openCredit = openRows.reduce(
      (sum, g) => sum + (openRowEntryCreditUsd(g, status, STATE.groups) || 0),
      0
    );
    const openUnrealized = openRows.reduce((sum, g) => {
      const v = openRowDisplayUnrealizedUsd(g, status, STATE.groups);
      return sum + (v ?? 0);
    }, 0);

    const effectiveCap = num(summary?.effective_capital_usdc);
    const lifetimePnl = num(summary?.realized_pnl_usdc);
    const lifetimeApr = num(summary?.lifetime_realized_apr);
    const winRate = num(summary?.realized_win_rate);
    const avgHolding = num(summary?.avg_holding_days);
    const closedCount = num(summary?.realized_closed_group_count);
    const windowDays = num(summary?.window_days_used);
    const windowPnl = num(summary?.window_realized_pnl_usdc);
    const windowApr = num(summary?.window_realized_apr);
    const targetApr = num(summary?.target_portfolio_apr);

    root.innerHTML = `
      <div class="grid grid-cols-2 md:grid-cols-4 gap-y-5 gap-x-6">
        <div>
          <div class="text-xs text-slate-400">Total equity</div>
          <div class="text-2xl font-mono">${fmtUsd(totalEquity)}</div>
          <div class="text-xs text-slate-500">day-start ${fmtUsd(dayStart)}</div>
        </div>
        <div>
          <div class="text-xs text-slate-400">Day P&amp;L</div>
          <div class="text-2xl font-mono ${pnlClass(dayPnl)}">${fmtUsd(dayPnl)}</div>
          <div class="text-xs text-slate-500">drawdown ${fmtPct(dayDrawdown)}</div>
        </div>
        <div>
          <div class="text-xs text-slate-400">Open credit</div>
          <div class="text-2xl font-mono">${fmtUsd(openCredit)}</div>
          <div class="text-xs text-slate-500">unrealized ${fmtUsd(openUnrealized)}</div>
        </div>
        <div>
          <div class="text-xs text-slate-400">Projected APR (open)</div>
          <div class="text-2xl font-mono">${fmtPct(projected)}</div>
          <div class="text-xs text-slate-500">target progress ${fmtPct(targetProgress)}</div>
        </div>

        <div>
          <div class="text-xs text-slate-400">Total profit (lifetime)</div>
          <div class="text-2xl font-mono ${pnlClass(lifetimePnl)}">${fmtUsd(lifetimePnl)}</div>
          <div class="text-xs text-slate-500">${closedCount ?? 0} closed groups</div>
        </div>
        <div>
          <div class="text-xs text-slate-400">Lifetime APR</div>
          <div class="text-2xl font-mono">${fmtPct(lifetimeApr)}</div>
          <div class="text-xs text-slate-500">target ${fmtPct(targetApr)}</div>
        </div>
        <div>
          <div class="text-xs text-slate-400">${windowDays ?? 30}d realized</div>
          <div class="text-2xl font-mono ${pnlClass(windowPnl)}">${fmtUsd(windowPnl)}</div>
          <div class="text-xs text-slate-500">window APR ${fmtPct(windowApr)}</div>
        </div>
        <div>
          <div class="text-xs text-slate-400">Win rate · avg holding</div>
          <div class="text-2xl font-mono">${fmtPct(winRate, 1)} · ${fmtNum(avgHolding, 2)}d</div>
          <div class="text-xs text-slate-500">effective capital ${fmtUsd(effectiveCap)}</div>
        </div>
      </div>
    `;
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

  function closedAnnualizedWeight(g, ann, pnl, holding) {
    if (holding !== null && holding > 0) {
      if (pnl !== null && ann !== null && ann !== 0 && pnl !== 0) {
        const capitalDays = (pnl * 365) / ann;
        if (Number.isFinite(capitalDays) && capitalDays > 0) return capitalDays;
      }
      const maxLoss = num(g.max_loss);
      if (maxLoss !== null && maxLoss > 0) return maxLoss * holding;
    }
    return null;
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

  function closedAnnualizedEquityDaysWeight(g, status, holding) {
    if (holding === null || holding <= 0) return null;
    const book = String(g.collateral_currency || g.currency || "USDC").toUpperCase();
    const equityUsd = closedBookEquityUsd(status, book);
    if (equityUsd === null || equityUsd <= 0) return null;
    return equityUsd * holding;
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
      const pnl = num(g.realized_pnl);
      if (pnl !== null) {
        s.realizedPnl += pnl;
        if (pnl > 0) s.wins += 1;
      }
      const holding = groupHoldingDays(g);
      if (holding !== null) {
        s.holdingSum += holding;
        s.holdingCount += 1;
      }
      const tableAnn = closedAnnualizedReturnOnEquity(g, status);
      const ann = tableAnn ?? num(g.realized_annualized_return);
      if (ann !== null) {
        s.annualizedSum += ann;
        s.annualizedCount += 1;
        const weight =
          tableAnn !== null
            ? closedAnnualizedEquityDaysWeight(g, status, holding)
            : closedAnnualizedWeight(g, ann, pnl, holding);
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
    const avgAnn =
      summary.annualizedCount > 0 ? summary.annualizedSum / summary.annualizedCount : null;
    const weightedAnn =
      summary.annualizedWeight > 0 ? summary.annualizedWeightedSum / summary.annualizedWeight : avgAnn;
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
            <div class="label">Open groups</div>
            <div class="value">${summary.openCount}</div>
          </div>
          <div class="stat-tile">
            <div class="label">Realized APR</div>
            <div class="value">${fmtPct(weightedAnn, 1)}</div>
          </div>
          <div class="stat-tile">
            <div class="label">Unrealized P&amp;L</div>
            <div class="value ${pnlClass(summary.unrealizedUsd)}">${fmtUsd(summary.unrealizedUsd)}</div>
          </div>
          <div class="stat-tile">
            <div class="label">Realized P&amp;L</div>
            <div class="value ${pnlClass(summary.realizedPnl)}">${fmtUsd(summary.realizedPnl)}</div>
          </div>
          <div class="stat-tile">
            <div class="label">Win rate</div>
            <div class="value">${fmtPct(winRate, 1)}</div>
          </div>
          <div class="stat-tile">
            <div class="label">Avg holding</div>
            <div class="value">${avgHolding === null ? "—" : fmtNum(avgHolding, 2) + "d"}</div>
          </div>
        </div>
        <div class="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
          <span>${summary.closedCount} closed · books ${escapeHtml(books)}</span>
          <span>weighted annualized ${fmtPct(weightedAnn, 1)}</span>
        </div>
      </div>
    `;
  }

  function bullPutLegMiniCardHtml(g, status, groups, role) {
    const instrument = openRowLegInstrumentName(g, role);
    const title = role === "short" ? "Short leg" : "Long leg";
    const badgeClass = role === "short" ? "chip-warn" : "chip-ok";
    const amount = openRowLegSignedSizeForDisplay(g, status, role);
    const strike = openRowLegStrike(g, role);
    const avg = openRowLegFieldValue(g, status, role, "average_price");
    const mark = openRowLegFieldValue(g, status, role, "mark_price");
    const legPnlUsd = openRowLegPnlUsd(status, g, groups, role);
    const coll = openRowBookCollateralUpper(g) || g.collateral_currency || "";
    return `
      <div class="rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="chip ${badgeClass}">${title}</span>
          <span class="font-mono text-xs">${amount === null ? "—" : fmtNum(amount, 4)}</span>
        </div>
        <div class="font-mono text-[11px] break-all text-slate-200 mt-2">${escapeHtml(instrument || "—")}</div>
        <div class="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
          <span class="text-slate-500">Strike</span>
          <span class="text-right font-mono">${fmtStrike(strike)}</span>
          <span class="text-slate-500">Avg</span>
          <span class="text-right font-mono">${fmtDeribitPriceCell(avg, coll)}</span>
          <span class="text-slate-500">Mark</span>
          <span class="text-right font-mono">${fmtDeribitPriceCell(mark, coll)}</span>
          <span class="text-slate-500">Leg PNL</span>
          <span class="text-right font-mono ${pnlClass(legPnlUsd)}">${legPnlUsd === null ? "—" : fmtUsd(legPnlUsd)}</span>
        </div>
      </div>`;
  }

  function bullPutSpreadLegPriceStackHtml(g, status, coll, fieldName) {
    const shortPrice = openRowLegFieldValue(g, status, "short", fieldName);
    const longPrice = openRowLegFieldValue(g, status, "long", fieldName);
    return `
      <div>S ${fmtDeribitPriceCell(shortPrice, coll)}</div>
      <div class="text-[11px] text-emerald-200/80 mt-1">L ${fmtDeribitPriceCell(longPrice, coll)}</div>`;
  }

  function bullPutSpreadLegPnlStackHtml(shortPnlUsd, longPnlUsd) {
    if (shortPnlUsd === null && longPnlUsd === null) return "";
    return `
      <div class="text-[11px] text-slate-500 mt-1">
        <span class="${pnlClass(shortPnlUsd)}">S ${shortPnlUsd === null ? "—" : fmtUsd(shortPnlUsd)}</span>
        <span class="mx-1">·</span>
        <span class="${pnlClass(longPnlUsd)}">L ${longPnlUsd === null ? "—" : fmtUsd(longPnlUsd)}</span>
      </div>`;
  }

  /**
   * Bull put numeric columns: single line (header = label), full wording in title tooltip.
   */
  function bullPutSpreadValueCellHtml(titleAttr, valueInnerHtml, extraClass = "") {
    return `<td class="px-3 py-3 text-right font-mono align-middle tabular-nums ${extraClass}" title="${escapeHtml(
      titleAttr
    )}">${valueInnerHtml}</td>`;
  }

  function openSpreadLegGridCellHtml(g, status, groups, role) {
    const title = role === "short" ? "Short leg" : "Long leg";
    const chipClass = role === "short" ? "chip-warn" : "chip-ok";
    const instrument = openRowLegInstrumentName(g, role);
    const amount = openRowLegSignedSizeForDisplay(g, status, role);
    const strike = openRowLegStrike(g, role);
    const avg = openRowLegFieldValue(g, status, role, "average_price");
    const mark = openRowLegFieldValue(g, status, role, "mark_price");
    const legPnlUsd = openRowLegPnlUsd(status, g, groups, role);
    const coll = openRowBookCollateralUpper(g) || g.collateral_currency || "";
    return `
      <div class="open-spread-leg-row">
        <div class="open-spread-leg-name">
          <span class="chip ${chipClass}">${title}</span>
          <div class="font-mono text-xs break-all mt-1">${escapeHtml(instrument || "—")}</div>
        </div>
        <div class="open-spread-metric">
          <span class="open-spread-label">Amount</span>
          <span class="open-spread-value">${amount === null ? "—" : fmtNum(amount, 4)}</span>
        </div>
        <div class="open-spread-metric">
          <span class="open-spread-label">Strike</span>
          <span class="open-spread-value">${fmtStrike(strike)}</span>
        </div>
        <div class="open-spread-metric">
          <span class="open-spread-label">Entry</span>
          <span class="open-spread-value">${fmtDeribitPriceCell(avg, coll)}</span>
        </div>
        <div class="open-spread-metric">
          <span class="open-spread-label">Mark</span>
          <span class="open-spread-value">${fmtDeribitPriceCell(mark, coll)}</span>
        </div>
        <div class="open-spread-metric">
          <span class="open-spread-label">Leg PNL</span>
          <span class="open-spread-value ${pnlClass(legPnlUsd)}">${legPnlUsd === null ? "—" : fmtUsd(legPnlUsd)}</span>
        </div>
      </div>`;
  }

  function openSpreadTableRowHtml(g, status, groups) {
    const dteVal = openRowDteDays(g);
    const pnlUsd = openRowDisplayUnrealizedUsd(g, status, groups);
    const coll = openRowBookCollateralUpper(g) || g.collateral_currency || "";
    const strikeWidth = bullPutSpreadWidth(g);
    const entryGap = openRowLegPriceGap(g, status, "average_price");
    const markGap = openRowLegPriceGap(g, status, "mark_price");
    const account = accountHint(g);
    return `
      <tr class="open-spread-table-row">
        <td colspan="10" class="px-3 py-3">
          <div class="open-spread-card">
            <div class="open-spread-summary">
              <div>
                <div class="flex flex-wrap items-center gap-2">
                  ${strategyChipHtml("bull_put_spread")}
                  <span class="font-semibold text-slate-200">${escapeHtml(String(g.currency || "").toUpperCase() || "Spread")} put spread</span>
                  <span class="text-xs text-slate-500">${escapeHtml(coll)} book</span>
                </div>
                <div class="text-xs text-slate-500 mt-1">
                  Strike width ${fmtStrike(strikeWidth)} · entry gap ${fmtDeribitPriceCell(entryGap, coll)} · mark gap ${fmtDeribitPriceCell(markGap, coll)}
                  ${account ? ` · ${escapeHtml(account)}` : ""}
                </div>
              </div>
              <div class="open-spread-summary-metrics">
                <div class="open-spread-metric">
                  <span class="open-spread-label">DTE</span>
                  <span class="open-spread-value">${dteVal !== null ? fmtNum(dteVal, 2) : "—"}</span>
                </div>
                <div class="open-spread-metric">
                  <span class="open-spread-label">Net PNL</span>
                  <span class="open-spread-value ${pnlClass(pnlUsd)}">${pnlUsd === null ? "—" : fmtUsd(pnlUsd)}</span>
                </div>
                <div class="open-spread-metric">
                  <span class="open-spread-label">Credit kept</span>
                  <span class="open-spread-value">${fmtPct(num(g.profit_capture), 1)}</span>
                </div>
              </div>
            </div>
            <div class="open-spread-leg-grid">
              ${openSpreadLegGridCellHtml(g, status, groups, "short")}
              ${openSpreadLegGridCellHtml(g, status, groups, "long")}
            </div>
          </div>
        </td>
      </tr>`;
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
    if (n === null || Math.abs(n) < 0.005) return "Flat";
    return n > 0 ? "In profit" : "Underwater";
  }

  function creditCaptureBarHtml(value) {
    const pct = num(value);
    const width = pct === null ? 0 : Math.max(0, Math.min(100, pct * 100));
    const tone = pct === null ? "bar-muted" : pct >= 0.5 ? "bar-ok" : pct >= 0.15 ? "bar-warn" : "bar-bad";
    return `<span class="credit-capture-bar"><span class="${tone}" style="width:${width}%"></span></span>`;
  }

  function openPositionMetricHtml(label, valueHtml, extraClass = "") {
    return `
      <div class="open-position-metric ${extraClass}">
        <span class="open-position-label">${label}</span>
        <span class="open-position-value">${valueHtml}</span>
      </div>`;
  }

  function openPositionTitle(g) {
    const ccy = String(g.currency || "").toUpperCase() || "Option";
    const id = strategyId(g);
    if (id === "bull_put_spread") return `${ccy} put spread`;
    return `${ccy} short ${optionPutCallLabel(g).toLowerCase()}`;
  }

  function openPositionLegCardHtml(g, status, groups, role) {
    const isShort = role === "short";
    const title = isShort ? `Short ${optionPutCallLabel(g)}` : "Long protection";
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
          ${openPositionMetricHtml("Strike", fmtStrike(strike))}
          ${openPositionMetricHtml("Entry", fmtDeribitPriceCell(avg, coll))}
          ${openPositionMetricHtml("Mark", fmtDeribitPriceCell(mark, coll))}
          ${openPositionMetricHtml(
            "Leg PNL",
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
        <span>Width ${fmtStrike(strikeWidth)}</span>
        <span>Entry gap ${fmtDeribitPriceCell(entryGap, coll)}</span>
        <span>Mark gap ${fmtDeribitPriceCell(markGap, coll)}</span>`;
    }
    return `
      <span>Strike ${fmtStrike(openRowLegStrike(g, "short"))}</span>
      <span>${escapeHtml(strategyLegDetail(g))}</span>`;
  }

  function openPositionCardHtml(g, status, groups) {
    const id = strategyId(g);
    const isBullPutSpread = id === "bull_put_spread";
    const dteVal = openRowDteDays(g);
    const pnlUsd = openRowDisplayUnrealizedUsd(g, status, groups);
    const nativeUnr = openRowDisplayNativeUnrealizedValue(g, status, groups);
    const coll = openRowBookCollateralUpper(g) || g.collateral_currency || "";
    const creditKept = num(g.profit_capture);
    const entryCredit = openRowEntryCreditUsd(g, status, groups);
    const longLeg = openRowLegInstrumentName(g, "long");
    const account = accountHint(g);
    const strategyClass = openPositionStrategyClass(id);
    const toneClass = openPositionToneClass(pnlUsd);
    return `
      <article class="open-position-card ${strategyClass} ${toneClass}">
        <div class="open-position-glow"></div>
        <div class="open-position-header">
          <div class="open-position-main">
            <div class="open-position-title-row">
              ${strategyChipHtml(id)}
              <h3>${escapeHtml(openPositionTitle(g))}</h3>
              <span class="open-book-pill">${escapeHtml(coll)} book</span>
              <span class="open-status-pill">${openPositionStatusLabel(pnlUsd)}</span>
            </div>
            <div class="open-position-instruments">
              <span>${escapeHtml(g.short_instrument_name || "—")}</span>
              ${isBullPutSpread && longLeg ? `<span>Long ${escapeHtml(longLeg)}</span>` : ""}
            </div>
            <div class="open-position-detail-row">
              ${openPositionDetailHtml(g, status, groups)}
              ${account ? `<span>${escapeHtml(account)}</span>` : ""}
            </div>
          </div>
          <div class="open-position-pnl-panel">
            <span class="open-position-label"${
              isBullPutSpread
                ? ' title="Sum of leg mark MTM when both legs load; otherwise engine entry−debit (bid/ask close est.)."'
                : ""
            }>Unrealized PNL</span>
            <strong class="${pnlClass(pnlUsd)}">${pnlUsd === null ? "—" : fmtUsd(pnlUsd)}</strong>
            <span class="open-position-native ${pnlClass(nativeUnr)}">${fmtNativeUnrealizedDisplay(nativeUnr, coll)}</span>
          </div>
        </div>
        <div class="open-position-kpis open-position-kpis-extended">
          ${openPositionMetricHtml("DTE", dteVal !== null ? `${fmtNum(dteVal, 2)}d` : "—")}
          ${openPositionMetricHtml(
            "Credit kept",
            `${fmtPct(creditKept, 1)}${creditCaptureBarHtml(creditKept)}`
          )}
          ${openPositionMetricHtml("Entry credit", entryCredit === null ? "—" : fmtUsd(entryCredit))}
          ${(() => {
            const entryApr = groupEntryNetApr(g, status);
            const aprClass =
              entryApr !== null && entryApr >= 0.15 ? "pnl-pos" : "";
            return openPositionMetricHtml(
              "Entry net APR",
              entryApr === null ? "—" : fmtPct(entryApr, 1),
              aprClass
            );
          })()}
          ${openPositionMetricHtml(
            "Entry fee",
            groupEntryFeeUsd(g) === null ? "—" : fmtUsd(groupEntryFeeUsd(g))
          )}
          ${openPositionMetricHtml(
            "Est. close fee",
            groupCloseFeeUsd(g) === null ? "—" : fmtUsd(groupCloseFeeUsd(g))
          )}
        </div>
        <div class="open-position-legs ${isBullPutSpread ? "has-two-legs" : "has-one-leg"}">
          ${openPositionLegCardHtml(g, status, groups, "short")}
          ${isBullPutSpread ? openPositionLegCardHtml(g, status, groups, "long") : ""}
        </div>
      </article>`;
  }

  function bullPutSpreadOpenPanelHtml(rows, status, groups) {
    const info = strategyInfo("bull_put_spread");
    const body = rows
      .map((g) => {
        const dteVal = openRowDteDays(g);
        const pnlUsd = openRowDisplayUnrealizedUsd(g, status, groups);
        const coll = openRowBookCollateralUpper(g) || g.collateral_currency || "";
        const strikeWidth = bullPutSpreadWidth(g);
        const entryGap = openRowLegPriceGap(g, status, "average_price");
        const markGap = openRowLegPriceGap(g, status, "mark_price");
        return `
          <tr>
            <td class="px-3 py-3 min-w-[24rem]">
              <div class="grid grid-cols-1 xl:grid-cols-2 gap-2">
                ${bullPutLegMiniCardHtml(g, status, groups, "short")}
                ${bullPutLegMiniCardHtml(g, status, groups, "long")}
              </div>
              ${
                accountHint(g)
                  ? `<div class="text-[11px] text-slate-500 mt-2">${escapeHtml(accountHint(g))}</div>`
                  : ""
              }
            </td>
            <td class="px-3 py-3 align-middle">${escapeHtml(coll)}</td>
            ${bullPutSpreadValueCellHtml(
              "Strike width: short strike minus long strike",
              `<span class="text-slate-100">${fmtStrike(strikeWidth)}</span>`
            )}
            ${bullPutSpreadValueCellHtml(
              "Entry gap: short average minus long average",
              fmtDeribitPriceCell(entryGap, coll)
            )}
            ${bullPutSpreadValueCellHtml(
              "Mark gap: short mark minus long mark",
              fmtDeribitPriceCell(markGap, coll)
            )}
            ${bullPutSpreadValueCellHtml(
              "Net credit received at entry (after fees)",
              fmtUsd(openRowEntryCreditUsd(g, status, groups))
            )}
            ${bullPutSpreadValueCellHtml(
              "Estimated debit to close (bid/ask + fees)",
              fmtUsd(g.current_debit)
            )}
            <td
              class="px-3 py-3 text-right font-mono align-middle text-base tabular-nums ${pnlClass(pnlUsd)}"
              title="${escapeHtml(
                [
                  "Unrealized P&L",
                  `${fmtPct(num(g.profit_capture), 1)} kept`,
                  dteVal !== null ? `${fmtNum(dteVal, 2)} DTE` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")
              )}"
            >
              ${pnlUsd === null ? "—" : fmtUsd(pnlUsd)}
            </td>
          </tr>`;
      })
      .join("");

    return `
      <div class="overflow-x-auto rounded-2xl border ${info.accentClass} bg-slate-900/60">
        <div class="flex flex-wrap items-baseline justify-between gap-2 px-4 py-3 border-b border-slate-800">
          <div class="flex items-center gap-2">
            <h3 class="text-sm font-semibold text-slate-200">${escapeHtml(info.title)} open positions</h3>
            ${strategyChipHtml("bull_put_spread")}
          </div>
          <span class="text-xs text-slate-500">${rows.length} open · Unrealized = leg mark MTM sum when both legs load; else engine close est. (bid/ask)</span>
        </div>
        <table class="w-full text-sm">
          <thead class="bg-slate-900/80 text-slate-400">
            <tr>
              <th class="text-left px-3 py-2 min-w-[24rem]">Short / long legs</th>
              <th class="text-left px-3 py-2">Book</th>
              <th class="text-right px-3 py-2 whitespace-nowrap">Spread</th>
              <th class="text-right px-3 py-2 whitespace-nowrap">Avg gap</th>
              <th class="text-right px-3 py-2 whitespace-nowrap">Mark gap</th>
              <th class="text-right px-3 py-2 whitespace-nowrap">Net</th>
              <th class="text-right px-3 py-2 whitespace-nowrap">Close</th>
              <th class="text-right px-3 py-2 whitespace-nowrap">MTM</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-800">${body}</tbody>
        </table>
      </div>
    `;
  }

  function strategyOpenPanelHtml(id, rows, status, groups) {
    const normalizedId = normalizeStrategyId(id) || id;
    const spreadRows =
      rows?.length &&
      rows.every(
        (g) =>
          strategyId(g) === "bull_put_spread" ||
          (String(g?.long_instrument_name || "").trim() && optionPutCallLabel(g).toLowerCase() === "put")
      );
    if (normalizedId === "bull_put_spread" || spreadRows) {
      return bullPutSpreadOpenPanelHtml(rows, status, groups);
    }
    const info = strategyInfo(normalizedId);
    const body = rows
      .map((g) => {
        const dteVal = openRowDteDays(g);
        const pnlUsd = openRowDisplayUnrealizedUsd(g, status, groups);
        const coll = openRowBookCollateralUpper(g) || g.collateral_currency || "";
        return `
          <tr>
            <td class="px-3 py-2">
              <div class="font-mono text-[11px] break-all max-w-[18rem]">${escapeHtml(g.short_instrument_name || "")}</div>
              <div class="text-[11px] text-slate-500 mt-1">${escapeHtml(
                [accountHint(g), strategyLegDetail(g)].filter(Boolean).join(" · ")
              )}</div>
            </td>
            <td class="px-3 py-2">${escapeHtml(coll)}</td>
            <td class="px-3 py-2 text-right font-mono">${fmtShortAmountDisplay(g, status)}</td>
            <td class="px-3 py-2 text-center font-mono text-xs">${escapeHtml(optionPutCallLabel(g))}</td>
            <td class="px-3 py-2 text-right font-mono">${dteVal !== null ? fmtNum(dteVal, 2) : "—"}</td>
            <td class="px-3 py-2 text-right font-mono ${pnlClass(pnlUsd)}">${pnlUsd === null ? "—" : fmtUsd(pnlUsd)}</td>
            <td class="px-3 py-2 text-right font-mono">${fmtUsd(openRowEntryCreditUsd(g, status, groups))}</td>
            <td class="px-3 py-2 text-right font-mono">${fmtPct(num(g.profit_capture), 1)}</td>
          </tr>`;
      })
      .join("");

    return `
      <div class="overflow-x-auto rounded-2xl border ${info.accentClass} bg-slate-900/60">
        <div class="flex flex-wrap items-baseline justify-between gap-2 px-4 py-3 border-b border-slate-800">
          <div class="flex items-center gap-2">
            <h3 class="text-sm font-semibold text-slate-200">${escapeHtml(info.title)} open positions</h3>
            ${strategyChipHtml(normalizedId)}
          </div>
          <span class="text-xs text-slate-500">${rows.length} open</span>
        </div>
        <table class="w-full text-sm">
          <thead class="bg-slate-900/80 text-slate-400">
            <tr>
              <th class="text-left px-3 py-2 min-w-[14rem]">Instrument / leg</th>
              <th class="text-left px-3 py-2">Book</th>
              <th class="text-right px-3 py-2">Amount</th>
              <th class="text-center px-3 py-2">Put / Call</th>
              <th class="text-right px-3 py-2">DTE</th>
              <th class="text-right px-3 py-2">PNL（USD）</th>
              <th class="text-right px-3 py-2">Entry credit</th>
              <th class="text-right px-3 py-2">Credit kept</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-800">${body}</tbody>
        </table>
      </div>
    `;
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
      `${totalOpen} open · ${totalClosed} closed · ${activeStrategies || 0} active strategy groups`
    );

    if (cardsRoot) {
      cardsRoot.innerHTML = summaries.map(strategySummaryCardHtml).join("");
    }

    if (!openRoot) return;
    if (!openRows.length) {
      openRoot.innerHTML = `
        <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-sm text-slate-400">
          No open strategy positions.
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
      .map((id) => strategyOpenPanelHtml(id, byStrategy.get(id), status, groups))
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
    if (STATE.charts[key]) {
      STATE.charts[key].destroy();
      STATE.charts[key] = null;
    }
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

  function renderRiskVsCapitalChart() {
    const ctx = document.getElementById("chart-risk-capital")?.getContext("2d");
    if (!ctx) return;
    destroyChart("riskCapital");

    const books = visibleBooks();
    const portfolio = STATE.status?.portfolio;
    const openGroups = openTradeGroupsForRisk();
    const creditByBook = sumOpenCreditByBook(openGroups);

    const equityBars = books.map((b) => {
      const v = bookEquityUsdForDisplay(b, STATE.status);
      return v !== null ? v : 0;
    });
    const creditBars = books.map((b) => creditByBook[b] || 0);

    const totEq = num(portfolio?.total_equity_usdc);
    const totalCredit = books.reduce((sum, b) => sum + (creditByBook[b] || 0), 0);

    const nOpen = openGroups.length;
    let meta = `${nOpen} open group${nOpen === 1 ? "" : "s"}`;
    if (portfolio && totEq !== null) {
      const creditPct = totEq > 0 ? totalCredit / totEq : null;
      meta += ` · open credit ${fmtUsd(totalCredit)} (${fmtPct(creditPct, 2)} of equity)`;
    } else if (!STATE.status && nOpen > 0) {
      meta += " · book equity needs live /api/status";
    }

    setText("risk-capital-meta", meta);

    const barColors = books.map((b) => BOOK_COLORS[b] || "#94a3b8");
    const baseOpts = riskBarChartBaseOptions();

    STATE.charts.riskCapital = new Chart(ctx, {
      type: "bar",
      data: {
        labels: books,
        datasets: [
          {
            label: "Book equity (USDC eq.)",
            data: equityBars,
            backgroundColor: barColors.map((c) => c + "55"),
            borderColor: barColors,
            borderWidth: 1,
          },
          {
            label: "Open credit",
            data: creditBars,
            backgroundColor: "rgba(16, 185, 129, 0.36)",
            borderColor: "#34d399",
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
                const credit = creditBars[i] ?? 0;
                const r = eq > 0 ? credit / eq : null;
                const lines = [`Open credit / equity: ${fmtPct(r, 2)}`];
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
    const ctx = document.getElementById("chart-cum-pnl")?.getContext("2d");
    if (!ctx) return;
    destroyChart("cumPnl");
    const series = STATE.cumulativePnl;
    setText(
      "cum-pnl-meta",
      series?.realized_count ? `${series.realized_count} closed groups` : "no closed groups"
    );
    if (!series) return;
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

  function renderDailyPnlChart() {
    const ctx = document.getElementById("chart-daily-pnl")?.getContext("2d");
    if (!ctx) return;
    destroyChart("dailyPnl");
    const MA_WINDOW = 30;
    const series = STATE.cumulativePnl;
    if (!series) return;
    const books = visibleBooks();
    const validDaily = (series.daily_total || []).filter((r) => Number.isFinite(dateToMs(r.date)));
    let meta = series?.daily_total?.length ? `${series.daily_total.length} active days` : "—";
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
          label: "Daily total",
          data: barData,
          order: 1,
          backgroundColor: BOOK_COLORS.TOTAL + "aa",
          borderColor: BOOK_COLORS.TOTAL,
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
            label: `${book} daily`,
            data: barData,
            order: 1,
            backgroundColor: BOOK_COLORS[book] + "aa",
            borderColor: BOOK_COLORS[book],
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
    if (!datasets.length) return;
    const flatPoints = datasets.flatMap((d) => d.data || []);
    const xBounds = suggestTimeScaleMinMax(flatPoints);
    const base = chartCommonOptions();
    STATE.charts.dailyPnl = new Chart(ctx, {
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
    const ctx = document.getElementById("chart-apr")?.getContext("2d");
    if (!ctx) return;
    destroyChart("apr");
    const rows = STATE.aprSeries?.rows || [];
    const data = finalizeSimpleLineData(
      filterValidTimePoints(rows.map((r) => ({ x: dateToMs(r.date), y: num(r.apr) })))
    );
    if (!data.length) return;
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

  function renderOpenTable(status, groups) {
    const root = document.getElementById("open-rows");
    if (!root) return;
    const rows = currentOpenRows(status, groups);
    setText("open-meta", `${rows.length} open`);
    if (!rows.length) {
      root.innerHTML = `<div class="open-empty-state">No open positions</div>`;
      return;
    }
    root.innerHTML = rows.map((g) => openPositionCardHtml(g, status, groups)).join("");
  }

  function renderActivityList(root, rows, status, groups, kind, emptyLabel) {
    if (!root) return;
    if (!rows.length) {
      root.innerHTML = `<li class="activity-empty">${escapeHtml(emptyLabel)}</li>`;
      return;
    }
    const html = [];
    for (const g of rows) {
      try {
        html.push(activityRowHtml(g, status, groups, { kind }));
      } catch (err) {
        console.warn("activity row skipped", g?.group_id, err);
      }
    }
    root.innerHTML = html.length
      ? html.join("")
      : `<li class="activity-empty">${escapeHtml(emptyLabel)}</li>`;
  }

  function renderRecentActivity(status, report, groups) {
    const openedRoot = document.getElementById("recent-opened-list");
    const closedRoot = document.getElementById("recent-closed-list");
    if (!openedRoot && !closedRoot) return;

    const opened = recentOpenedRows(status, groups, 20);
    const closed = recentClosedRows(report, groups, 20);
    setText("activity-meta", `${opened.length} opened · ${closed.length} closed`);

    renderActivityList(openedRoot, opened, status, groups, "open", "No recent opens");
    renderActivityList(closedRoot, closed, status, groups, "closed", "No recent closes");
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
    const pnlUsd = num(g.realized_pnl);
    if (pnlUsd === null) return null;
    const book = String(g.collateral_currency || g.currency || "USDC").toUpperCase();
    if (book === "USDC") return pnlUsd;
    if (book !== "BTC" && book !== "ETH") return pnlUsd;
    const idx =
      num(status?.underlying_index_usd?.[book]) ?? num(STATE.lastSpotUsd?.[book]);
    if (idx === null || idx <= 0) return null;
    return pnlUsd / idx;
  }

  function closedBookTotalEquityNative(status, book) {
    const b = String(book || "USDC").toUpperCase();
    const eq = num(status?.accounts?.[b]?.equity);
    if (eq === null || eq <= 0) return null;
    return eq;
  }

  /** ``365 × pnl_native / (equity_native × holding_days)`` using live book equity from ``/api/status``. */
  function closedAnnualizedReturnOnEquity(g, status) {
    const holding = groupHoldingDays(g);
    if (holding === null || holding <= 0) return null;
    const book = String(g.collateral_currency || g.currency || "USDC").toUpperCase();
    const equity = closedBookTotalEquityNative(status, book);
    if (equity === null) return null;
    const pnlN = closedPnlInBookNativeUnits(g, status);
    if (pnlN === null) return null;
    return (365 * pnlN) / (equity * holding);
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

  async function tickHeaderSpot({ renderDependentViews = true } = {}) {
    try {
      const d = await fetchJson("/api/spot");
      STATE.lastSpotUsd.BTC = num(d.BTC);
      STATE.lastSpotUsd.ETH = num(d.ETH);
      const elBtc = document.getElementById("header-spot-btc");
      const elEth = document.getElementById("header-spot-eth");
      const b = STATE.lastSpotUsd.BTC;
      const e = STATE.lastSpotUsd.ETH;
      if (elBtc) elBtc.textContent = b !== null && b > 0 ? `BTC ${fmt.usd2.format(b)}` : "BTC —";
      if (elEth) elEth.textContent = e !== null && e > 0 ? `ETH ${fmt.usd2.format(e)}` : "ETH —";
      if (renderDependentViews) {
        renderStrategyGroups(STATE.status, STATE.report, STATE.groups);
        renderOpenTable(STATE.status, STATE.groups);
        renderRecentActivity(STATE.status, STATE.report, STATE.groups);
      }
    } catch (_) {
      /* ignore */
    }
  }

  function refreshWaitMs() {
    if (!STATE.lastRefreshStartedMs) return 0;
    return Math.max(0, FRONTEND_REFRESH_INTERVAL_MS - (Date.now() - STATE.lastRefreshStartedMs));
  }

  async function refreshAll({ force = false, silentIfLimited = false } = {}) {
    if (STATE.refreshInFlight) {
      if (!silentIfLimited) showToast("refresh already running");
      return;
    }
    const waitMs = refreshWaitMs();
    if (!force && waitMs > 0) {
      if (!silentIfLimited) showToast(`refresh rate limited; wait ${Math.ceil(waitMs / 1000)}s`);
      return;
    }

    STATE.refreshInFlight = true;
    STATE.lastRefreshStartedMs = Date.now();
    try {
      // Progressive rendering: update UI as each endpoint resolves so the
      // initial paint does not wait on the slowest API call (often /api/stress).
      let renderScheduled = false;
      function scheduleRender() {
        if (renderScheduled) return;
        renderScheduled = true;
        requestAnimationFrame(() => {
          renderScheduled = false;
          updateUnderlyingIndexCache(STATE.status, STATE.groups);
          renderRegime(STATE.status);
          renderAccountCards(STATE.health, STATE.status);
          renderBookCards(STATE.status);
          renderAggregate(STATE.status, STATE.report);
          renderStrategyGroups(STATE.status, STATE.report, STATE.groups);
          renderRiskVsCapitalChart();
          renderCumulativePnlChart();
          renderDailyPnlChart();
          renderAprChart();
          renderOpenTable(STATE.status, STATE.groups);
          renderRecentActivity(STATE.status, STATE.report, STATE.groups);
          renderStress(STATE.stress);
        });
      }

      try {
        await tickHeaderSpot({ renderDependentViews: false });
        STATE.health = await fetchJson("/api/health");
        renderTopBar(STATE.health);
      } catch (err) {
        showToast(`health failed: ${err.message}`);
      }

      const taskFactories = [
        () =>
          fetchJson("/api/groups")
            .then((d) => {
              STATE.groups = d;
              scheduleRender();
            })
            .catch((err) => {
              showToast(`groups: ${err.message}`);
            }),
        () =>
          fetchJson("/api/cumulative_pnl_series")
            .then((d) => {
              STATE.cumulativePnl = d;
              scheduleRender();
            })
            .catch((err) => showToast(`cumulative pnl: ${err.message}`)),
        () =>
          fetchJson(`/api/apr_series?window_days=${STATE.aprWindow}`)
            .then((d) => {
              STATE.aprSeries = d;
              scheduleRender();
            })
            .catch((err) => showToast(`apr series: ${err.message}`)),
      ];

      if (STATE.health?.has_private_creds) {
        taskFactories.push(
          () =>
            fetchJson("/api/status")
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
              }),
          () =>
            fetchJson("/api/report?days=30")
              .then((d) => {
                STATE.report = d;
                scheduleRender();
              })
              .catch((err) => showToast(`report: ${err.message}`)),
          () =>
            fetchJson("/api/stress?shocks=0.1,0.2,0.3,0.4,0.5")
              .then((d) => {
                STATE.stress = d;
                scheduleRender();
              })
              .catch((err) => showToast(`stress: ${err.message}`))
        );
      } else {
        STATE.status = null;
        STATE.report = null;
        STATE.stress = null;
      }

      await promisePool(taskFactories, FRONTEND_API_CONCURRENCY);

      // One final render pass to ensure consistency.
      scheduleRender();

      setText("last-refresh", `last refresh: ${luxon.DateTime.now().toFormat("HH:mm:ss")}`);
    } finally {
      STATE.refreshInFlight = false;
    }
  }

  function setBookFilter(book) {
    STATE.bookFilter = book;
    document.querySelectorAll("#book-filter button").forEach((btn) => {
      btn.classList.toggle("filter-active", btn.dataset.book === book);
    });
    renderRiskVsCapitalChart();
    renderCumulativePnlChart();
    renderDailyPnlChart();
  }

  function attachAutoRefresh() {
    const checkbox = document.getElementById("auto-refresh");
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
    document.getElementById("refresh-now").addEventListener("click", () => refreshAll());
    document.getElementById("book-filter").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-book]");
      if (btn) setBookFilter(btn.dataset.book);
    });
    document.getElementById("apr-window").addEventListener("change", async (e) => {
      STATE.aprWindow = parseInt(e.target.value, 10) || 30;
      try {
        STATE.aprSeries = await fetchJson(
          `/api/apr_series?window_days=${STATE.aprWindow}`
        );
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
        requestAnimationFrame(() => {
          Object.values(STATE.charts).forEach((chart) => chart?.resize?.());
        });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    attachControls();
    attachExpandableSections();
    attachAutoRefresh();
    refreshAll({ force: true });
  });
})();
