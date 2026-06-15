/**
 * Regression: ledger equity_by_book may store native units for BTC/ETH while
 * total_equity_usdc remains correct USDC.
 */
process.env.DASHBOARD_TEST_INVESTOR = "1";
globalThis.window = globalThis;
globalThis.window.__DASHBOARD_MODE__ = "investor";

const { overviewEquityBreakdown, isPortfolioBreakdownConsistent, num } = await import(
  "../../frontend/src/modules/domain.js"
);

const portfolio = {
  total_equity_usdc: "2798.55",
  equity_by_book: {
    BTC: "0.20455",
    ETH: "2.0787",
    USDC: "2637.74",
    USDT: "160.81",
  },
};

const statusWithSpot = {
  underlying_index_usd: { BTC: 105000, ETH: 3500 },
  accounts: {},
};

const { equityUsdByBook, equityNativeByBook } = overviewEquityBreakdown(portfolio, statusWithSpot);
const sum = ["BTC", "ETH", "USDC", "USDT"].reduce((s, b) => s + (num(equityUsdByBook[b]) ?? 0), 0);

console.log("breakdown", equityUsdByBook);
console.log("native", equityNativeByBook);
console.log("sum", sum.toFixed(2), "total", portfolio.total_equity_usdc);

const btcUsd = num(equityUsdByBook.BTC);
const ethUsd = num(equityUsdByBook.ETH);
if (btcUsd === null || btcUsd > 50) throw new Error(`BTC USD implausible: ${btcUsd}`);
if (ethUsd === null || ethUsd > 50) throw new Error(`ETH USD implausible: ${ethUsd}`);
if (Math.abs(sum - 2798.55) > 2) throw new Error(`sum ${sum} != total`);
if (!isPortfolioBreakdownConsistent(portfolio, statusWithSpot)) {
  throw new Error("breakdown should be consistent after reconcile");
}

const statusLive = {
  underlying_index_usd: { BTC: 105000, ETH: 3500 },
  accounts: {
    BTC: { equity: 0.000004 },
    ETH: { equity: 0.00012 },
  },
};
const live = overviewEquityBreakdown(portfolio, statusLive);
if (num(live.equityUsdByBook.BTC) > 1) {
  throw new Error(`live BTC should use account native: ${live.equityUsdByBook.BTC}`);
}

console.log("overview equity OK");
