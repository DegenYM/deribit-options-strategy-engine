import assert from "node:assert/strict";
import {
  aggregateProfitDisposition,
  annualizeRealizedApr,
  computeLifetimeRealizedApr,
  computeWindowRealizedApr,
  profitCompositionByBook,
  sumLifetimeEarnedUsdByBook,
  sumLifetimeRealizedPnlUsdcByBook,
} from "../../frontend/src/modules/charts.js";
import {
  emptyProfitDisposition,
  fmtProfitAvgUsd,
  fmtProfitNative,
  fmtProfitUsdt,
  groupHasItmSpotExitFills,
  itmFoldedPremiumUsdt,
  itmSpotExitNetUsdtForTotalProfit,
  itmSpotExitPremiumFolded,
  itmSpotExitDisplayNetUsdt,
  itmSpotRoundTripComplete,
  unrestoredSpotExitNative,
  adminGroupNeedsMarketRecover,
  groupHasFilledSpotRestore,
  resolveAdminGroupActionKind,
  fmtRealizedPnlDisplay,
  profitDispositionForGroup,
  profitSweepHasExchangeFill,
  profitSweepExchangeNativeSold,
  profitSweepMetaLine,
  profitSwapDisplayAvg,
  realizedPnlDisplayUsdc,
  resolvePremiumSweepBookDisplay,
  summarizeProfitDisposition,
  summarizeSpotExitDisposition,
  truncateDecimal,
  cspPremiumSwapMetaLine,
  cashSecuredMetaLine,
  cashSecuredWheelPnl,
  fmtCashSecuredPanel,
  summarizeCashSecuredDisposition,
  activityLifecycleCardHtml,
  overviewProfitCompositionHtml,
} from "../../frontend/src/modules/domain.js";

const status = { underlying_index_usd: { BTC: 63000, ETH: 1675 } };

function group(overrides) {
  return {
    group_id: "g1",
    currency: "BTC",
    collateral_currency: "BTC",
    status: "closed",
    strategy: "covered_call",
    option_type: "call",
    realized_pnl_collateral_native: "0.001",
    profit_sweep_status: "",
    profit_sweep_amount: "0",
    profit_sweep_quote_proceeds: "0",
    closed_timestamp_ms: 1,
    realized_pnl: "60",
    ...overrides,
  };
}

const filledPartial = profitDispositionForGroup(
  group({
    profit_sweep_status: "filled",
    profit_sweep_amount: "0.000914",
    profit_sweep_quote_proceeds: "57.5",
  }),
  status,
);
assert.ok(Math.abs(filledPartial.held - 0.000086) < 1e-10);
assert.equal(filledPartial.sweptNative, 0.000914);
assert.equal(filledPartial.pending, 0);

// Dust-padded journal amount must not inflate Sold / shrink Remaining.
const filledDustPadded = profitDispositionForGroup(
  group({
    profit_sweep_status: "filled",
    profit_sweep_amount: "0.0003",
    profit_sweep_exchange_native: "0.0002",
    profit_sweep_exchange_quote_proceeds: "12.5",
    profit_sweep_quote_proceeds: "18.9",
    profit_sweep_reason: "take_profit; dust_pool_sweep; proceeds_reconciled",
    realized_pnl_collateral_native: "0.0003",
  }),
  status,
);
assert.equal(filledDustPadded.sweptNative, 0.0002);
assert.ok(Math.abs(filledDustPadded.held - 0.0001) < 1e-12);
assert.equal(filledDustPadded.sweptUsdt, 12.5);
assert.equal(filledDustPadded.pending, 0);

const resweepPending = profitDispositionForGroup(
  group({
    profit_sweep_status: "pending",
    profit_sweep_amount: "0.000914",
    profit_sweep_quote_proceeds: "57.5",
  }),
  status,
);
assert.ok(Math.abs(resweepPending.pending - 0.000086) < 1e-10);
assert.equal(resweepPending.sweptNative, 0.000914);
assert.equal(resweepPending.held, 0);

const fullQueue = profitDispositionForGroup(
  group({
    profit_sweep_status: "pending",
    profit_sweep_amount: "0.001",
  }),
  status,
);
assert.equal(fullQueue.pending, 0.001);
assert.equal(fullQueue.sweptNative, 0);

const report = { recent_closed_trades: [] };
const groups = {
  closed: [
    group({
      profit_sweep_status: "filled",
      profit_sweep_amount: "0.000914",
      profit_sweep_quote_proceeds: "57.5",
    }),
    group({
      group_id: "g2",
      profit_sweep_status: "filled",
      profit_sweep_amount: "0.0008",
      profit_sweep_quote_proceeds: "50",
      realized_pnl_collateral_native: "0.0008",
    }),
  ],
  open: [],
};

const dustPaddedSummary = summarizeProfitDisposition(
  aggregateProfitDisposition(
    report,
    {
      closed: [
        group({
          group_id: "dust-pad",
          profit_sweep_status: "filled",
          profit_sweep_amount: "0.0003",
          profit_sweep_exchange_native: "0.0002",
          profit_sweep_exchange_quote_proceeds: "12.5",
          profit_sweep_quote_proceeds: "18.9",
          profit_sweep_reason: "take_profit; dust_pool_sweep; proceeds_reconciled",
          realized_pnl_collateral_native: "0.0003",
        }),
      ],
      open: [],
    },
    status,
  ),
  { status },
);
assert.ok(Math.abs(dustPaddedSummary.spotSold.BTC - 0.0002) < 1e-12);
assert.ok(Math.abs(dustPaddedSummary.spotSoldQuote.BTC - 12.5) < 1e-9);
assert.ok(Math.abs(dustPaddedSummary.spotHeld.BTC - 0.0001) < 1e-12);
assert.ok(Math.abs(dustPaddedSummary.spotEarned.BTC - 0.0003) < 1e-12);

const disposition = aggregateProfitDisposition(report, groups, status);
const summary = summarizeProfitDisposition(disposition);
assert.ok(summary.spotEarned.BTC > 0);
assert.ok(summary.usdtSwapped > 0);

const composition = profitCompositionByBook(report, groups, status);
assert.ok(composition.earnedUsdByBook.BTC > composition.swappedUsdtByBook.BTC);
// Earned USD = swapped USDT proceeds + unswept native × live spot (not all-native×spot).
assert.ok(Math.abs(composition.earnedUsdByBook.BTC - (107.5 + 0.000086 * 63000)) < 0.1);
assert.ok(Math.abs(composition.earnedNativeByBook.BTC - 0.0018) < 1e-4);
assert.ok(Math.abs(composition.swappedNativeByBook.BTC - 0.001714) < 1e-4);
assert.ok(Math.abs(composition.swappedUsdtByBook.BTC - 107.5) < 0.01);

const usdByBook = sumLifetimeRealizedPnlUsdcByBook(report, groups, status);
let expectedBtcUsd = 0;
for (const g of groups.closed) {
  const pnl = realizedPnlDisplayUsdc(g, status);
  if (pnl !== null) expectedBtcUsd += pnl;
}
assert.ok(Math.abs(composition.usdByBook.BTC - expectedBtcUsd) < 0.01);
assert.ok(Math.abs(usdByBook.BTC - expectedBtcUsd) < 0.01);
assert.ok(Math.abs(composition.nativeByBook.BTC - (summary.spotHeld.BTC + summary.spotPending.BTC)) < 1e-10);

const fullySold = profitCompositionByBook(
  report,
  {
    closed: [
      group({
        group_id: "g3",
        profit_sweep_status: "filled",
        profit_sweep_amount: "0.001",
        profit_sweep_quote_proceeds: "57.5",
      }),
    ],
    open: [],
  },
  status,
);
assert.equal(fullySold.nativeByBook.BTC, 0);
// Fully swapped: earned USD matches sold USDT proceeds (not native×spot).
assert.ok(Math.abs(fullySold.earnedUsdByBook.BTC - 57.5) < 0.1);
assert.ok(Math.abs(fullySold.swappedUsdtByBook.BTC - 57.5) < 0.01);
assert.ok(Math.abs(fullySold.usdByBook.BTC - 57.5) < 0.01);

