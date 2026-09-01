const STORAGE_KEY = "admin.selectedInvestor";

const els = {
  list: document.getElementById("admin-investor-list"),
  hint: document.getElementById("admin-sidebar-hint"),
  countBadge: document.getElementById("admin-count-badge"),
  healthBadge: document.getElementById("admin-health-badge"),
  lastRefresh: document.getElementById("admin-last-refresh"),
  selectedName: document.getElementById("admin-selected-name"),
  selectedMeta: document.getElementById("admin-selected-meta"),
  openOps: document.getElementById("admin-open-ops"),
  openPortal: document.getElementById("admin-open-portal"),
  startBtn: document.getElementById("admin-frontend-start"),
  restartBtn: document.getElementById("admin-frontend-restart"),
  stopBtn: document.getElementById("admin-frontend-stop"),
  actionStatus: document.getElementById("admin-action-status"),
  frame: document.getElementById("admin-frame"),
  empty: document.getElementById("admin-frame-empty"),
  refresh: document.getElementById("admin-refresh"),
  tradeBar: document.getElementById("admin-trade-bar"),
  panicBtn: document.getElementById("admin-panic"),
  dialog: document.getElementById("admin-action-dialog"),
  dialogTitle: document.getElementById("admin-dialog-title"),
  dialogLead: document.getElementById("admin-dialog-lead"),
  dialogTargets: document.getElementById("admin-dialog-targets"),
  dialogConfirmWrap: document.getElementById("admin-dialog-confirm-wrap"),
  dialogConfirm: document.getElementById("admin-dialog-confirm"),
  dialogPreview: document.getElementById("admin-dialog-preview"),
  dialogError: document.getElementById("admin-dialog-error"),
  dialogPreviewBtn: document.getElementById("admin-dialog-preview-btn"),
  dialogLiveBtn: document.getElementById("admin-dialog-live-btn"),
  dialogCancel: document.getElementById("admin-dialog-cancel"),
  dialogForm: document.getElementById("admin-action-form"),
};

let catalog = { investors: [] };
let selectedId = null;
let actionBusy = false;

function formatTime(ts) {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleTimeString("en-GB", { hour12: false });
  } catch {
    return "—";
  }
}

function healthLabel(row) {
  const health = row?.health || {};
  if (health.ok) return "healthy";
  if (!row?.frontend_port) return "no port";
  return health.error || "down";
}

function healthClass(row) {
  const health = row?.health || {};
  if (health.ok) return "is-healthy";
  if (!row?.frontend_port) return "is-unknown";
  return "is-down";
}

function findInvestor(id) {
  return (catalog.investors || []).find((row) => row.investor_id === id) || null;
}

function persistSelection(id) {
  try {
    if (id) sessionStorage.setItem(STORAGE_KEY, id);
  } catch {
    /* ignore */
  }
}

