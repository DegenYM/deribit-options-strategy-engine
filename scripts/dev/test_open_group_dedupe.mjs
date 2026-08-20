import assert from "node:assert/strict";
import { currentOpenRows, dedupeTradeGroups } from "../../frontend/src/modules/domain.js";

const g95 = {
  group_id: "0095",
  status: "open",
  account_name: "covered_call",
  currency: "BTC",
  quantity: "0.1",
  covered_underlying_quantity: "0.1",
  short_instrument_name: "BTC-28AUG26-73000-C",
  strategy: "covered_call",
};
const g96 = {
  group_id: "0096",
  status: "open",
  account_name: "covered_call",
  currency: "BTC",
  quantity: "0.1",
  covered_underlying_quantity: "0.1",
  short_instrument_name: "BTC-4SEP26-75000-C",
  strategy: "covered_call",
};

const status = {
  trade_groups: [g95, g96],
  positions: [
    { instrument_name: g95.short_instrument_name, account_name: "covered_call", size: "-0.1", kind: "option" },
    { instrument_name: g96.short_instrument_name, account_name: "covered_call", size: "-0.1", kind: "option" },
  ],
};

{
  const untagged95 = { ...g95, account_name: "" };
  const rows = currentOpenRows(status, { open: [untagged95, g96] });
  assert.equal(rows.length, 2, `expected 2 open rows, got ${rows.length}: ${rows.map((g) => g.group_id)}`);
  assert.deepEqual(rows.map((g) => g.group_id).sort(), ["0095", "0096"]);
}

{
  const rows = dedupeTradeGroups([g95, { ...g95, account_name: "" }, g96]);
  assert.equal(rows.length, 2);
}

{
  const rows = currentOpenRows(status, { open: [g95, g96] });
  assert.equal(rows.length, 2);
}

console.log("ok: open-group dedupe keeps two 0.1 BTC covers, not three");