const statusWithLowWallet = {
  ...status,
  accounts: { USDT: { equity: 1.43 } },
};
const lowWallet = aggregateProfitDisposition(report, groups, statusWithLowWallet);
const lowSummary = summarizeProfitDisposition(lowWallet);
assert.ok(Math.abs(lowSummary.usdtSwapped - 107.5) < 0.01);

const withdrawnCase = profitCompositionByBook(
  report,
  {
    closed: [
      group({
        group_id: "g4",
        profit_sweep_status: "filled",
        profit_sweep_amount: "0.00048072",
        profit_sweep_quote_proceeds: "0.0806",
        realized_pnl_collateral_native: "0.00048072",
        realized_pnl: "38",
        profit_sweep_reason: "proceeds_reconciled",
      }),
    ],
    open: [],
  },
  status,
);
assert.ok(Math.abs(withdrawnCase.swappedUsdtByBook.BTC - 0) < 0.01);
assert.ok(Math.abs(withdrawnCase.usdByBook.BTC - 0.00048072 * 63000) < 0.1);
assert.ok(Math.abs(withdrawnCase.nativeByBook.BTC - 0.00048072) < 1e-10);

const actualProceedsField = profitCompositionByBook(
  report,
  {
    closed: [
      group({
        group_id: "g5",
        profit_sweep_status: "filled",
        profit_sweep_amount: "0.001",
        profit_sweep_quote_proceeds: "0.08",
        profit_sweep_quote_proceeds_lifetime: "57.5",
        realized_pnl: "60",
        profit_sweep_reason: "proceeds_reconciled",
      }),
    ],
    open: [],
  },
  status,
);
assert.ok(Math.abs(actualProceedsField.swappedUsdtByBook.BTC - 0) < 0.01);
assert.ok(Math.abs(actualProceedsField.usdByBook.BTC - 63) < 0.1);

const dustLifetime = profitCompositionByBook(
  report,
  {
    closed: [
      group({
        group_id: "g8",
        profit_sweep_status: "filled",
        profit_sweep_amount: "0.00048072",
        profit_sweep_quote_proceeds: "0.08355164",
        profit_sweep_quote_proceeds_lifetime: "0.08355164",
        realized_pnl_collateral_native: "0.00048072",
        realized_pnl: "38",
        profit_sweep_reason: "proceeds_reconciled",
      }),
    ],
    open: [],
  },
  status,
);
assert.ok(Math.abs(dustLifetime.swappedUsdtByBook.BTC - 0) < 0.0001);
assert.ok(Math.abs(dustLifetime.nativeByBook.BTC - 0.00048072) < 1e-10);

const jackLike = profitCompositionByBook(
  report,
  {
    closed: [
      group({
        group_id: "j-btc",
        profit_sweep_status: "filled",
        profit_sweep_amount: "0.00048072",
        profit_sweep_quote_proceeds: "0.08355164",
        profit_sweep_quote_proceeds_lifetime: "0.08355164",
        realized_pnl_collateral_native: "0.00048072",
        realized_pnl: "38",
        profit_sweep_reason: "proceeds_reconciled",
      }),
      group({
        group_id: "j-eth",
        currency: "ETH",
        collateral_currency: "ETH",
        profit_sweep_status: "filled",
        profit_sweep_amount: "0.0044",
        profit_sweep_quote_proceeds: "0.0215",
        realized_pnl_collateral_native: "0.0044",
        realized_pnl: "9.13127",
        profit_sweep_reason: "proceeds_reconciled",
      }),
    ],
    open: [],
  },
  status,
);
assert.ok(Math.abs(jackLike.swappedUsdtByBook.BTC - 0) < 0.0001);
assert.ok(Math.abs(jackLike.swappedUsdtByBook.ETH - 0) < 0.0001);
assert.ok(Math.abs(jackLike.usdByBook.BTC - 0.00048072 * 63000) < 0.1);
assert.ok(Math.abs(jackLike.usdByBook.ETH - 0.0044 * 1675) < 0.1);

const maLikeStatus = {
  ...status,
  premium_sweep_fill_stats_by_book: {
    BTC: {
      net_native_sold: "0.0027",
      net_usdt: "161.9496",
      unlabeled_native_sold: "0.0002",
      unlabeled_usdt: "14.616",
      display_native_sold: "0.0029",
      display_usdt: "176.5656",
      display_avg_price_usd: "60884.69",
    },
  },
};
const maLike = summarizeProfitDisposition(
  {
    ...emptyProfitDisposition(),
    heldNative: { BTC: 0, ETH: 0, USDC: 0 },
    pendingSweepNative: { BTC: 0, ETH: 0 },
    sweptNativeRef: { BTC: 0.00285, ETH: 0 },
    sweptQuoteProceedsByBook: { BTC: 161.9496, ETH: 0 },
    excludedSweptNativeRefByBook: { BTC: 0.0002, ETH: 0 },
    excludedSweptQuoteProceedsByBook: { BTC: 14.65414, ETH: 0 },
    sweptUsdt: 176.60374,
  },
  { status: maLikeStatus },
);
// Exchange display sold used for Sold; journal dust padding above display stays in Remaining.
assert.ok(Math.abs(maLike.spotSold.BTC - 0.0029) < 1e-10);
assert.ok(Math.abs(maLike.spotEarned.BTC - (0.00285 + 0.0002)) < 1e-10);
assert.ok(Math.abs(maLike.spotHeld.BTC - 0.00015) < 1e-10);
assert.ok(Math.abs(maLike.spotSoldQuote.BTC - 176.5656) < 0.0001);
assert.ok(Math.abs(maLike.usdtSwapped - 176.5656) < 0.0001);
assert.ok(Math.abs(maLike.spotSoldAvg.BTC - 60884.689) < 0.001);
assert.equal(profitSwapDisplayAvg("BTC", 176.5656, 0.0029), 60884.689);
assert.equal(profitSwapDisplayAvg("BTC", 161.9496, 0.0027), 59981.333);

const maPartialStatus = {
  ...status,
  premium_sweep_fill_stats_by_book: {
    BTC: {
      net_native_sold: "0.00011",
      net_usdt: "6.85249747",
      net_avg_price_usd: "62295.43",
    },
  },
};
const maComposition = profitCompositionByBook(
  report,
  {
    closed: [
      group({
        group_id: "0001",
        profit_sweep_status: "filled",
        profit_sweep_amount: "0.0002",
        profit_sweep_quote_proceeds: "14.65414",
        profit_sweep_quote_proceeds_lifetime: "14.65414",
        profit_sweep_reason: "manual_swap; proceeds_reconciled; unlabeled_premium_reconciled",
        realized_pnl_collateral_native: "0.0002",
        realized_pnl: "14.65414",
      }),
      group({
        group_id: "0002",
        profit_sweep_status: "filled",
        profit_sweep_amount: "0.00011",
        profit_sweep_quote_proceeds: "6.85249747",
        profit_sweep_quote_proceeds_lifetime: "6.85249747",
        realized_pnl_collateral_native: "0.00011",
        realized_pnl: "6.85",
      }),
    ],
    open: [],
  },
  maPartialStatus,
);
assert.ok(Math.abs(maComposition.swappedUsdtByBook.BTC - 21.50663747) < 0.01);
assert.ok(Math.abs(maComposition.swappedNativeByBook.BTC - 0.00031) < 1e-10);