function restoreSelection() {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function setActionStatus(message, kind) {
  if (!els.actionStatus) return;
  if (!message) {
    els.actionStatus.hidden = true;
    els.actionStatus.textContent = "";
    els.actionStatus.className = "admin-action-status";
    return;
  }
  els.actionStatus.hidden = false;
  els.actionStatus.textContent = message;
  els.actionStatus.className = `admin-action-status admin-action-status--${kind || "info"}`;
}

function setLink(el, url) {
  if (!el) return;
  if (!url) {
    el.hidden = true;
    el.removeAttribute("href");
    return;
  }
  el.hidden = false;
  el.href = url;
}

function renderList() {
  const rows = catalog.investors || [];
  if (els.hint) {
    els.hint.textContent = rows.length ? `${rows.length}` : "none";
  }
  if (!els.list) return;
  if (!rows.length) {
    els.list.innerHTML = `<p class="admin-empty-list">No investors in registry.toml.</p>`;
    return;
  }
  els.list.replaceChildren(
    ...rows.map((row) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `admin-investor-row ${healthClass(row)}`;
      if (row.investor_id === selectedId) button.classList.add("is-selected");
      button.dataset.investorId = row.investor_id;
      const port = row.frontend_port != null ? `:${row.frontend_port}` : "no port";
      button.innerHTML = `
        <span class="admin-dot" aria-hidden="true"></span>
        <span class="admin-investor-copy">
          <span class="admin-investor-name">${escapeHtml(row.display_name || row.investor_id)}</span>
          <span class="admin-investor-id">${escapeHtml(row.investor_id)} · ${escapeHtml(port)}</span>
        </span>
        <span class="admin-investor-state">${escapeHtml(healthLabel(row))}</span>
      `;
      button.addEventListener("click", () => selectInvestor(row.investor_id, { reloadFrame: true }));
      return button;
    })
  );
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function embedUrl(opsUrl) {
  try {
    const url = new URL(opsUrl, window.location.href);
    url.searchParams.set("embed", "1");
    return url.toString();
  } catch {
    return opsUrl;
  }
}

function resetFrameHeight() {
  if (!els.frame) return;
  els.frame.style.removeProperty("height");
}

function showEmpty(message) {
  if (els.frame) {
    els.frame.hidden = true;
    els.frame.removeAttribute("src");
    resetFrameHeight();
  }
  if (els.empty) {
    els.empty.hidden = false;
    els.empty.textContent = message;
  }
}

function showFrame(url, { reload = false } = {}) {
  if (els.empty) els.empty.hidden = true;
  if (!els.frame) return;
  const src = embedUrl(url);
  els.frame.hidden = false;
  if (reload || els.frame.getAttribute("src") !== src) {
    resetFrameHeight();
    els.frame.src = src;
  }
}

function isLoopbackOrigin(origin) {
  try {
    const parsed = new URL(origin);
    return parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost";
  } catch {
    return false;
  }
}

function onEmbedMessage(event) {
  if (!isLoopbackOrigin(event.origin)) return;
  const data = event.data || {};
  if (data.source !== "deribit-dashboard") return;
  if (data.type === "embed-height") {
    const height = Number(data.height);
    if (!Number.isFinite(height) || height < 200 || !els.frame || els.frame.hidden) return;
    els.frame.style.height = `${Math.ceil(height)}px`;
    return;
  }
  if (data.type === "admin-group-action") {
    const kind = data.kind === "recover" ? "recover" : "close";
    openTradeDialog(kind, {
      groupId: String(data.group_id || ""),
      account: String(data.account || ""),
      estimates: data.estimates || null,
    });
  }
}

function selectInvestor(id, { reloadFrame = false } = {}) {
  const row = findInvestor(id);
  selectedId = row ? row.investor_id : null;
  persistSelection(selectedId);
  renderList();
  updateToolbar(row);
  if (!row) {
    showEmpty("Select an investor on the left to embed their ops dashboard.");
    return;
  }
  if (!row.ops_url) {
    showEmpty(`${row.display_name} has no frontend_port, so the dashboard cannot be embedded.`);
    return;
  }
  if (!row.health?.ok) {
    showEmpty(
      `${row.display_name} frontend is not responding. Use Start frontend, or run ./bot investor frontend start --investor ${row.investor_id}`
    );
    return;
  }
  showFrame(row.ops_url, { reload: reloadFrame });
}

function updateToolbar(row) {
  if (!row) {
    if (els.selectedName) els.selectedName.textContent = "Select an investor";
    if (els.selectedMeta) els.selectedMeta.textContent = "List comes from registry.toml";
    setLink(els.openOps, null);
    setLink(els.openPortal, null);
    for (const btn of [els.startBtn, els.restartBtn, els.stopBtn]) {
      if (btn) btn.hidden = true;
    }
    if (els.tradeBar) els.tradeBar.hidden = true;
    return;
  }
  const port = row.frontend_port != null ? `127.0.0.1:${row.frontend_port}` : "no port";
  const host = row.hostname || "no hostname";
  const flags = [
    row.frontend_enabled ? "frontend on" : "frontend off",
    row.live_enabled ? "live on" : "live off",
    `${row.account_count || 0} accounts`,
  ].join(" · ");
  if (els.selectedName) els.selectedName.textContent = row.display_name || row.investor_id;
  if (els.selectedMeta) els.selectedMeta.textContent = `${row.investor_id} · ${port} · ${host} · ${flags}`;
  setLink(els.openOps, row.ops_url);
  setLink(els.openPortal, row.portal_url);
  if (els.startBtn) els.startBtn.hidden = false;
  if (els.restartBtn) els.restartBtn.hidden = false;
  if (els.stopBtn) els.stopBtn.hidden = false;
  if (els.tradeBar) els.tradeBar.hidden = false;
}

async function loadCatalog({ keepFrame = false } = {}) {
  if (els.hint) els.hint.textContent = "Loading…";
  const response = await fetch("/api/admin/investors", { cache: "no-store" });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `HTTP ${response.status}`);
  }
  catalog = await response.json();
  const rows = catalog.investors || [];
  if (els.countBadge) els.countBadge.textContent = `investors: ${catalog.investor_count ?? rows.length}`;
  if (els.healthBadge) els.healthBadge.textContent = `healthy: ${catalog.healthy_count ?? 0}/${rows.length}`;
  if (els.lastRefresh) els.lastRefresh.textContent = `Last refresh: ${formatTime(catalog.generated_at_ms)}`;

  const preferred = selectedId || restoreSelection();
  const next = rows.some((row) => row.investor_id === preferred)
    ? preferred
    : rows[0]?.investor_id || null;
  selectInvestor(next, { reloadFrame: !keepFrame });
}

