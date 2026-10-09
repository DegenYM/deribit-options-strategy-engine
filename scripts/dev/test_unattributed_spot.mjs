import assert from "node:assert/strict";
import {
  profitCompositionByBook,
  sumLifetimeRealizedPnlUsdcAtSpot,
  sumWindowRealizedPnlUsdcAtSpot,
} from "../../frontend/src/modules/charts.js";
import {
  mergeStatusPayload,
  overviewProfitCompositionHtml,
  unattributedSpotPnl,
  unattributedSpotPnlUsd,
} from "../../frontend/src/modules/domain.js";

const DAY = 24 * 3600 * 1000;
const now = Date.now();
const status = {
  underlying_index_usd: { BTC: "80000", ETH: "2500" },
  unattributed_spot_pnl: {
    total_usd: "251.4273",
    by_book: { BTC: "191.5449", ETH: "61.121", USDC: "-1.2386" },
    events: [
      { ts_ms: now - 40 * DAY, book: "BTC", usd: "185.7049", label: "operator-unwind-premature-csp-swap" },
      { ts_ms: now - 40 * DAY, book: "ETH", usd: "61.121", label: "operator-unwind-premature-csp-swap" },
      { ts_ms: now - 5 * DAY, book: "BTC", usd: "5.84", label: "overbuy-unwind-0033" },
      { ts_ms: now - 60 * DAY, book: "USDC", usd: "-1.2386", label: "(unlabeled)" },
    ],
    trade_count: 31,
  },
};

assert.equal(unattributedSpotPnl({}), null);
assert.equal(unattributedSpotPnlUsd({}), null);
assert.equal(unattributedSpotPnlUsd(status), 251.4273);
assert.ok(Math.abs(unattributedSpotPnlUsd(status, { windowDays: 30, nowMs: now }) - 5.84) < 1e-9);

// No closed groups: Total profit is the unattributed line alone (lifetime vs window).
const groups = { open: [], closed: [] };
assert.equal(sumLifetimeRealizedPnlUsdcAtSpot(null, groups, status), 251.4273);
assert.ok(Math.abs(sumWindowRealizedPnlUsdcAtSpot(null, groups, status, 30) - 5.84) < 1e-9);
assert.equal(sumLifetimeRealizedPnlUsdcAtSpot(null, groups, { underlying_index_usd: {} }), null);

const comp = profitCompositionByBook(null, groups, status);
assert.equal(comp.unattributedSpotUsd, 251.4273);
const html = overviewProfitCompositionHtml({ summary: {}, profitCompositionByBook: comp });
assert.match(html, /Unattributed spot/);
assert.match(html, /\$251\.43/);

const noRow = overviewProfitCompositionHtml({
  summary: {},
  profitCompositionByBook: profitCompositionByBook(null, groups, { underlying_index_usd: {} }),
});
assert.doesNotMatch(noRow, /Unattributed spot/);

// Fast refresh without the field keeps the last exchange-backed value.
const merged = mergeStatusPayload(status, { portfolio: { total_equity_usdc: "1" } });
assert.equal(merged.unattributed_spot_pnl, status.unattributed_spot_pnl);

console.log("test_unattributed_spot: ok");