// Jack: exchange net native/USDT per book (post buyback).
const jackOversellStatus = {
  ...status,
  premium_sweep_fill_stats_by_book: {
    BTC: {
      net_native_sold: "0.0065",
      net_usdt: "411.449",
      net_avg_price_usd: "63299.84",
    },
    ETH: {
      net_native_sold: "0.0834",
      net_usdt: "139.982",
      net_avg_price_usd: "1678.44",
    },
  },
};
const jackOversell = summarizeProfitDisposition(
  {
    ...emptyProfitDisposition(),
    heldNative: { BTC: 0, ETH: 0, USDC: 0 },
    pendingSweepNative: { BTC: 0, ETH: 0 },
    sweptNativeRef: { BTC: 0.0063078, ETH: 0.08089459 },
    sweptQuoteProceedsByBook: { BTC: 443.42546388, ETH: 108.07189632 },
    sweptUsdt: 551.4973602,
  },
  { status: jackOversellStatus },
);
// Exchange sold can exceed journal earned; Earned is lifted to ≥ Sold.
assert.ok(Math.abs(jackOversell.spotEarned.BTC - 0.0065) < 1e-10);
assert.ok(Math.abs(jackOversell.spotSold.BTC - 0.0065) < 1e-10);
// Exchange execution VWAP (net USDT ÷ net native sold), not journal attribution drift.
assert.ok(Math.abs(jackOversell.spotSoldQuote.BTC - 411.449) < 0.01);
assert.ok(Math.abs(jackOversell.spotSoldAvg.BTC - 63299.84) < 0.01);
assert.ok(Math.abs(jackOversell.spotEarned.ETH - 0.0834) < 1e-10);
assert.ok(Math.abs(jackOversell.spotSold.ETH - 0.0834) < 1e-10);
assert.ok(Math.abs(jackOversell.spotSoldQuote.ETH - 139.982) < 0.01);
assert.ok(Math.abs(jackOversell.spotSoldAvg.ETH - 1678.44) < 0.01);
assert.ok(Math.abs(jackOversell.usdtSwapped - 551.431) < 0.02);

const jackDisplay = resolvePremiumSweepBookDisplay({
  journalSold: 0.0063078,
  journalQuote: 443.42546388,
  exchange: jackOversellStatus.premium_sweep_fill_stats_by_book.BTC,
  earned: 0.0063078,
});
assert.ok(Math.abs(jackDisplay.soldNative - 0.0065) < 1e-10);
assert.ok(Math.abs(jackDisplay.soldQuote - 411.449) < 0.01);
assert.ok(Math.abs(jackDisplay.avg - 63299.84) < 0.01);

const jackComposition = profitCompositionByBook(
  report,
  {
    closed: [
      group({
        group_id: "j-btc-all",
        profit_sweep_status: "filled",
        profit_sweep_amount: "0.0063078",
        profit_sweep_quote_proceeds_lifetime: "443.42546388",
        realized_pnl_collateral_native: "0.0063078",
        realized_pnl: "455",
      }),
      group({
        group_id: "j-eth-all",
        currency: "ETH",
        collateral_currency: "ETH",
        profit_sweep_status: "filled",
        profit_sweep_amount: "0.08089459",
        profit_sweep_quote_proceeds_lifetime: "108.07189632",
        realized_pnl_collateral_native: "0.08089459",
        realized_pnl: "161",
      }),
    ],
    open: [],
  },
  jackOversellStatus,
);
assert.ok(Math.abs(jackComposition.swappedNativeByBook.BTC - 0.0065) < 1e-10);
assert.ok(Math.abs(jackComposition.swappedNativeByBook.ETH - 0.0834) < 1e-10);
assert.ok(Math.abs(jackComposition.swappedUsdtByBook.BTC - 411.449) < 0.01);
assert.ok(Math.abs(jackComposition.swappedUsdtByBook.ETH - 139.982) < 0.01);
// Fully swapped books: usdByBook follows exchange Sold USDT (not journal lifetime drift).
assert.ok(Math.abs(jackComposition.usdByBook.ETH - 139.982) < 0.5);

const jackSummary = summarizeProfitDisposition(
  {
    ...emptyProfitDisposition(),
    heldNative: { BTC: 0, ETH: 0, USDC: 0 },
    pendingSweepNative: { BTC: 0, ETH: 0 },
    sweptNativeRef: { BTC: 0.0063078, ETH: 0.08089459 },
    sweptQuoteProceedsByBook: { BTC: 443.42546388, ETH: 108.07189632 },
    sweptUsdt: 551.4973602,
  },
  { status: jackOversellStatus },
);
// Composition row USD must match profit-swap SOLD quote, not earned-at-spot.
assert.ok(Math.abs(jackComposition.swappedUsdtByBook.BTC - jackSummary.spotSoldQuote.BTC) < 0.01);
assert.ok(Math.abs(jackComposition.swappedUsdtByBook.ETH - jackSummary.spotSoldQuote.ETH) < 0.01);

const entryUsd = (comp, book) => {
  const swapped = comp.swappedUsdtByBook[book] || 0;
  const native = comp.nativeByBook[book] || 0;
  const total = comp.usdByBook[book] || 0;
  if (swapped > 0.005 && Math.abs(native) < 1e-8) return swapped;
  return total;
};
assert.ok(Math.abs(entryUsd(jackComposition, "BTC") - jackSummary.spotSoldQuote.BTC) < 0.01);
assert.ok(Math.abs(entryUsd(jackComposition, "ETH") - jackSummary.spotSoldQuote.ETH) < 0.01);

const entryMs = 1_699_000_000_000;
const closedMs = 1_700_000_000_000;
const sampleDays = (Date.now() - entryMs) / (24 * 3600 * 1000);
const capital = 10000;
const recentClosedMs = Date.now() - 5 * 24 * 3600 * 1000;
const recentEntryMs = recentClosedMs - sampleDays * 24 * 3600 * 1000;
const reconciledGroups = {
  closed: [
    group({
      group_id: "g6",
      entry_timestamp_ms: entryMs,
      closed_timestamp_ms: closedMs,
      profit_sweep_status: "filled",
      profit_sweep_amount: "0.001",
      profit_sweep_quote_proceeds: "1.43",
      realized_pnl: "60",
      profit_sweep_reason: "proceeds_reconciled",
    }),
  ],
  open: [],
};
const summaryStub = { effective_capital_usdc: String(capital), lifetime_sample_days: String(sampleDays) };
const lifetimeApr = computeLifetimeRealizedApr(report, reconciledGroups, status, summaryStub);
const expectedApr = annualizeRealizedApr(63, sampleDays, capital);
assert.ok(Math.abs(lifetimeApr - expectedApr) < 1e-10);
assert.ok(lifetimeApr > 0);

const recentGroups = {
  closed: [
    group({
      group_id: "g7",
      entry_timestamp_ms: recentEntryMs,
      closed_timestamp_ms: recentClosedMs,
      profit_sweep_status: "filled",
      profit_sweep_amount: "0.001",
      profit_sweep_quote_proceeds: "1.43",
      realized_pnl: "60",
      profit_sweep_reason: "proceeds_reconciled",
    }),
  ],
  open: [],
};
const windowApr = computeWindowRealizedApr(report, recentGroups, status, summaryStub, 30);
const expectedWindowApr = annualizeRealizedApr(63, 30, capital);
assert.ok(Math.abs(windowApr - expectedWindowApr) < 1e-10);