async function runFrontendAction(action) {
  const row = findInvestor(selectedId);
  if (!row || actionBusy) return;
  const labels = { start: "Start", stop: "Stop", restart: "Restart" };
  const verb = labels[action] || action;
  if (!window.confirm(`${verb} frontend for ${row.display_name} (${row.investor_id})?`)) {
    return;
  }
  actionBusy = true;
  setActionStatus(`${verb} ${row.investor_id}…`, "info");
  try {
    const response = await fetch(`/api/admin/frontend/${encodeURIComponent(row.investor_id)}/${action}`, {
      method: "POST",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      const detail = payload.detail || payload.result?.message || `HTTP ${response.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    setActionStatus(`${verb} done: ${payload.result?.message || payload.result?.state || "ok"}`, "ok");
    await loadCatalog({ keepFrame: false });
  } catch (err) {
    setActionStatus(`${verb} failed: ${err.message || err}`, "error");
  } finally {
    actionBusy = false;
  }
}

const TRADE_KINDS = {
  recover: {
    title: "Recover market",
    lead: "Emergency market buyback of ITM-sold cover. If this group still has a resting auto limit, live submit cancels it first.",
    endpoint: "spot-restore",
    empty: "No recoverable groups (need closed + unrestored).",
    pick: "restore",
  },
  close: {
    title: "Close position",
    lead: "Market-close the selected open group (close-position --group-id).",
    endpoint: "close-position",
    empty: "No open groups.",
    pick: "open",
  },
  panic: {
    title: "Panic close",
    lead: "panic-close: cancel resting orders, flatten the account, and write cooldown. Pick one account, or run on all accounts.",
    endpoint: "panic-close",
    empty: "No operational accounts.",
    pick: "account",
  },
};

let tradeKind = null;
let tradeTargets = null;
let previewed = false;
let cardEstimates = null;

function setDialogError(message) {
  if (!els.dialogError) return;
  if (!message) {
    els.dialogError.hidden = true;
    els.dialogError.textContent = "";
    return;
  }
  els.dialogError.hidden = false;
  els.dialogError.textContent = message;
}

function formatApiError(detail, fallback) {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || JSON.stringify(item)).join("; ");
  }
  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      /* ignore */
    }
  }
  return fallback;
}

function setDialogPreview(text) {
  if (!els.dialogPreview) return;
  const value = String(text || "").trim();
  els.dialogPreview.hidden = !value;
  els.dialogPreview.textContent = value;
}

function formatNum(value, digits) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return n.toFixed(digits);
}

function formatUsd(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (n > 0) return `+$${n.toFixed(2)}`;
  if (n < 0) return `-$${Math.abs(n).toFixed(2)}`;
  return `$${n.toFixed(2)}`;
}

function formatPx(value, book) {
  const text = formatNum(value, 8);
  if (!text) return "—";
  const trimmed = text.replace(/\.?0+$/, "") || "0";
  return book ? `${trimmed} ${book}` : trimmed;
}

function formatFee(usd, native, book) {
  const usdText = Number.isFinite(Number(usd)) ? `$${Number(usd).toFixed(2)}` : null;
  const nativeText = formatNum(native, 8);
  if (usdText && nativeText) return `${usdText}  (${nativeText.replace(/\.?0+$/, "")} ${book || ""})`.trim();
  return usdText || (nativeText ? `${nativeText.replace(/\.?0+$/, "")} ${book || ""}`.trim() : "—");
}

function formatSignedNative(value, book) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const body = n.toFixed(8).replace(/\.?0+$/, "") || "0";
  const signed = n > 0 ? `+${body}` : body;
  return book ? `${signed} ${book}` : signed;
}

function formatPnl(usd, native, book) {
  const usdText = formatUsd(usd);
  if (!Number.isFinite(Number(native))) return usdText;
  return `${usdText}  (${formatSignedNative(native, book)})`;
}

function pickEstimate(plan, key, altKeys = []) {
  const card = cardEstimates || {};
  for (const name of [key, ...altKeys]) {
    if (card[name] != null && card[name] !== "") return card[name];
  }
  if (plan?.[key] != null && plan[key] !== "") return plan[key];
  return null;
}

function formatPreviewText(body) {
  const plan = body?.plan;
  if (!plan || typeof plan !== "object") {
    return JSON.stringify(body, null, 2);
  }
  const book = pickEstimate(plan, "book", ["collateral"]) || plan.collateral || plan.currency || "";
  const lines = ["PREVIEW — no order sent"];
  if (body.group_id) {
    lines.push(`#${body.group_id}  ${plan.short_instrument_name || plan.instrument_name || pickEstimate(plan, "instrument") || ""}`);
  }
  if (body.account) lines.push(`account: ${body.account}`);
  if (plan.quantity) lines.push(`qty ${plan.quantity} · ${plan.order_type || "market"}`);
  if (plan.unrestored_amount) lines.push(`unrestored ${plan.unrestored_amount} ${plan.currency || book}`);

  const closePx = pickEstimate(plan, "mark_price", ["est_close_price"]);
  const entryPx = pickEstimate(plan, "entry_price");
  const feeUsd = pickEstimate(plan, "est_close_fee_usd", ["est_close_fee_usdc"]);
  const feeNat = pickEstimate(plan, "est_close_fee_native");
  const entryCredit = pickEstimate(plan, "entry_credit_usd", ["entry_credit_usdc"]);
  const closeCost = plan.est_close_cost_usdc;
  const creditN = Number(entryCredit);
  const costN = Number(closeCost);
  const qtyN = Number(pickEstimate(plan, "quantity") ?? plan.quantity);
  const entryN = Number(entryPx);
  const closeN = Number(closePx);
  const feeNatN = Number(feeNat);
  let pnlUsd = pickEstimate(plan, "est_pnl_usd", ["est_pnl_usdc"]);
  let pnlNat = pickEstimate(plan, "est_pnl_native");
  if (Number.isFinite(creditN) && Number.isFinite(costN)) {
    pnlUsd = creditN - costN;
  }
  if (Number.isFinite(qtyN) && qtyN > 0 && Number.isFinite(entryN) && Number.isFinite(closeN)) {
    pnlNat = (entryN - closeN) * qtyN;
    if (Number.isFinite(feeNatN)) pnlNat -= Math.abs(feeNatN);
  }
  const breakeven = pickEstimate(plan, "breakeven_price");
  const remaining = pickEstimate(plan, "remaining_proceeds_usdt");

  if (body.kind === "spot_restore" || plan.action === "spot-restore") {
    lines.push("");
    lines.push(`Est. buy price     market`);
    lines.push(`Breakeven          ${formatPx(breakeven, "USDT")}`);
    lines.push(`Remaining proceeds ${remaining == null ? "—" : `$${Number(remaining).toFixed(2)}`}`);
    lines.push(`Est. PnL           unknown until market fill`);
  } else if (body.kind !== "panic_close") {
    lines.push("");
    lines.push(`Est. close price   ${formatPx(closePx, book)}`);
    lines.push(`Entry price        ${formatPx(entryPx, book)}`);
    lines.push(`Est. close fee     ${formatFee(feeUsd, feeNat, book)}`);
    lines.push(`Est. PnL           ${formatPnl(pnlUsd, pnlNat, book)}`);
    if (entryCredit != null || closeCost != null) {
      lines.push("");
      lines.push(`Entry credit       ${entryCredit == null ? "—" : `$${Number(entryCredit).toFixed(2)}`}`);
      lines.push(`Est. close cost    ${closeCost == null ? "—" : `$${Number(closeCost).toFixed(2)}`}`);
    }
  }

  if (Array.isArray(plan.accounts)) {
    lines.push("");
    for (const row of plan.accounts) {
      if (row?.error) {
        lines.push(`${row.account}: ${row.error}`);
        continue;
      }
      lines.push(`${row.account || "account"}`);
      const groups = Array.isArray(row.open_groups) ? row.open_groups : [];
      if (!groups.length) {
        lines.push("  no open groups");
        continue;
      }
      for (const group of groups) {
        if (typeof group === "string") {
          lines.push(`  #${group}`);
          continue;
        }
        lines.push(
          `  #${group.group_id}  ${group.short_instrument_name || ""}  PnL ${formatUsd(group.est_pnl_usdc)}  fee ${formatFee(group.est_close_fee_usdc, group.est_close_fee_native, group.collateral)}`
        );
      }
    }
  }
  lines.push("");
  lines.push("Live market fill can differ from this estimate.");
  return lines.join("\n");
}

function selectedTradePayload() {
  const checked = els.dialogTargets?.querySelector("input[name='admin-target']:checked");
  const payload = { account: checked?.dataset.account || null };
  if (tradeKind !== "panic") payload.group_id = checked?.dataset.groupId || "";
  if (tradeKind === "panic" && checked?.dataset.account === "") payload.account = null;
  return payload;
}

function renderTradeTargets(kind) {
  const spec = TRADE_KINDS[kind];
  const rows =
    kind === "recover"
      ? tradeTargets?.restore_candidates || []
      : kind === "close"
        ? tradeTargets?.open_groups || []
        : [
            { slug: "", display_name: "All accounts", strategy: "all" },
            ...(tradeTargets?.accounts || []),
          ];
  if (!els.dialogTargets) return false;
  if (!rows.length) {
    els.dialogTargets.innerHTML = `<p class="admin-empty-list">${escapeHtml(spec.empty)}</p>`;
    return false;
  }
  els.dialogTargets.replaceChildren(
    ...rows.map((row, index) => {
      const label = document.createElement("label");
      label.className = "admin-dialog-option";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "admin-target";
      input.checked = index === 0;
      if (kind === "panic") {
        input.dataset.account = row.slug || "";
        label.append(input, document.createTextNode(row.display_name || row.slug || "All accounts"));
        const small = document.createElement("small");
        small.textContent = row.slug ? `${row.slug} · ${row.strategy}` : "Runs panic-close once per operational account";
        label.append(small);
      } else {
        input.dataset.account = row.account || "";
        input.dataset.groupId = row.group_id || "";
        const title =
          kind === "recover"
            ? `#${row.group_id}  Recover ${row.unrestored_amount} ${row.currency}`
            : `#${row.group_id}  ${row.short_instrument_name || ""}`;
        label.append(input, document.createTextNode(title));
        const small = document.createElement("small");
        small.textContent =
          kind === "recover"
            ? `${row.account} · ${row.instrument_name} · status ${row.spot_restore_status || "none"}`
            : `${row.account} · qty ${row.quantity} · ${row.currency}`;
        label.append(small);
      }
      return label;
    })
  );
  return true;
}

function setPresetTarget(preset) {
  if (!els.dialogTargets) return;
  els.dialogTargets.innerHTML = "";
  const input = document.createElement("input");
  input.type = "radio";
  input.name = "admin-target";
  input.checked = true;
  input.hidden = true;
  input.dataset.account = preset.account || "";
  input.dataset.groupId = preset.groupId || "";
  const note = document.createElement("p");
  note.className = "admin-dialog-lead";
  note.textContent = `#${preset.groupId}${preset.account ? ` · ${preset.account}` : ""}`;
  els.dialogTargets.append(input, note);
}

async function openTradeDialog(kind, preset = null) {
  const row = findInvestor(selectedId);
  if (!row || actionBusy) return;
  tradeKind = kind;
  previewed = false;
  tradeTargets = null;
  cardEstimates = preset?.estimates || null;
  const spec = TRADE_KINDS[kind];
  if (els.dialogTitle) els.dialogTitle.textContent = `${spec.title} · ${row.display_name}`;
  if (els.dialogLead) els.dialogLead.textContent = spec.lead;
  setDialogPreview("");
  if (els.dialogConfirm) els.dialogConfirm.value = "";
  if (els.dialogConfirmWrap) els.dialogConfirmWrap.hidden = true;
  if (els.dialogLiveBtn) els.dialogLiveBtn.disabled = true;
  setDialogError("");
  if (els.dialogTargets) els.dialogTargets.textContent = preset?.groupId ? "" : "Loading targets…";
  els.dialog?.showModal();
  if (preset?.groupId) {
    setPresetTarget(preset);
    if (els.dialogPreviewBtn) els.dialogPreviewBtn.disabled = false;
    await postTrade({ live: false });
    return;
  }
  try {
    const response = await fetch(`/api/admin/investors/${encodeURIComponent(row.investor_id)}/targets`, {
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    tradeTargets = payload;
    if (!renderTradeTargets(kind)) {
      if (els.dialogPreviewBtn) els.dialogPreviewBtn.disabled = true;
    } else if (els.dialogPreviewBtn) {
      els.dialogPreviewBtn.disabled = false;
    }
  } catch (err) {
    setDialogError(`Failed to load targets: ${err.message || err}`);
  }
}

async function postTrade({ live }) {
  const row = findInvestor(selectedId);
  const spec = TRADE_KINDS[tradeKind];
  if (!row || !spec) {
    setDialogError("No investor or action selected.");
    return;
  }
  if (actionBusy) return;
  const payload = selectedTradePayload();
  if (tradeKind !== "panic" && !payload.group_id) {
    setDialogError("Select a group first.");
    return;
  }
  if (live && !previewed) {
    setDialogError("Preview first.");
    return;
  }
  if (live) {
    const typed = String(els.dialogConfirm?.value || "").trim();
    if (typed !== "LIVE") {
      setDialogError("Type LIVE in the box to submit a live order.");
      return;
    }
    payload.live = true;
    payload.confirm = "LIVE";
  }
  setDialogError("");
  setDialogPreview(live ? "Submitting live order…" : "Previewing…");
  if (els.dialogPreviewBtn) els.dialogPreviewBtn.disabled = true;
  if (els.dialogLiveBtn) els.dialogLiveBtn.disabled = true;
  actionBusy = true;
  try {
    const response = await fetch(`/api/admin/investors/${encodeURIComponent(row.investor_id)}/${spec.endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(formatApiError(body.detail, `HTTP ${response.status}`));
    }
    setDialogPreview(live ? JSON.stringify(body, null, 2) : formatPreviewText(body));
    if (!live) {
      previewed = true;
      if (els.dialogConfirmWrap) els.dialogConfirmWrap.hidden = false;
      if (els.dialogLiveBtn) els.dialogLiveBtn.disabled = false;
      setActionStatus(`${spec.title} preview ready. Confirm to submit.`, "info");
    } else {
      setActionStatus(`${spec.title} live order submitted.`, "ok");
      if (els.frame && !els.frame.hidden) {
        els.frame.src = els.frame.src;
      }
    }
  } catch (err) {
    setDialogError(`${live ? "Live" : "Preview"} failed: ${err.message || err}`);
  } finally {
    actionBusy = false;
    if (els.dialogPreviewBtn) els.dialogPreviewBtn.disabled = false;
    if (els.dialogLiveBtn) els.dialogLiveBtn.disabled = !previewed || live;
  }
}

function bind() {
  window.addEventListener("message", onEmbedMessage);
  els.refresh?.addEventListener("click", () => {
    loadCatalog({ keepFrame: true }).catch((err) => {
      setActionStatus(`Refresh failed: ${err.message || err}`, "error");
    });
  });
  els.startBtn?.addEventListener("click", () => runFrontendAction("start"));
  els.restartBtn?.addEventListener("click", () => runFrontendAction("restart"));
  els.stopBtn?.addEventListener("click", () => runFrontendAction("stop"));
  els.panicBtn?.addEventListener("click", () => openTradeDialog("panic"));
  els.dialogForm?.addEventListener("submit", (event) => {
    event.preventDefault();
  });
  els.dialogCancel?.addEventListener("click", () => els.dialog?.close());
  els.dialogPreviewBtn?.addEventListener("click", (event) => {
    event.preventDefault();
    postTrade({ live: false });
  });
  els.dialogLiveBtn?.addEventListener("click", (event) => {
    event.preventDefault();
    postTrade({ live: true });
  });
}

bind();
loadCatalog().catch((err) => {
  if (els.hint) els.hint.textContent = "Load failed";
  if (els.empty) {
    els.empty.hidden = false;
    els.empty.textContent = `Could not load investors: ${err.message || err}`;
  }
});
