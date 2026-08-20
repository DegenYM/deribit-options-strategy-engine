const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function show(id, on) {
  $(id).classList.toggle("hidden", !on);
}

function money(plan) {
  return `USD ${plan.price_usd_month} / NT$${plan.price_twd_month.toLocaleString()}`;
}

function fmtPrice(value) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return n.toLocaleString("en-US", { maximumFractionDigits: n >= 1000 ? 0 : 2 });
}

function setChip(el, text, variant) {
  el.className = `inv-chip inv-chip--${variant}`;
  el.textContent = text;
}

function desiredVariant(desired) {
  if (desired === "live") return "success";
  if (desired === "dry_run") return "info";
  if (desired === "paused") return "warning";
  if (desired === "panic") return "danger";
  return "neutral";
}

function stampRefresh() {
  const now = new Date();
  $("lastRefresh").textContent = `上次更新：${now.toLocaleTimeString()}`;
}

async function loadPlans() {
  const { plans } = await api("/api/plans");
  $("plansMount").innerHTML = plans
    .map(
      (plan) => `<article class="plan">
        <b>${plan.name}</b>
        <div class="plan-price">${money(plan)}</div>
        <p>${plan.blurb_zh}</p>
        <small>${plan.disclaimer_zh}</small>
      </article>`
    )
    .join("");
  $("subscribeRow").innerHTML = plans
    .map(
      (plan) =>
        `<button type="button" class="ds-btn" data-plan="${plan.id}">訂閱 ${plan.name}</button>`
    )
    .join("");
  document.querySelectorAll("#subscribeRow button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api("/api/billing/dev-subscribe", {
          method: "POST",
          body: JSON.stringify({ plan_id: btn.dataset.plan }),
        });
        await refreshApp();
      } catch (err) {
        alert(err.message);
      }
    });
  });
}

async function refreshApp() {
  const me = await api("/api/auth/me");
  $("who").textContent = `${me.email} · ${me.approved ? "已核准" : "waitlist"}`;
  const dash = await api("/api/dashboard");
  const bot = await api("/api/bot/status");
  const bill = await api("/api/billing");
  $("billStatus").textContent = bill.plan_id
    ? `目前方案 ${bill.plan_id}（${bill.status}）`
    : "尚未訂閱。開發模式可用下方按鈕開通。";

  const btc = dash.market?.btc_usd;
  const eth = dash.market?.eth_usd;
  $("headerSpotBtc").textContent = fmtPrice(btc);
  $("headerSpotEth").textContent = fmtPrice(eth);

  setChip($("whoBadge"), `帳戶 · ${me.email}`, me.approved ? "success" : "warning");
  setChip($("planBadge"), `方案 · ${bill.plan_id || "未訂閱"}`, bill.plan_id ? "info" : "neutral");
  setChip($("desiredBadge"), `狀態 · ${bot.desired}`, desiredVariant(bot.desired));
  setChip($("tierBadge"), `Risk · ${bot.risk_tier || "—"}`, "warning");
  setChip($("credsBadge"), `金鑰 · ${bot.has_credentials ? "已綁定" : "未設定"}`, bot.has_credentials ? "success" : "neutral");
  $("statusCluster").classList.remove("hidden");

  $("botMeta").innerHTML = [
    ["desired", bot.desired],
    ["tier", bot.risk_tier],
    ["coins", (bot.coins || []).join(", ") || "—"],
    ["live 解鎖", bot.live_unlocked ? "是" : "否"],
    ["金鑰", bot.client_id ? `${bot.client_id} · ***${bot.secret_last4 || ""}` : "未設定"],
    ["現貨 BTC", fmtPrice(btc)],
    ["現貨 ETH", fmtPrice(eth)],
  ]
    .map(
      ([label, value]) =>
        `<div class="stat-tile"><div class="label">${label}</div><div class="value">${value}</div></div>`
    )
    .join("");

  const groups = dash.bot.open_groups || [];
  $("groupMeta").textContent = `${groups.length} open`;
  $("groups").innerHTML = groups.length
    ? groups
        .map((g) => {
          const book = String(g.currency || "").toLowerCase();
          const accent = book === "btc" ? " open-position-card--btc" : "";
          return `<article class="open-position-card${accent}">
            <div class="open-position-header">
              <h3>${g.short_instrument}</h3>
              <span class="open-book-pill">${g.currency || "—"}</span>
              <span class="open-book-pill">call</span>
            </div>
            <div class="open-position-detail-row">
              <span>qty ${g.quantity ?? "—"}</span>
              <span>strike ${g.strike ?? "—"}</span>
              <span>DTE ${g.dte_days ?? "—"}</span>
              <span>premium ${g.entry_credit ?? "—"}</span>
            </div>
          </article>`;
        })
        .join("")
    : `<div class="open-empty-state">目前沒有開放中的 covered call。</div>`;
  $("keyStatus").textContent = bot.has_credentials ? "已保存（密鑰不回顯）" : "尚未綁定";
  $("riskTier").value = bot.risk_tier || "low";
  document.querySelectorAll("input[name=coin]").forEach((el) => {
    el.checked = (bot.coins || []).includes(el.value);
  });
  $("sweep").checked = !!bot.profit_sweep;
  stampRefresh();
}

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const requested = await api("/api/auth/magic-link", {
    method: "POST",
    body: JSON.stringify({ email: $("email").value }),
  });
  $("loginHint").textContent = "開發模式：正在使用回傳的 magic token 登入…";
  await api("/api/auth/verify", { method: "POST", body: JSON.stringify({ token: requested.dev_token }) });
  show("gate", false);
  show("app", true);
  $("logoutBtn").classList.remove("hidden");
  await refreshApp();
});