// ma: manual swap + reconcile lifetime drift must not double-count or inflate income.
const maIncomeGroups = {
  closed: [
    group({
      group_id: "0001",
      profit_sweep_status: "filled",
      profit_sweep_amount: "0.0002",
      profit_sweep_quote_proceeds: "14.65414",
      profit_sweep_quote_proceeds_lifetime: "14.65414",
      profit_sweep_reason: "manual_swap; proceeds_reconciled; unlabeled_premium_reconciled",
      realized_pnl_collateral_native: "0.0002",
      realized_pnl: "14.65414",
    }),
    group({
      group_id: "0016",
      profit_sweep_status: "filled",
      profit_sweep_amount: "0.00002",
      profit_sweep_exchange_native: "0.00002",
      profit_sweep_exchange_quote_proceeds: "0.90955257",
      profit_sweep_quote_proceeds: "0.90955257",
      profit_sweep_quote_proceeds_lifetime: "1.23016371",
      profit_sweep_reason: "take_profit; dust_pool_sweep; proceeds_reconciled",
      realized_pnl_collateral_native: "0.00002",
      realized_pnl: "1.259841",
    }),
  ],
  open: [],
};
const maIncomeDisp = aggregateProfitDisposition(report, maIncomeGroups, status);
const maIncomeSummary = summarizeProfitDisposition(maIncomeDisp, { status });
const maIncomeComp = profitCompositionByBook(report, maIncomeGroups, status);
const maActualUsdt = 14.65414 + 0.90955257;
assert.ok(Math.abs(maIncomeSummary.spotSoldQuote.BTC - maActualUsdt) < 0.01);
assert.ok(Math.abs(maIncomeSummary.usdtSwapped - maActualUsdt) < 0.01);
assert.ok(Math.abs(maIncomeComp.usdByBook.BTC - maActualUsdt) < 0.01);

// an: journal profit_sweep_amount can drift above exchange fill qty; avg must match Deribit VWAP.
const anStatus = {
  ...status,
  premium_sweep_fill_stats_by_book: {
    BTC: {
      gross_native_sold: "0.0018",
      gross_usdt: "113.3305",
      gross_avg_price_usd: "62961.38",
      net_native_sold: "0.0018",
      net_usdt: "113.3305",
      net_avg_price_usd: "62961.38",
    },
  },
};
const anSummary = summarizeProfitDisposition(
  {
    ...emptyProfitDisposition(),
    sweptNativeRef: { BTC: 0.001896, ETH: 0 },
    sweptQuoteProceedsByBook: { BTC: 115.59969053, ETH: 0 },
    sweptUsdt: 113.3305,
  },
  { status: anStatus },
);
assert.ok(Math.abs(anSummary.spotSold.BTC - 0.0018) < 1e-10);
assert.ok(Math.abs(anSummary.spotSoldQuote.BTC - 113.3305) < 0.01);
assert.ok(Math.abs(anSummary.spotSoldAvg.BTC - 62961.38) < 1);
assert.ok(Math.abs(anSummary.spotSoldQuote.BTC / anSummary.spotSoldAvg.BTC - 0.0018) < 1e-6);
assert.ok(Math.abs(anSummary.spotHeld.BTC - 0.000096) < 1e-7);
assert.equal(fmtProfitNative("BTC", anSummary.spotHeld.BTC), "0.000096");

assert.equal(fmtProfitNative("BTC", 0.000096), "0.000096");
assert.equal(fmtProfitNative("BTC", 0.0000965), "0.000096");
assert.equal(fmtProfitUsdt(113.3305), "$113.33");
assert.equal(fmtProfitUsdt(113.3399), "$113.339");
assert.equal(truncateDecimal(62961.389, 3), 62961.389);

const reconciledOnly = group({
  group_id: "0034",
  profit_sweep_status: "filled",
  profit_sweep_amount: "0.000406",
  profit_sweep_quote_proceeds: "14.08540529",
  realized_pnl_collateral_native: "0.000406",
  profit_sweep_reason: "take_profit; proceeds_reconciled",
});
assert.equal(profitSweepHasExchangeFill(reconciledOnly), false);
// No per-group exchange fill → LOG shows pending, not fake journal USDT/avg.
const reconciledOnlyMeta = profitSweepMetaLine(reconciledOnly);
assert.ok(reconciledOnlyMeta);
assert.equal(reconciledOnlyMeta[0], "Profit swapped");
assert.ok(reconciledOnlyMeta[1].includes("0.000406 BTC"), reconciledOnlyMeta[1]);
assert.ok(reconciledOnlyMeta[1].includes("pending"), reconciledOnlyMeta[1]);
assert.ok(!reconciledOnlyMeta[1].includes("14.085 USDT"), reconciledOnlyMeta[1]);
assert.ok(Math.abs(realizedPnlDisplayUsdc(reconciledOnly, status) - 0.000406 * 63000) < 0.1);

// MA dust-pool ledger row without exchange_native must not print absurd avg.
const ma0016 = group({
  group_id: "0016",
  profit_sweep_status: "filled",
  profit_sweep_amount: "0.00002",
  profit_sweep_instrument_name: "BTC_USDT",
  profit_sweep_quote_proceeds: "3.58959158",
  realized_pnl_collateral_native: "0.00002",
  profit_sweep_reason: "take_profit; dust_pool_sweep; proceeds_reconciled",
});
assert.equal(profitSweepHasExchangeFill(ma0016), false);
const ma0016Meta = profitSweepMetaLine(ma0016);
assert.ok(ma0016Meta);
assert.ok(ma0016Meta[1].includes("pending"), ma0016Meta[1]);
assert.ok(!ma0016Meta[1].includes("avg"), ma0016Meta[1]);
assert.ok(!ma0016Meta[1].includes("3.589"), ma0016Meta[1]);

const exchangeFilled = group({
  profit_sweep_status: "filled",
  profit_sweep_amount: "0.000406",
  profit_sweep_exchange_native: "0.000406",
  profit_sweep_quote_proceeds: "25.58",
  profit_sweep_order_id: "BTC_USDT-123",
  realized_pnl_collateral_native: "0.000406",
});
assert.equal(profitSweepHasExchangeFill(exchangeFilled), true);
assert.equal(profitSweepExchangeNativeSold(exchangeFilled, "BTC"), 0.000406);
const exchangeFilledMeta = profitSweepMetaLine(exchangeFilled);
assert.ok(exchangeFilledMeta);
const expectedAvg = fmtProfitAvgUsd("BTC", profitSwapDisplayAvg("BTC", 25.58, 0.000406));
assert.ok(exchangeFilledMeta[1].includes(`avg ${expectedAvg}`), exchangeFilledMeta[1]);
assert.ok(exchangeFilledMeta[1].includes("0.000406 BTC"), exchangeFilledMeta[1]);

const jack0042 = group({
  group_id: "0042",
  currency: "ETH",
  collateral_currency: "ETH",
  profit_sweep_status: "filled",
  profit_sweep_amount: "0.00096",
  profit_sweep_exchange_native: "0.0009",
  profit_sweep_quote_proceeds: "1.5714",
  profit_sweep_order_id: "ETH_USDT-8545094014",
  realized_pnl_collateral_native: "0.00096",
  profit_sweep_reason: "time_exit; dust_pool_sweep; proceeds_reconciled",
});
assert.equal(profitSweepExchangeNativeSold(jack0042, "ETH"), 0.0009);
const jack0042Meta = profitSweepMetaLine(jack0042);
assert.ok(jack0042Meta);
assert.ok(jack0042Meta[1].includes("0.0009 ETH"), jack0042Meta[1]);
const jack0042QuoteDisp = fmtProfitUsdt(jack0042.profit_sweep_quote_proceeds).replace(/^\$/, "");
assert.ok(jack0042Meta[1].includes(`${jack0042QuoteDisp} USDT`), jack0042Meta[1]);
const jack0042Avg = fmtProfitAvgUsd(
  "ETH",
  profitSwapDisplayAvg("ETH", jack0042.profit_sweep_quote_proceeds, 0.0009)
);
assert.ok(jack0042Meta[1].includes(`avg ${jack0042Avg}`), jack0042Meta[1]);

