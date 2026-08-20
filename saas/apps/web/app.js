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

async function loadPlans() {
  const { plans } = await api("/api/plans");
  $("plansMount").innerHTML = plans
    .map(
      (plan) => `<article class="plan">
        <b>${plan.name}</b>
        <div>${money(plan)}</div>
        <p>${plan.blurb_zh}</p>
        <small>${plan.disclaimer_zh}</small>
      </article>`
    )
    .join("");
  $("subscribeRow").innerHTML = plans
    .map((plan) => `<button type="button" data-plan="${plan.id}">訂閱 ${plan.name}</button>`)
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
  $("market").innerHTML = dash.market
    ? `<div><span>BTC</span><span>${dash.market.btc_usd ?? "—"}</span></div>
       <div><span>ETH</span><span>${dash.market.eth_usd ?? "—"}</span></div>`
    : `<div><span>行情 daemon</span><span>尚未寫入 snapshot</span></div>`;
  $("botMeta").innerHTML = `
    <div><span>desired</span><span>${bot.desired}</span></div>
    <div><span>tier</span><span>${bot.risk_tier}</span></div>
    <div><span>coins</span><span>${(bot.coins || []).join(", ")}</span></div>
    <div><span>live 解鎖</span><span>${bot.live_unlocked ? "是" : "否"}</span></div>
    <div><span>金鑰</span><span>${bot.client_id ? bot.client_id + " · ***" + (bot.secret_last4 || "") : "未設定"}</span></div>`;
  const groups = dash.bot.open_groups || [];
  $("groups").innerHTML = groups.length
    ? groups
        .map(
          (g) => `<div class="card">
            <strong>${g.short_instrument}</strong>
            <span>${g.currency} · qty ${g.quantity} · DTE ${g.dte_days}</span>
            <span>premium ${g.entry_credit}</span>
          </div>`
        )
        .join("")
    : `<p class="hint">目前沒有開放中的 covered call。</p>`;
  $("keyStatus").textContent = bot.has_credentials ? "已保存（密鑰不回顯）" : "尚未綁定";
  $("riskTier").value = bot.risk_tier || "low";
  document.querySelectorAll("input[name=coin]").forEach((el) => {
    el.checked = (bot.coins || []).includes(el.value);
  });
  $("sweep").checked = !!bot.profit_sweep;
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
