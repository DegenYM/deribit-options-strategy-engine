/**
 * BOOK card / equity-composition split: multi-sub-account labels without
 * changing aggregated ``bookEquityUsdForDisplay``.
 */
import assert from "node:assert/strict";

globalThis.window = globalThis;
globalThis.window.__DASHBOARD_MODE__ = "ops";
globalThis.window.location = { search: "" };
globalThis.window.self = globalThis.window;
globalThis.window.top = globalThis.window;
globalThis.document = globalThis.document || {
  querySelector() {
    return null;
  },
  getElementById() {
    return null;
  },
};

const {
  bookEquityBySubAccount,
  bookEquityUsdForDisplay,
  bookSubAccountCountLabel,
  bookSubAccountSplitsHtml,
  overviewEquityCompositionHtml,
} = await import("../../frontend/src/modules/domain.js");

const { bookCardHtml } = await import("../../frontend/src/modules/render.js");

const multiStatus = {
  portfolio: {
    equity_by_book: { BTC: 7982.42, ETH: 2551.18, USDC: 9678.91, USDT: 0.76 },
    day_start_equity_by_book: { BTC: 361.61, ETH: 2613.85, USDC: 17377.15, USDT: 0.76 },
  },
  accounts: {
    BTC: { equity: 0.1035 },
    ETH: { equity: 1.047 },
    USDC: { equity: 9678.91 },
    USDT: { equity: 0.76 },
  },
  account_statuses: [
    {
      name: "covered_call",
      portfolio: {
        equity_by_book: { BTC: 7982.42, ETH: 2551.18, USDC: 7170.08, USDT: 0.76 },
      },
      accounts: {
        BTC: { equity: 0.1035 },
        ETH: { equity: 1.047 },
        USDC: { equity: 7170.08 },
        USDT: { equity: 0.76 },
      },
    },
    {
      name: "naked",
      portfolio: { equity_by_book: { USDC: 2508.83, USDT: 0 } },
      accounts: {
        BTC: { equity: 0 },
        ETH: { equity: 0 },
        USDC: { equity: 2508.83 },
        USDT: { equity: 0 },
      },
    },
  ],
};

const singleStatus = {
  portfolio: { equity_by_book: { USDC: 4100 } },
  accounts: { USDC: { equity: 4100 } },
  account_statuses: [
    {
      name: "covered_call",
      portfolio: { equity_by_book: { USDC: 4100 } },
      accounts: { USDC: { equity: 4100 } },
    },
  ],
};

assert.equal(bookEquityUsdForDisplay("USDC", multiStatus), 9678.91);
assert.equal(bookEquityUsdForDisplay("BTC", multiStatus), 7982.42);

const usdcRows = bookEquityBySubAccount("USDC", multiStatus);
assert.deepEqual(
  usdcRows.map((row) => row.name),
  ["covered_call", "naked"]
);
assert.equal(usdcRows[0].equityUsd, 7170.08);
assert.equal(usdcRows[1].equityUsd, 2508.83);

assert.deepEqual(
  bookEquityBySubAccount("BTC", multiStatus).map((row) => row.name),
  ["covered_call"]
);
assert.deepEqual(
  bookEquityBySubAccount("ETH", multiStatus).map((row) => row.name),
  ["covered_call"]
);
assert.deepEqual(
  bookEquityBySubAccount("USDT", multiStatus).map((row) => row.name),
  ["covered_call"]
);
assert.equal(bookEquityBySubAccount("USDC", singleStatus).length, 1);
assert.equal(bookSubAccountSplitsHtml(usdcRows).includes("covered_call"), true);
assert.equal(bookSubAccountSplitsHtml(bookEquityBySubAccount("BTC", multiStatus)), "");
assert.equal(bookSubAccountCountLabel(2), "2 sub-accounts");

const usdcCard = bookCardHtml("USDC", multiStatus);
assert.match(usdcCard, /USDC BOOK/);
assert.match(usdcCard, /\$9,678\.91/);
assert.match(usdcCard, /2 sub-accounts/);
assert.match(usdcCard, /covered_call/);
assert.match(usdcCard, /\$7,170\.08/);
assert.match(usdcCard, /naked/);
assert.match(usdcCard, /\$2,508\.83/);

const btcCard = bookCardHtml("BTC", multiStatus);
assert.match(btcCard, /BTC BOOK/);
assert.doesNotMatch(btcCard, /sub-accounts/);
assert.doesNotMatch(btcCard, /covered_call/);
assert.doesNotMatch(btcCard, /naked/);

const singleCard = bookCardHtml("USDC", singleStatus);
assert.match(singleCard, /\$4,100\.00/);
assert.doesNotMatch(singleCard, /sub-accounts/);
assert.doesNotMatch(singleCard, /naked/);

const composition = overviewEquityCompositionHtml(
  20213.27,
  { BTC: 0.1035, ETH: 1.047, USDC: 9678.91, USDT: 0.76 },
  { BTC: 7982.42, ETH: 2551.18, USDC: 9678.91, USDT: 0.76 },
  multiStatus
);
assert.match(composition, /2 sub-accounts/);
assert.match(composition, /covered_call/);
assert.match(composition, /naked/);
assert.equal((composition.match(/2 sub-accounts/g) || []).length, 1);

const singleComposition = overviewEquityCompositionHtml(
  4100,
  { USDC: 4100 },
  { USDC: 4100 },
  singleStatus
);
assert.doesNotMatch(singleComposition, /sub-accounts/);
assert.doesNotMatch(singleComposition, /naked/);

console.log("book card splits OK");