const jack0037 = group({
  group_id: "0037",
  currency: "ETH",
  collateral_currency: "ETH",
  profit_sweep_status: "filled",
  profit_sweep_amount: "0.00236",
  profit_sweep_exchange_native: "0.0023",
  profit_sweep_exchange_quote_proceeds: "3.77752",
  profit_sweep_quote_proceeds: "3.77752",
  profit_sweep_order_id: "ETH_USDT-8436355552",
  realized_pnl_collateral_native: "0.00236",
  profit_sweep_reason: "take_profit; exchange_fully_swept",
});
const jack0037Meta = profitSweepMetaLine(jack0037);
assert.ok(jack0037Meta);
assert.ok(jack0037Meta[1].includes("0.0023 ETH"), jack0037Meta[1]);
assert.ok(jack0037Meta[1].includes("3.777 USDT"), jack0037Meta[1]);

const jack0036 = group({
  group_id: "0036",
  currency: "ETH",
  collateral_currency: "ETH",
  profit_sweep_status: "filled",
  profit_sweep_amount: "0.001705",
  profit_sweep_exchange_native: "0.0017",
  profit_sweep_exchange_quote_proceeds: "2.78307",
  profit_sweep_quote_proceeds: "2.78307",
  profit_sweep_order_id: "ETH_USDT-8434278007",
  realized_pnl_collateral_native: "0.001705",
  profit_sweep_reason: "take_profit; exchange_fully_swept",
});
const jack0036Meta = profitSweepMetaLine(jack0036);
assert.ok(jack0036Meta);
assert.ok(jack0036Meta[1].includes("0.0017 ETH"), jack0036Meta[1]);

// Legacy ITM fold: premium → Profit swap Sold; ITM Sold shows cover only.
const itmFolded = group({
  group_id: "0070",
  currency: "ETH",
  collateral_currency: "ETH",
  quantity: "2",
  covered_underlying_quantity: "2",
  short_entry_average_price: "0.01",
  entry_fee_collateral: "0.00054",
  realized_pnl_collateral_native: "0.00812",
  spot_exit_status: "filled",
  spot_exit_amount: "2.0156",
  spot_exit_quote_proceeds_lifetime: "3839.52123",
  spot_exit_settlement_loss: "0.00381376",
  spot_restore_status: "filled",
  spot_restore_amount: "1.9999",
  spot_restore_quote_spent_lifetime: "3809.60951",
});
assert.equal(itmSpotExitPremiumFolded(itmFolded), true);
const foldedUsdt = itmFoldedPremiumUsdt(itmFolded);
assert.ok(foldedUsdt > 0);
const itmDisp = profitDispositionForGroup(itmFolded, status);
assert.ok(itmDisp);
assert.ok(Math.abs(itmDisp.sweptUsdt - foldedUsdt) < 1e-6);
const itmNet = itmSpotExitNetUsdtForTotalProfit(itmFolded);
assert.ok(itmNet !== null);
assert.ok(
  Math.abs(itmNet - (3839.52123 - 3809.60951 - foldedUsdt)) < 1e-4,
  `itmNet=${itmNet} folded=${foldedUsdt}`,
);
const spotExitSum = summarizeSpotExitDisposition({ closed: [itmFolded], open: [] }, { status });
assert.ok(spotExitSum);
assert.ok(spotExitSum.soldNative.ETH < 2.0156 - 0.01);
assert.ok(spotExitSum.soldQuote.ETH < 3839.52123 - foldedUsdt + 1e-6);

// Exchange fill-stats include folded premium — Sold / Net must still peel it.
const foldNative = 2.0156 - spotExitSum.soldNative.ETH;
assert.ok(foldNative > 0.01);
const withFillStats = summarizeSpotExitDisposition(
  { closed: [itmFolded], open: [] },
  {
    status: {
      ...status,
      spot_exit_fill_stats_by_book: {
        ETH: { native_sold: "2.0156", usdt: "3839.52123" },
      },
      spot_restore_fill_stats_by_book: {
        ETH: { native_bought: "1.9999", usdt_spent: "3809.60951" },
      },
    },
  },
);
assert.ok(withFillStats);
assert.ok(
  Math.abs(withFillStats.soldNative.ETH - spotExitSum.soldNative.ETH) < 1e-9,
  `fill-stats soldNative=${withFillStats.soldNative.ETH} journal=${spotExitSum.soldNative.ETH}`,
);
assert.ok(
  Math.abs(withFillStats.soldQuote.ETH - spotExitSum.soldQuote.ETH) < 1e-6,
  `fill-stats soldQuote=${withFillStats.soldQuote.ETH}`,
);
assert.ok(
  Math.abs(withFillStats.usdtNet - (spotExitSum.soldQuote.ETH - 3809.60951)) < 1e-4,
  `usdtNet=${withFillStats.usdtNet}`,
);

// Profit-swap Sold must re-add ITM-folded premium when exchange fill-stats omit it.
assert.ok(itmDisp.fromItmFold);
const foldDisp = emptyProfitDisposition();
foldDisp.sweptNativeRef.ETH = itmDisp.sweptNative + 0.005; // fold + real premium_sweep
foldDisp.sweptQuoteProceedsByBook.ETH = itmDisp.sweptUsdt + 8;
foldDisp.sweptUsdt = itmDisp.sweptUsdt + 8;
foldDisp.foldedSweptNativeRefByBook.ETH = itmDisp.sweptNative;
foldDisp.foldedSweptQuoteProceedsByBook.ETH = itmDisp.sweptUsdt;
foldDisp.heldNative.ETH = 0.01; // unswept leftover
const foldSwap = summarizeProfitDisposition(foldDisp, {
  status: {
    ...status,
    premium_sweep_fill_stats_by_book: {
      ETH: {
        display_native_sold: 0.005,
        display_usdt: 8,
        native_sold: 0.005,
        usdt: 8,
        net_native_sold: 0.005,
        net_usdt: 8,
      },
    },
  },
});
assert.ok(foldSwap);
assert.ok(
  Math.abs(foldSwap.spotSold.ETH - (0.005 + itmDisp.sweptNative)) < 1e-9,
  `profit-swap sold=${foldSwap.spotSold.ETH}`,
);
assert.ok(
  Math.abs(foldSwap.spotSoldQuote.ETH - (8 + foldedUsdt)) < 1e-4,
  `profit-swap soldQuote=${foldSwap.spotSoldQuote.ETH}`,
);
assert.ok(
  Math.abs(foldSwap.spotEarned.ETH - (0.01 + 0.005 + itmDisp.sweptNative)) < 1e-9,
);
assert.ok(
  Math.abs(foldSwap.spotHeld.ETH - 0.01) < 1e-9,
  `remaining should exclude fold; held=${foldSwap.spotHeld.ETH}`,
);

// Pat-style: exchange Sold (incl. dust) leaves unswept < journal pending → clamp Remaining.
const patPendingStatus = {
  ...status,
  premium_sweep_fill_stats_by_book: {
    BTC: {
      net_native_sold: "0.00091239",
      net_usdt: "57.5",
      display_native_sold: "0.00091239",
      display_usdt: "57.5",
    },
  },
};
const patPendingSummary = summarizeProfitDisposition(
  {
    ...emptyProfitDisposition(),
    heldNative: { BTC: 0, ETH: 0, USDC: 0 },
    pendingSweepNative: { BTC: 0.00015437, ETH: 0 },
    sweptNativeRef: { BTC: 0.00084602, ETH: 0 },
    sweptQuoteProceedsByBook: { BTC: 53.2, ETH: 0 },
    sweptUsdt: 53.2,
  },
  { status: patPendingStatus },
);
const patEarned = 0.00084602 + 0.00015437;
const patSold = 0.00091239;
const patUnswept = patEarned - patSold;
assert.ok(Math.abs(patPendingSummary.spotSold.BTC - patSold) < 1e-12);
assert.ok(Math.abs(patPendingSummary.spotEarned.BTC - patEarned) < 1e-12);
assert.ok(Math.abs(patPendingSummary.spotPending.BTC - patUnswept) < 1e-12);
assert.ok(Math.abs(patPendingSummary.spotPending.BTC - 0.000088) < 1e-12);
assert.equal(patPendingSummary.spotHeld.BTC || 0, 0);
// Must not lift Earned to sold+pending (would inflate Remaining).
assert.ok(patPendingSummary.spotEarned.BTC + 1e-12 < patSold + 0.00015437);

