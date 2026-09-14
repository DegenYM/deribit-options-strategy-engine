/**
 * Activity OPEN / CLOSE tab: row filtering + session tab persistence helpers.
 */
import assert from "node:assert/strict";

const store = new Map();
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
globalThis.sessionStorage = {
  getItem(key) {
    return store.has(key) ? store.get(key) : null;
  },
  setItem(key, value) {
    store.set(String(key), String(value));
  },
  removeItem(key) {
    store.delete(key);
  },
};

const {
  ACTIVITY_TAB_CLOSED,
  ACTIVITY_TAB_OPEN,
  ACTIVITY_TAB_STORAGE_KEY,
  activityClosedRows,
  activityOpenRows,
  activityRowsForTab,
  normalizeActivityTab,
  readSavedActivityTab,
  saveActivityTab,
} = await import("../../frontend/src/modules/domain.js");

const openCover = {
  group_id: "0095",
  status: "open",
  account_name: "covered_call",
  currency: "BTC",
  quantity: "0.1",
  covered_underlying_quantity: "0.1",
  short_instrument_name: "BTC-28AUG26-73000-C",
  strategy: "covered_call",
  entry_timestamp_ms: 1_700_000_000_000,
};
const closedCover = {
  group_id: "0088",
  status: "closed",
  account_name: "covered_call",
  currency: "BTC",
  quantity: "0.1",
  covered_underlying_quantity: "0.1",
  short_instrument_name: "BTC-4SEP26-75000-C",
  strategy: "covered_call",
  entry_timestamp_ms: 1_690_000_000_000,
  closed_timestamp_ms: 1_695_000_000_000,
};

const status = { trade_groups: [openCover, closedCover], positions: [] };
const groups = { open: [openCover], closed: [closedCover] };
const report = { recent_closed_trades: [closedCover] };

{
  assert.equal(normalizeActivityTab("open"), ACTIVITY_TAB_OPEN);
  assert.equal(normalizeActivityTab("OPEN"), ACTIVITY_TAB_OPEN);
  assert.equal(normalizeActivityTab(""), ACTIVITY_TAB_OPEN);
  assert.equal(normalizeActivityTab(null), ACTIVITY_TAB_OPEN);
  assert.equal(normalizeActivityTab("closed"), ACTIVITY_TAB_CLOSED);
  assert.equal(normalizeActivityTab("CLOSE"), ACTIVITY_TAB_CLOSED);
  assert.equal(normalizeActivityTab("Close"), ACTIVITY_TAB_CLOSED);
}

{
  store.clear();
  assert.equal(readSavedActivityTab(), ACTIVITY_TAB_OPEN);
  assert.equal(saveActivityTab("close"), ACTIVITY_TAB_CLOSED);
  assert.equal(store.get(ACTIVITY_TAB_STORAGE_KEY), ACTIVITY_TAB_CLOSED);
  assert.equal(readSavedActivityTab(), ACTIVITY_TAB_CLOSED);
  assert.equal(saveActivityTab("OPEN"), ACTIVITY_TAB_OPEN);
  assert.equal(readSavedActivityTab(), ACTIVITY_TAB_OPEN);
}

{
  const openRows = activityOpenRows(status, groups);
  const closedRows = activityClosedRows(status, report, groups);
  assert.equal(openRows.length, 1, `open rows: ${openRows.map((g) => g.group_id)}`);
  assert.equal(openRows[0].group_id, "0095");
  assert.equal(closedRows.length, 1, `closed rows: ${closedRows.map((g) => g.group_id)}`);
  assert.equal(closedRows[0].group_id, "0088");
  assert.ok(openRows.every((g) => String(g.status).toLowerCase() !== "closed"));
  assert.ok(closedRows.every((g) => String(g.status).toLowerCase() === "closed"));
}

{
  const openTab = activityRowsForTab("open", status, report, groups);
  const closeTab = activityRowsForTab("CLOSE", status, report, groups);
  assert.deepEqual(
    openTab.map((g) => g.group_id),
    ["0095"]
  );
  assert.deepEqual(
    closeTab.map((g) => g.group_id),
    ["0088"]
  );
  assert.equal(activityRowsForTab("bogus", status, report, groups).length, openTab.length);
}

console.log("ok: activity OPEN/CLOSE tabs filter rows and remember session tab");
