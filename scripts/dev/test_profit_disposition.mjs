import assert from "node:assert/strict";
import {
  aggregateProfitDisposition,
  annualizeRealizedApr,
  computeLifetimeRealizedApr,
  computeWindowRealizedApr,
  profitCompositionByBook,
  sumLifetimeRealizedPnlUsdcByBook,
} from "../../frontend/src/modules/charts.js";
import {
  emptyProfitDisposition,
  fmtProfitNative,
  fmtProfitUsdt,
  profitDispositionForGroup,
  profitSwapDisplayAvg,
  realizedPnlDisplayUsdc,
  resolvePremiumSweepBookDisplay,
  summarizeProfitDisposition,
  truncateDecimal,
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

const disposition = aggregateProfitDisposition(report, groups, status);
const summary = summarizeProfitDisposition(disposition);
assert.ok(summary.spotEarned.BTC > 0);
assert.ok(summary.usdtSwapped > 0);

const composition = profitCompositionByBook(report, groups, status);
assert.ok(composition.earnedUsdByBook.BTC > composition.swappedUsdtByBook.BTC);
assert.ok(Math.abs(composition.earnedUsdByBook.BTC - 113.4) < 0.1);
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
assert.ok(Math.abs(fullySold.earnedUsdByBook.BTC - 63) < 0.1);
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
assert.ok(Math.abs(withdrawnCase.swappedUsdtByBook.BTC - 0.0806) < 0.01);
assert.ok(Math.abs(withdrawnCase.usdByBook.BTC - 0.0806) < 0.01);

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
assert.ok(Math.abs(actualProceedsField.swappedUsdtByBook.BTC - 0.08) < 0.01);
assert.ok(Math.abs(actualProceedsField.usdByBook.BTC - 0.08) < 0.01);

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
assert.ok(Math.abs(dustLifetime.swappedUsdtByBook.BTC - 0.08355164) < 0.0001);
assert.equal(dustLifetime.nativeByBook.BTC, 0);

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
assert.ok(Math.abs(jackLike.swappedUsdtByBook.BTC - 0.08355164) < 0.0001);
assert.ok(Math.abs(jackLike.swappedUsdtByBook.ETH - 0.0215) < 0.0001);

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
assert.ok(Math.abs(maLike.spotEarned.BTC - 0.00285) < 1e-10);
assert.ok(Math.abs(maLike.spotSold.BTC - 0.0029) < 1e-10);
assert.ok(Math.abs(maLike.spotHeld.BTC) < 1e-10);
assert.ok(Math.abs(maLike.spotSoldQuote.BTC - 176.5656) < 0.0001);
assert.ok(Math.abs(maLike.usdtSwapped - 176.5656) < 0.0001);
assert.ok(Math.abs(maLike.spotSoldAvg.BTC - 60884.68) < 0.01);
assert.equal(profitSwapDisplayAvg("BTC", 176.5656, 0.0029), 60884.68);
assert.equal(profitSwapDisplayAvg("BTC", 161.9496, 0.0027), 59981.33);

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
assert.ok(Math.abs(jackOversell.spotEarned.BTC - 0.0063078) < 1e-10);
assert.ok(Math.abs(jackOversell.spotSold.BTC - 0.0065) < 1e-10);
// Exchange execution VWAP (net USDT ÷ net native sold), not journal attribution drift.
assert.ok(Math.abs(jackOversell.spotSoldQuote.BTC - 411.449) < 0.01);
assert.ok(Math.abs(jackOversell.spotSoldAvg.BTC - 63299.84) < 0.01);
assert.ok(Math.abs(jackOversell.spotEarned.ETH - 0.08089459) < 1e-10);
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
// usdByBook stays journal per-group; composition display uses swappedUsdt (see entryUsd below).
assert.ok(Math.abs(jackComposition.usdByBook.ETH - 108.07) < 0.5);

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
const sampleDays = (closedMs - entryMs) / (24 * 3600 * 1000);
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
const expectedApr = annualizeRealizedApr(1.43, sampleDays, capital);
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
const expectedWindowApr = annualizeRealizedApr(1.43, 30, capital);
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
assert.equal(fmtProfitNative("BTC", 0.0000965), "0.0000965");
assert.equal(fmtProfitUsdt(113.3305), "$113.3305");
assert.equal(fmtProfitUsdt(113.3399), "$113.3399");
assert.equal(truncateDecimal(62961.389, 2), 62961.38);

console.log("test_profit_disposition: ok");