// Failed ITM plan (not_enough_funds, no proceeds) is not a fill — do not fold or hide premium.
const failedItmPlan = group({
  group_id: "0082",
  currency: "ETH",
  collateral_currency: "ETH",
  quantity: "1",
  covered_underlying_quantity: "1",
  realized_pnl_collateral_native: "0.01",
  spot_exit_status: "pending",
  spot_exit_amount: "0.9845",
  spot_exit_order_id: "ETH_USDT-failed",
  spot_exit_quote_proceeds: "0",
  spot_exit_reason: "not_enough_funds",
});
assert.equal(groupHasItmSpotExitFills(failedItmPlan), false);
assert.equal(itmSpotExitPremiumFolded(failedItmPlan), false);
const failedDisp = profitDispositionForGroup(failedItmPlan, status);
assert.ok(failedDisp);
assert.equal(failedDisp.fromItmFold, undefined);
assert.ok(Math.abs(failedDisp.held - 0.01) < 1e-12);

const cspSwapped = group({
  group_id: "0115",
  currency: "ETH",
  collateral_currency: "USDC",
  strategy: "cash_secured",
  cash_secured_from_group_id: "0095",
  entry_credit: "6.035",
  realized_pnl: "6.035",
  realized_close_debit: "0",
  realized_close_fee: "0",
  csp_premium_swap_status: "filled",
  csp_premium_swap_amount: "5.7845",
  csp_premium_swap_native: "0.0023",
});
const cspSkipped = group({
  group_id: "0097",
  currency: "BTC",
  collateral_currency: "USDC",
  strategy: "cash_secured",
  cash_secured_from_group_id: "0096",
  entry_credit: "5.6875",
  realized_pnl: "5.6875",
  realized_close_debit: "0",
  realized_close_fee: "0",
  csp_premium_swap_status: "skipped",
  csp_premium_swap_reason: "dust_below_min_omitted",
});
const cspSwappedDisp = profitDispositionForGroup(cspSwapped, status);
assert.ok(cspSwappedDisp);
assert.ok(Math.abs(cspSwappedDisp.held - 0.2505) < 1e-12);
assert.equal(cspSwappedDisp.cspSpotBook, "ETH");
assert.ok(Math.abs(cspSwappedDisp.cspSpotNative - 0.0023) < 1e-12);
const cspSkippedDisp = profitDispositionForGroup(cspSkipped, status);
assert.ok(cspSkippedDisp);
assert.ok(Math.abs(cspSkippedDisp.held - 5.6875) < 1e-12);
assert.equal(cspSkippedDisp.cspSpotNative, undefined);
const cspComposition = profitCompositionByBook(
  report,
  { closed: [cspSwapped, cspSkipped], open: [] },
  { ...status, underlying_index_usd: { BTC: 80000, ETH: 2500 } },
);
assert.ok(Math.abs(cspComposition.nativeByBook.USDC - 5.938) < 1e-8);
assert.ok(Math.abs(cspComposition.nativeByBook.ETH - 0.0023) < 1e-12);
assert.ok(Math.abs((cspComposition.nativeByBook.BTC ?? 0)) < 1e-12);
assert.ok(Math.abs(cspComposition.usdByBook.USDC - 5.938) < 0.01);
assert.ok(Math.abs(cspComposition.usdByBook.ETH - 0.0023 * 2500) < 0.02);
assert.ok(Math.abs(cspComposition.earnedUsdByBook.USDC - 5.938) < 0.01);
assert.ok(Math.abs(cspComposition.earnedUsdByBook.ETH - 0.0023 * 2500) < 0.02);
assert.ok(Math.abs(realizedPnlDisplayUsdc(cspSwapped, { ...status, underlying_index_usd: { BTC: 80000, ETH: 2500 } }) - (0.2505 + 0.0023 * 2500)) < 0.02);

const cspSwapMeta = cspPremiumSwapMetaLine(cspSwapped);
assert.ok(cspSwapMeta);
assert.equal(cspSwapMeta[0], "CSP swapped");
assert.ok(cspSwapMeta[1].includes("USDC →"), cspSwapMeta[1]);
assert.ok(cspSwapMeta[1].includes("ETH"), cspSwapMeta[1]);
assert.ok(cspSwapMeta[1].includes("leftover"), cspSwapMeta[1]);
const cspSkipMeta = cspPremiumSwapMetaLine(cspSkipped);
assert.ok(cspSkipMeta);
assert.equal(cspSkipMeta[0], "CSP swap");
assert.ok(cspSkipMeta[1].includes("USDC"), cspSkipMeta[1]);
assert.ok(cspSkipMeta[1].includes("skipped"), cspSkipMeta[1]);
const cspParent = group({
  group_id: "0095",
  cash_secured_status: "entered",
  cash_secured_group_id: "0115",
  cash_secured_group_ids: ["0115"],
  spot_exit_status: "filled",
  entry_credit: "8.77",
  covered_underlying_quantity: "0.1",
});
const cspParentGroups = { closed: [cspSwapped, cspSkipped], open: [] };
const cspParentWheel = cashSecuredWheelPnl(cspParent, cspParentGroups);
assert.ok(Number.isFinite(cspParentWheel.total));
assert.ok(cspParentWheel.closedLegs >= 1);
const cspParentMeta = cashSecuredMetaLine(cspParent, cspParentGroups);
assert.ok(cspParentMeta);
assert.equal(cspParentMeta[0], "CSP wheel");
const cspParentCard = activityLifecycleCardHtml(cspParent, status, cspParentGroups);
assert.ok(cspParentCard.includes("Wheel PnL") || cspParentCard.includes("母倉累計"), cspParentCard);
const cspPanelParentA = group({
  group_id: "0095",
  cash_secured_status: "entered",
  cash_secured_group_id: "0099",
  cash_secured_group_ids: ["0097", "0099"],
  spot_exit_status: "filled",
  entry_credit: "8.77",
  short_strike: "73000",
  covered_underlying_quantity: "0.1",
});
const cspPanelParentB = group({
  group_id: "0096",
  cash_secured_status: "entered",
  cash_secured_group_id: "0098",
  spot_exit_status: "filled",
  entry_credit: "28.79",
  short_strike: "75000",
  covered_underlying_quantity: "0.1",
});
const cspPanelOpenA = group({
  group_id: "0099",
  status: "open",
  strategy: "cash_secured",
  cash_secured_from_group_id: "0095",
  short_instrument_name: "BTC_USDC-11SEP26-72000-P",
  quantity: "0.1",
  entry_timestamp_ms: 3,
  collateral_currency: "USDC",
});
const cspPanelClosedA = group({
  group_id: "0097",
  status: "closed",
  strategy: "cash_secured",
  cash_secured_from_group_id: "0095",
  short_instrument_name: "BTC_USDC-4SEP26-73000-P",
  quantity: "0.1",
  realized_pnl: "5.69",
  entry_timestamp_ms: 1,
  collateral_currency: "USDC",
});
const cspPanelOpenB = group({
  group_id: "0098",
  status: "open",
  strategy: "cash_secured",
  cash_secured_from_group_id: "0096",
  short_instrument_name: "BTC_USDC-11SEP26-75000-P",
  quantity: "0.1",
  entry_timestamp_ms: 2,
  collateral_currency: "USDC",
});
const cspPanelHtml = fmtCashSecuredPanel(
  summarizeCashSecuredDisposition({
    closed: [cspPanelParentA, cspPanelParentB, cspPanelClosedA],
    open: [cspPanelOpenA, cspPanelOpenB],
  }),
);
assert.ok(cspPanelHtml.includes("csp-wheel-grid"), cspPanelHtml);
assert.ok(cspPanelHtml.includes("#0095"), cspPanelHtml);
assert.ok(cspPanelHtml.includes("#0096"), cspPanelHtml);
assert.ok(cspPanelHtml.includes("73000-C"), cspPanelHtml);
assert.ok(cspPanelHtml.includes("72000-P"), cspPanelHtml);
assert.ok(cspPanelHtml.includes("75000-P"), cspPanelHtml);
assert.ok(cspPanelHtml.includes("#0099"), cspPanelHtml);
assert.ok(cspPanelHtml.includes("#0097"), cspPanelHtml);
const firstWheel = cspPanelHtml.indexOf("csp-wheel");
const idx95 = cspPanelHtml.indexOf("#0095");
const idx72000 = cspPanelHtml.indexOf("72000-P");
const idx73000p = cspPanelHtml.indexOf("73000-P");
const idx75000 = cspPanelHtml.indexOf("75000-P");
const idx96 = cspPanelHtml.indexOf("#0096");
assert.ok(firstWheel >= 0 && idx95 > firstWheel);
assert.ok(idx72000 > idx95 && idx72000 < idx96, "72000 put stays inside #0095 card");
assert.ok(idx73000p > idx95 && idx73000p < idx96, "closed 73000 put stays inside #0095 card");
assert.ok(idx72000 < idx73000p, "open put listed before closed history");
assert.ok(idx75000 > idx96, "75000 put stays inside #0096 card");
const cspParentSwap = cspPremiumSwapMetaLine(cspParent, { closed: [cspSwapped], open: [] });
assert.ok(cspParentSwap);
assert.equal(cspParentSwap[0], "CSP swapped");
assert.ok(cspParentSwap[1].includes("ETH"), cspParentSwap[1]);
const cspItmRestore = group({
  group_id: "0115",
  currency: "ETH",
  collateral_currency: "USDC",
  strategy: "cash_secured",
  cash_secured_from_group_id: "0095",
  spot_restore_status: "filled",
  spot_restore_reason: "cash_secured_itm_assignment",
  spot_restore_amount: "2",
  spot_restore_quote_spent: "4600",
});
const cspItmMeta = cashSecuredMetaLine(cspItmRestore);
assert.ok(cspItmMeta);
assert.equal(cspItmMeta[0], "CSP");
assert.ok(cspItmMeta[1].includes("ITM"), cspItmMeta[1]);
assert.ok(cspItmMeta[1].includes("USDC"), cspItmMeta[1]);
assert.ok(cspItmMeta[1].includes("ETH"), cspItmMeta[1]);