$("logoutBtn").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  location.reload();
});

$("refreshNow").addEventListener("click", async () => {
  try {
    await api("/api/auth/me");
    await refreshApp();
  } catch {
    $("loginHint").textContent = "尚未登入。";
  }
});

$("keyForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/bot/credentials", {
      method: "POST",
      body: JSON.stringify({
        client_id: $("clientId").value,
        client_secret: $("clientSecret").value,
      }),
    });
    $("clientSecret").value = "";
    await refreshApp();
  } catch (err) {
    alert(err.message);
  }
});

$("pingBtn").addEventListener("click", async () => {
  try {
    const result = await api("/api/bot/credentials/ping", { method: "POST" });
    alert(result.ok ? "Deribit ping 成功" : JSON.stringify(result));
  } catch (err) {
    alert(err.message);
  }
});

$("settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const coins = [...document.querySelectorAll("input[name=coin]:checked")].map((el) => el.value);
  try {
    await api("/api/bot/settings", {
      method: "POST",
      body: JSON.stringify({
        risk_tier: $("riskTier").value,
        coins,
        profit_sweep: $("sweep").checked,
      }),
    });
    await refreshApp();
  } catch (err) {
    alert(err.message);
  }
});

async function setDesired(desired) {
  try {
    await api("/api/bot/desired", { method: "POST", body: JSON.stringify({ desired }) });
    await refreshApp();
  } catch (err) {
    alert(err.message);
  }
}

$("dryBtn").addEventListener("click", () => setDesired("dry_run"));
$("liveBtn").addEventListener("click", () => setDesired("live"));
$("stopBtn").addEventListener("click", () => setDesired("stopped"));
$("pauseBtn").addEventListener("click", () => setDesired("paused"));
$("panicBtn").addEventListener("click", () => {
  if (confirm("Panic 會對該帳戶嘗試平倉（實單時為真單）。確定？")) setDesired("panic");
});

(async function boot() {
  await loadPlans();
  try {
    await api("/api/auth/me");
    show("gate", false);
    show("app", true);
    $("logoutBtn").classList.remove("hidden");
    await refreshApp();
  } catch {
    show("gate", true);
    show("app", false);
  }
})();