const eugene0021 = group({
  group_id: "0021",
  currency: "ETH",
  collateral_currency: "ETH",
  quantity: "1",
  covered_underlying_quantity: "1",
  short_instrument_name: "ETH-28AUG26-2400-C",
  account_name: "covered_call",
  cash_secured_group_id: "0026",
  cash_secured_group_ids: ["0026"],
  spot_exit_status: "filled",
  spot_exit_amount: "0.9608",
  spot_exit_quote_proceeds: "2399.43",
  spot_exit_quote_proceeds_lifetime: "2399.43",
  spot_exit_settlement_loss: "0.0392",
  spot_restore_status: "skipped",
  realized_pnl: "-261",
  realized_pnl_collateral_native: "-0.03657",
});
const eugene0026 = group({
  group_id: "0026",
  currency: "ETH",
  collateral_currency: "USDC",
  strategy: "cash_secured",
  option_type: "put",
  cash_secured_from_group_id: "0021",
  spot_restore_status: "filled",
  spot_restore_reason: "cash_secured_itm_assignment",
  spot_restore_amount: "1",
  spot_restore_quote_spent: "2405.859",
  spot_restore_quote_spent_lifetime: "2405.859",
});
const eugeneGroups = { closed: [eugene0021, eugene0026], open: [] };
assert.ok(unrestoredSpotExitNative(eugene0021) > 0.9);
assert.ok(unrestoredSpotExitNative(eugene0021, eugeneGroups) < 1e-8);
assert.equal(itmSpotRoundTripComplete(eugene0021, eugeneGroups), true);
const eugeneNet = itmSpotExitDisplayNetUsdt(eugene0021, eugeneGroups);
assert.ok(eugeneNet !== null);
assert.ok(Math.abs(eugeneNet - (2399.43 - 2405.859)) < 0.01, `eugeneNet=${eugeneNet}`);
const eugeneShown = fmtRealizedPnlDisplay(eugene0021, status, eugeneGroups);
assert.ok(!eugeneShown.includes("尚未補滿"), eugeneShown);
assert.ok(!eugeneShown.includes("restore incomplete"), eugeneShown);
assert.ok(eugeneShown.includes("賣出") || eugeneShown.includes("exit"), eugeneShown);
assert.equal(resolveAdminGroupActionKind(eugene0021, eugeneGroups), null);
assert.equal(adminGroupNeedsMarketRecover(eugene0021, eugeneGroups), false);
assert.equal(adminGroupNeedsMarketRecover(eugene0021), true);
assert.equal(resolveAdminGroupActionKind(eugene0021), "recover");

const eugene0020 = group({
  group_id: "0020",
  currency: "ETH",
  collateral_currency: "ETH",
  quantity: "1",
  covered_underlying_quantity: "1",
  short_instrument_name: "ETH-28AUG26-2300-C",
  cash_secured_group_id: "0031",
  cash_secured_group_ids: ["0031"],
  spot_exit_status: "filled",
  spot_exit_amount: "0.9208",
  spot_exit_quote_proceeds: "2299.4334",
  spot_exit_quote_proceeds_lifetime: "2299.4334",
  spot_exit_settlement_loss: "0.07917862",
  spot_restore_status: "skipped",
  realized_pnl: "-193",
  realized_pnl_collateral_native: "-0.077325",
});
const eugene0022 = group({
  group_id: "0022",
  currency: "BTC",
  collateral_currency: "BTC",
  quantity: "0.1",
  covered_underlying_quantity: "0.1",
  short_instrument_name: "BTC-28AUG26-77000-C",
  cash_secured_group_id: "0033",
  cash_secured_group_ids: ["0032", "0033"],
  spot_exit_status: "filled",
  spot_exit_amount: "0.0965",
  spot_exit_quote_proceeds: "7688.8399",
  spot_exit_quote_proceeds_lifetime: "7688.8399",
  spot_exit_settlement_loss: "0.00340308",
  spot_restore_status: "filled",
  spot_restore_amount: "0.1",
  spot_restore_quote_spent: "7856.6957",
  spot_restore_quote_spent_lifetime: "7856.6957",
  realized_pnl: "-261",
  realized_pnl_collateral_native: "-0.00327575",
});
const eugene0026Premium = group({
  group_id: "0026",
  currency: "ETH",
  collateral_currency: "USDC",
  strategy: "cash_secured",
  option_type: "put",
  cash_secured_from_group_id: "0021",
  entry_credit: "23.1350224",
  realized_pnl: "4.6873599",
  realized_pnl_collateral_native: "4.6873599",
  realized_close_debit: "18.4476625",
  realized_close_fee: "0.6476625",
  spot_restore_status: "filled",
  spot_restore_reason: "cash_secured_itm_assignment",
  spot_restore_amount: "1",
  spot_restore_quote_spent: "2405.859",
  spot_restore_quote_spent_lifetime: "2405.859",
  csp_premium_swap_status: "filled",
  csp_premium_swap_amount: "4.0396974",
  csp_premium_swap_native: "0.00167237",
});
const eugene0027 = group({
  group_id: "0027",
  currency: "BTC",
  collateral_currency: "USDC",
  strategy: "cash_secured",
  option_type: "put",
  cash_secured_from_group_id: "0022",
  entry_credit: "47.87675159",
  realized_pnl: "47.87675159",
  realized_close_debit: "0",
  realized_close_fee: "0",
  csp_premium_swap_status: "filled",
  csp_premium_swap_amount: "40.3435",
  csp_premium_swap_native: "0.0005",
});
const eugeneCompStatus = { ...status, underlying_index_usd: { BTC: 80000, ETH: 2400 } };
const eugene0021Net = 2399.43 - 2405.859;
const eugene0022Net = 7688.8399 - 7856.6957;

const eugene0020Comp = profitCompositionByBook(
  report,
  { closed: [eugene0020], open: [] },
  eugeneCompStatus,
);
assert.ok(Math.abs(eugene0020Comp.usdByBook?.USDT ?? 0) < 0.005, "unrestored #0020 must not count cover sale");
assert.ok(Math.abs(eugene0020Comp.earnedUsdByBook?.USDT ?? 0) < 0.005);

const eugene0021Comp = profitCompositionByBook(
  report,
  { closed: [eugene0021, eugene0026Premium], open: [] },
  eugeneCompStatus,
);
assert.ok(Math.abs((eugene0021Comp.usdByBook?.USDT ?? 0) - eugene0021Net) < 0.02, `0021 usdt=${eugene0021Comp.usdByBook?.USDT}`);
assert.ok(Math.abs((eugene0021Comp.earnedUsdByBook?.USDT ?? 0) - eugene0021Net) < 0.02);
assert.ok(
  Math.abs(eugene0021Comp.usdByBook?.USDC ?? 0) < 0.05,
  `child restore spend must not appear as USDC; usdc=${eugene0021Comp.usdByBook?.USDC}`,
);
assert.ok(
  Math.abs((eugene0021Comp.usdByBook?.ETH ?? 0) - 0.00167237 * 2400) < 0.05,
  `0021 eth=${eugene0021Comp.usdByBook?.ETH}`,
);

const eugene0022Only = profitCompositionByBook(
  report,
  { closed: [eugene0022], open: [] },
  eugeneCompStatus,
);
assert.ok(Math.abs((eugene0022Only.usdByBook?.USDT ?? 0) - eugene0022Net) < 0.05, `0022 usdt=${eugene0022Only.usdByBook?.USDT}`);
assert.ok(Math.abs((eugene0022Only.earnedUsdByBook?.USDT ?? 0) - eugene0022Net) < 0.05);
assert.ok(Math.abs(eugene0022Only.usdByBook?.BTC ?? 0) < 0.01, "ITM net must not mix into BTC premium");
assert.ok(Math.abs(eugene0022Only.usdByBook?.USDC ?? 0) < 0.01);

const eugeneWheelComp = profitCompositionByBook(
  report,
  { closed: [eugene0020, eugene0021, eugene0022, eugene0026Premium, eugene0027], open: [] },
  eugeneCompStatus,
);
assert.ok(
  Math.abs((eugeneWheelComp.usdByBook?.USDT ?? 0) - (eugene0021Net + eugene0022Net)) < 0.05,
  `combined usdt=${eugeneWheelComp.usdByBook?.USDT}`,
);
assert.ok(
  Math.abs((eugeneWheelComp.usdByBook?.USDC ?? 0) - (47.87675159 - 40.3435)) < 0.05,
  `usdc=${eugeneWheelComp.usdByBook?.USDC}`,
);
assert.ok(Math.abs((eugeneWheelComp.usdByBook?.BTC ?? 0) - 0.0005 * 80000) < 0.5);
const eugeneHtml = overviewProfitCompositionHtml({
  summary: { realized_pnl_usdc: "1" },
  profitCompositionByBook: eugene0022Only,
});
assert.ok(eugeneHtml.includes("USDT"), eugeneHtml);
assert.ok(eugeneHtml.includes("exit") || eugeneHtml.includes("賣出"), eugeneHtml);

const itmFoldComp = profitCompositionByBook(report, { closed: [itmFolded], open: [] }, status);
const foldNet = itmSpotExitNetUsdtForTotalProfit(itmFolded);
assert.ok(foldNet !== null);
assert.ok(Math.abs((itmFoldComp.usdByBook?.USDT ?? 0) - foldNet) < 0.05);
assert.ok(Math.abs((itmFoldComp.swappedUsdtByBook?.ETH ?? 0) - foldedUsdt) < 0.05);
assert.ok(Math.abs((itmFoldComp.usdByBook?.ETH ?? 0) - foldedUsdt) < 1);

const lifetimeUsd = sumLifetimeRealizedPnlUsdcByBook(report, { closed: [eugene0022], open: [] }, eugeneCompStatus);
const lifetimeEarned = sumLifetimeEarnedUsdByBook(report, { closed: [eugene0022], open: [] }, eugeneCompStatus);
assert.ok(Math.abs((lifetimeUsd?.USDT ?? 0) - eugene0022Net) < 0.05);
assert.ok(Math.abs((lifetimeEarned?.USDT ?? 0) - eugene0022Net) < 0.05);

const cspClosedCard = activityLifecycleCardHtml(
  cspSwapped,
  { ...status, underlying_index_usd: { BTC: 80000, ETH: 2500 } },
  { closed: [cspSwapped], open: [] },
);
assert.ok(cspClosedCard.includes("CSP swapped"), cspClosedCard);
assert.ok(cspClosedCard.includes("USDC"), cspClosedCard);
assert.ok(cspClosedCard.includes("ETH"), cspClosedCard);
assert.ok(cspClosedCard.includes("leftover") || cspClosedCard.includes("剩餘"), cspClosedCard);

assert.equal(groupHasFilledSpotRestore(eugene0022), true);
assert.equal(adminGroupNeedsMarketRecover(eugene0022, { closed: [eugene0022], open: [] }), false);
assert.equal(resolveAdminGroupActionKind(eugene0022, { closed: [eugene0022], open: [] }), null);
assert.equal(itmSpotRoundTripComplete(eugene0022, { closed: [eugene0022], open: [] }), true);
const eugene0022SkippedStatus = { ...eugene0022, spot_restore_status: "skipped" };
assert.equal(groupHasFilledSpotRestore(eugene0022SkippedStatus), true);
assert.equal(
  resolveAdminGroupActionKind(eugene0022SkippedStatus, { closed: [eugene0022SkippedStatus], open: [] }),
  null,
);

const eugene0031Open = group({
  group_id: "0031",
  status: "open",
  currency: "ETH",
  collateral_currency: "USDC",
  strategy: "cash_secured",
  option_type: "put",
  cash_secured_from_group_id: "0020",
});
assert.equal(adminGroupNeedsMarketRecover(eugene0020, { closed: [eugene0020], open: [] }), true);
assert.equal(resolveAdminGroupActionKind(eugene0020, { closed: [eugene0020], open: [] }), "recover");
assert.equal(
  resolveAdminGroupActionKind(eugene0020, { closed: [eugene0020], open: [eugene0031Open] }),
  "csp-abort-restore",
);
assert.equal(resolveAdminGroupActionKind(eugene0021, eugeneGroups), null);

const partialRestore = group({
  group_id: "partial",
  spot_exit_status: "filled",
  spot_exit_amount: "0.1",
  spot_exit_quote_proceeds: "7800",
  spot_restore_status: "filled",
  spot_restore_amount: "0.04",
  spot_restore_quote_spent: "3700",
  covered_underlying_quantity: "0.1",
  quantity: "0.1",
});
assert.ok(unrestoredSpotExitNative(partialRestore) > 0.05);
assert.equal(resolveAdminGroupActionKind(partialRestore, { closed: [partialRestore], open: [] }), "recover");

console.log("test_profit_disposition: ok");
