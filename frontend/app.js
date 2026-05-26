(()=>{var to=typeof window<"u"&&window.__DASHBOARD_MODE__==="investor"?"investor":"ops",_=to==="investor",eo=(()=>{if(!_)return"en";let t=String(typeof window<"u"&&window.__INVESTOR_LOCALE__||"en").trim().toLowerCase();return t==="zh-hant"||t==="zh_tw"||t==="zh-tw"||t==="zh-hk"||t==="zh"?"zh":"en"})(),L=_&&eo==="zh";function u(t,e){return _&&L?e:t}function no(){try{return document.querySelector('meta[name="dashboard-api-base"]')?.getAttribute("content")?.trim()||""}catch{return""}}function Fe(t){if(/^https?:\/\//i.test(t))return t;let n=((typeof window<"u"&&window.__API_BASE__?String(window.__API_BASE__).trim():"")||no()).replace(/\/$/,""),o=t.startsWith("/")?t:`/${t}`;return n?`${n}${o}`:o}var H={usd0:new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:0}),usd2:new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:2}),num4:new Intl.NumberFormat("en-US",{maximumFractionDigits:4}),num8:new Intl.NumberFormat("en-US",{maximumFractionDigits:8}),pct2:new Intl.NumberFormat("en-US",{style:"percent",maximumFractionDigits:2,minimumFractionDigits:2}),pct1:new Intl.NumberFormat("en-US",{style:"percent",maximumFractionDigits:1,minimumFractionDigits:1})},st={BTC:"#fb923c",ETH:"#818cf8",USDC:"#38bdf8",TOTAL:"#a3e635"},Y=["BTC","ETH","USDC"],Dt=18e4,ze=_?6:3,qe=!0,fe=45e3,Ve=3e3,Ge=new Set([502,503,504]),We=2,me=450,dt=10,q=[{id:"covered_call",title:"Covered Call",titleZh:"\u5099\u514C\u8CB7\u6B0A",short:"Covered Call",shortZh:"\u5099\u514C",chipShort:"CC",chipShortZh:"\u5099\u514C",accentClass:"strategy-card-call",description:"Short call backed by existing BTC/ETH spot collateral.",descriptionZh:"\u5728\u6301\u6709\u73FE\u8CA8\u64D4\u4FDD\u4E0B\u8CE3\u51FA\u8CB7\u6B0A\uFF0C\u4EE5\u6B0A\u5229\u91D1\u589E\u5F37\u6536\u76CA\u3002"},{id:"naked_short",title:"Naked Short",titleZh:"\u55AE\u8CE3\u9078\u64C7\u6B0A\uFF08\u88F8\u8CE3\uFF09",short:"Naked Short",shortZh:"\u88F8\u8CE3",chipShort:"Naked",chipShortZh:"\u88F8\u8CE3",accentClass:"strategy-card-put",description:"Single-leg short option (put / call / both) with uncapped tail risk on the chosen side.",descriptionZh:"\u55AE\u908A\u8CE3\u51FA\u8CB7\uFF0F\u8CE3\u6B0A\uFF1B\u5728\u5C0D\u61C9\u65B9\u5411\u5177\u5C3E\u90E8\u98A8\u96AA\uFF0C\u9700\u56B4\u683C\u98A8\u63A7\u3002"},{id:"bull_put_spread",title:"Bull Put Spread",titleZh:"\u725B\u52E2\u8CE3\u6B0A\u50F9\u5DEE",short:"Put Spread",shortZh:"\u8CE3\u6B0A\u50F9\u5DEE",chipShort:"Spread",chipShortZh:"\u50F9\u5DEE",accentClass:"strategy-card-spread",description:"Short put paired with a lower-strike long put protection leg.",descriptionZh:"\u8CE3\u51FA\u8F03\u9AD8\u5C65\u7D04\u50F9\u8CE3\u6B0A\uFF0C\u4E26\u8CB7\u5165\u8F03\u4F4E\u5C65\u7D04\u50F9\u8CE3\u6B0A\u4F5C\u4FDD\u8B77\u3002"}],V=Object.fromEntries(q.map(t=>[t.id,t]));var l={health:null,status:null,report:null,stress:null,groups:null,cumulativePnl:null,aprSeries:null,portfolioSnapshot:null,dataFreshness:{source:null,snapshotMs:null,statusMs:null,live:!1},chartsDataLoaded:!1,chartsLoadInFlight:!1,bookFilter:"ALL",aprWindow:30,charts:{},autoRefreshHandle:null,refreshInFlight:!1,investorReady:!1,investorLoadTotal:0,investorLoadDone:0,lastRefreshStartedMs:0,statusErrorOnce:!1,lastUnderlyingIndexUsd:{},lastSpotUsd:{BTC:null,ETH:null},activityOpenPage:1,activityClosedPage:1};function a(t){if(t==null||t==="")return null;let e=typeof t=="number"?t:Number(t);return Number.isFinite(e)?e:null}function g(t,e=2){let n=a(t);return n===null?"\u2014":e===0?H.usd0.format(n):H.usd2.format(n)}function k(t,e=2){let n=a(t);return n===null?"\u2014":e===1?H.pct1.format(n):H.pct2.format(n)}function mt(){if(l.status?.portfolio)return{portfolio:l.status.portfolio,source:"live",freshnessMs:l.dataFreshness.statusMs??0};let t=l.portfolioSnapshot?.portfolio;return t&&Object.keys(t).length>0?{portfolio:t,source:"snapshot",freshnessMs:a(l.portfolioSnapshot?.freshness_ms)}:{portfolio:null,source:null,freshnessMs:null}}function oo(t){let e=a(t);return e===null||e<0?null:Math.max(1,Math.round(e/6e4))}function ro(){let t=mt();if(t.source==="live"){let e=a(l.dataFreshness.statusMs);if(e!==null&&e<3e4)return`<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-emerald-500/40 bg-emerald-500/10 text-emerald-200">${u("Live","\u5373\u6642")}</span>`}if(t.source==="snapshot"){let e=oo(t.freshnessMs);return`<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-200">${e!==null?u(`Snapshot \xB7 ~${e}m ago`,`\u5FEB\u7167 \xB7 \u7D04 ${e} \u5206\u9418\u524D`):u("Snapshot","\u5FEB\u7167")}</span>`}return`<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-800/60 text-slate-400">${u("Loading\u2026","\u8F09\u5165\u4E2D\u2026")}</span>`}function he(){if(!_)return;let t=document.getElementById("data-freshness-slot");t&&(t.innerHTML=ro())}function vt(t,{indeterminate:e=!1}={}){let n=document.getElementById("investor-progress-bar");n&&(n.classList.toggle("hidden",!t),n.classList.toggle("investor-progress-bar--indeterminate",t&&e))}function Xe(){let e=`<div class="overview-metrics-grid">${'<div class="skeleton-block h-16 rounded-lg"></div>'.repeat(8)}</div>`;return _?`<div class="investor-view-desktop">${e}</div><div class="investor-view-mobile"><div class="inv-dashboard">
      <div class="inv-panel skeleton-block" style="height:5.5rem"></div>
      <div class="inv-panel skeleton-block" style="height:4rem"></div>
      <div class="inv-panel skeleton-block" style="height:7rem"></div>
    </div></div>`:e}function Qe(t){let{totalEquity:e,dayStart:n,dayPnl:o,dayDrawdown:r,openCredit:s,creditByStrategy:i,summary:c,winRate:d,avgHolding:p,sinceLine:f,lifetimePnl:m,lifetimeNativeByBook:v,closedCount:y,windowLabelDays:h,windowPnl:x,windowNativeByBook:w,lifetimeApr:T,windowApr:R,equityNativeByBook:B}=t;return`
    <div class="overview-metrics-grid">
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Total equity","\u7E3D\u6B0A\u76CA\uFF08USDC \u7D04\u7576\uFF09")}</div>
        <div class="text-2xl font-mono">${g(e)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${fmtBookEquityNativeBreakdown(B)}</div>
          <div class="overview-metric-line">${u("day-start","\u65E5\u521D")} ${g(n)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Day P&L","\u672C\u65E5\u640D\u76CA")}</div>
        <div class="text-2xl font-mono ${E(o)}">${g(o)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${u("drawdown","\u56DE\u64A4")} ${k(r)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Open credit","\u672A\u5BE6\u73FE\u6B0A\u5229\u91D1\uFF08\u9032\u5834\u6536\u6582\uFF09")}</div>
        <div class="text-2xl font-mono">${g(s)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${fmtOpenCreditStrategyBreakdown(i)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Win rate \xB7 avg holding","\u52DD\u7387 \xB7 \u5E73\u5747\u6301\u6709")}</div>
        <div class="text-2xl font-mono">${c?`${k(d,1)} \xB7 ${S(p,2)}${L?" \u5929":"d"}`:"\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${c?f:u("Loading performance\u2026","\u7E3E\u6548\u6458\u8981\u8F09\u5165\u4E2D\u2026")}</div>
        </div>
      </div>

      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Total profit (lifetime)","\u7D2F\u8A08\u5DF2\u5BE6\u73FE\u640D\u76CA")}</div>
        <div class="text-2xl font-mono ${E(m)}">${c?g(m):"\u2014"}</div>
        <div class="overview-metric-meta">
          ${c?`<div class="overview-metric-line">${fmtLifetimeRealizedNativeBreakdown(v)}</div>`:""}
          <div class="overview-metric-line">${c?`${y??0} ${u("closed groups","\u7B46\u5DF2\u5E73\u5009\u90E8\u4F4D")}`:""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${go(h)}</div>
        <div class="text-2xl font-mono ${E(x)}">${c?g(x):"\u2014"}</div>
        <div class="overview-metric-meta">
          ${c?`<div class="overview-metric-line">${fmtLifetimeRealizedNativeBreakdown(w)}</div>`:""}
          <div class="overview-metric-line">${c?ln(h):""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Realized APR (lifetime)","\u5DF2\u5BE6\u73FE\u5E74\u5316\uFF08\u5B58\u7E8C\u671F\uFF09")}</div>
        <div class="text-2xl font-mono">${c?k(T):"\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${c?u("annualized on actual span","\u4F9D\u5BE6\u969B\u5340\u9593\u5E74\u5316"):""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${bo(h)}</div>
        <div class="text-2xl font-mono">${c?k(R):"\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line overview-metric-line--hint">${c?cn(h):""}</div>
        </div>
      </div>
    </div>`}function ve(t,{pnl:e=!1,places:n={BTC:5,ETH:4,USDC:2}}={}){let o={BTC:"\u20BF",ETH:"\u25C6",USDC:"$"};return["BTC","ETH","USDC"].map(r=>{let s=a(t[r]),i=s===null?"\u2014":S(s,n[r]??4);return`<span class="inv-chip ${e?E(t[r]):""}"><span class="inv-chip-sym">${o[r]}</span><span class="inv-chip-val font-mono tabular-nums">${i}</span></span>`}).join("")}function so(t){return xt(new Set(q.map(e=>e.id))).map(e=>{let n=escapeHtml(rt(e).short),o=a(t[e]),r=o===null?"\u2014":g(o);return`<div class="inv-mini-row"><span class="inv-mini-label">${n}</span><span class="inv-mini-value font-mono tabular-nums">${r}</span></div>`}).join("")}function tn(t){let{totalEquity:e,dayStart:n,dayPnl:o,dayDrawdown:r,openCredit:s,creditByStrategy:i,summary:c,winRate:d,avgHolding:p,sinceLine:f,lifetimePnl:m,lifetimeNativeByBook:v,closedCount:y,windowLabelDays:h,windowPnl:x,windowNativeByBook:w,lifetimeApr:T,windowApr:R,equityNativeByBook:B}=t,b=c!=null?`${k(d,1)} \xB7 ${S(p,2)}${L?" \u5929":"d"}`:"\u2014",$=c?f:u("Loading performance\u2026","\u7E3E\u6548\u6458\u8981\u8F09\u5165\u4E2D\u2026");return`<div class="inv-dashboard">
    <section class="inv-panel inv-panel--hero" aria-label="${u("Account snapshot","\u5E33\u6236\u5FEB\u7167")}">
      <div class="inv-split">
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Total equity","\u7E3D\u6B0A\u76CA")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${g(e)}</span>
          <span class="inv-kpi-foot">${u("day-start","\u65E5\u521D")} ${g(n)}</span>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Day P&L","\u672C\u65E5\u640D\u76CA")}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${E(o)}">${g(o)}</span>
          <span class="inv-kpi-foot">${u("drawdown","\u56DE\u64A4")} ${k(r)}</span>
        </div>
      </div>
      <div class="inv-chips-row">${ve(B)}</div>
    </section>

    <section class="inv-panel" aria-label="${u("Open risk","\u672A\u5E73\u5009\u98A8\u96AA")}">
      <div class="inv-split">
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Open credit","\u672A\u5BE6\u73FE\u6B0A\u5229\u91D1")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${g(s)}</span>
          <div class="inv-mini-list">${so(i)}</div>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Win rate \xB7 hold","\u52DD\u7387 \xB7 \u6301\u6709")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${b}</span>
          <span class="inv-kpi-foot">${$}</span>
        </div>
      </div>
    </section>

    <section class="inv-panel" aria-label="${u("Realized performance","\u5DF2\u5BE6\u73FE\u7E3E\u6548")}">
      <h3 class="inv-panel-title">${u("Realized P&L","\u5DF2\u5BE6\u73FE\u640D\u76CA")}</h3>
      <div class="inv-compare">
        <div class="inv-compare-col">
          <span class="inv-compare-tag">${u("Lifetime","\u5B58\u7E8C")}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${E(m)}">${c?g(m):"\u2014"}</span>
          <div class="inv-chips-row inv-chips-row--compact">${c?ve(v,{pnl:!0}):""}</div>
          <span class="inv-kpi-foot">${c?`${y??0} ${u("closed","\u7B46\u5E73\u5009")}`:""}</span>
        </div>
        <div class="inv-compare-col">
          <span class="inv-compare-tag">${u("Last","\u8FD1")} ${h}${L?" \u65E5":"d"}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${E(x)}">${c?g(x):"\u2014"}</span>
          <div class="inv-chips-row inv-chips-row--compact">${c?ve(w,{pnl:!0}):""}</div>
          <span class="inv-kpi-foot">${c?ln(h):""}</span>
        </div>
      </div>
      <div class="inv-split inv-split--apr">
        <div class="inv-kpi inv-kpi--compact">
          <span class="inv-kpi-label">${u("APR lifetime","\u5E74\u5316\xB7\u5B58\u7E8C")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${c?k(T):"\u2014"}</span>
        </div>
        <div class="inv-kpi inv-kpi--compact">
          <span class="inv-kpi-label">${u("APR","\u5E74\u5316")} ${h}${L?" \u65E5":"d"}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${c?k(R):"\u2014"}</span>
          <span class="inv-kpi-foot">${c?cn(h):""}</span>
        </div>
      </div>
    </section>
  </div>`}function lt(t,e){let n=a(t);if(n===null)return"\u2014";let o=String(e||"").toUpperCase(),r='<span class="text-slate-500">',s="</span>";return o==="USDC"?`${r}($)${s}\xA0${S(n,4)}`:o==="BTC"?`${r}\u20BF${s}\xA0${S(n,5)}`:o==="ETH"?`${r}\u2666${s}\xA0${S(n,5)}`:S(n,4)}function _e(t){if(!t)return null;let e=String(t.kind||"").toLowerCase(),n=String(t.direction||"").toLowerCase()==="sell";if(e==="option"){let s=a(t.size);return s===null||s===0?null:n?-Math.abs(s):Math.abs(s)}let o=a(t.size_currency);if(o!==null&&o!==0)return n&&o>0?-Math.abs(o):o;let r=a(t.size);return r===null||r===0?null:n&&r>0?-Math.abs(r):r}function io(t,e,n=null){let o=n??l.groups;if(yt(e,o,t,"short")>1){let d=It(t,"short");if(d!==null)return S(d,4)}let r=X(e,t),s=_e(r);if(s!==null)return S(s,4);let i=a(t.quantity);if(i===null)return"\u2014";let c=i>0?-Math.abs(i):i;return S(c,4)}function j(t,e){return String(e==="long"?t?.long_instrument_name||"":t?.short_instrument_name||"")}function It(t,e){let n=a(t.quantity);return n===null?null:e==="short"?-Math.abs(n):Math.abs(n)}function yt(t,e,n,o){let r=j(n,o);if(!r)return 0;let s=String(n?.account_name||""),i=new Set,c=0;for(let d of[t?.trade_groups||[],e?.open||[]])for(let p of d){if(!Nt(p))continue;let f=pt(p);i.has(f)||(i.add(f),j(p,o)===r&&(s&&String(p?.account_name||"")!==s||c++))}return c}function xe(t,e,n){let o=j(e,n);if(!o)return null;let r=t?.positions||[],s=String(e?.account_name||"");if(s){let i=r.find(c=>c.instrument_name===o&&String(c.account_name||"")===s);if(i)return i}return r.find(i=>i.instrument_name===o)||null}function X(t,e){return xe(t,e,"short")}function ao(t,e,n=null){let o=n??l.groups,r=e.short_average_price,s=e.short_mark_price,i=e.short_floating_profit_loss,c=e.short_has_floating_profit_loss,d=e.short_floating_profit_loss_usd,p=e.short_has_floating_profit_loss_usd,f=r==null||r==="",m=s==null||s==="",v=i==null||i==="",y=d==null||d==="",h=yt(t,o,e,"short")>1;if((f||m||v||y||c===void 0||p===void 0)&&t?.positions?.length){let x=X(t,e);x&&(f&&(r=x.average_price),m&&(s=x.mark_price),h||(v&&(i=x.floating_profit_loss),c===void 0&&(c=x.has_floating_profit_loss),y&&(d=x.floating_profit_loss_usd),p===void 0&&(p=x.has_floating_profit_loss_usd)))}return{...e,short_average_price:r,short_mark_price:s,short_floating_profit_loss:i,short_has_floating_profit_loss:c,short_floating_profit_loss_usd:d,short_has_floating_profit_loss_usd:p}}function en(t){let e=t.expiration_timestamp_ms;if(e!=null&&e!==""){if(typeof e=="number"&&Number.isFinite(e))return Math.round(e);if(typeof e=="bigint")return Number(e);let n=String(e).trim();if(/^\d+$/.test(n)){let o=Number(n);return Number.isFinite(o)?o:null}}if(t.expiry){let n=luxon.DateTime.fromISO(String(t.expiry),{zone:"utc"});if(n.isValid)return n.toMillis()}return null}function ge(t){let e=a(t.dte_days)??a(t.dte);if(e!==null)return e;let n=en(t);if(n===null)return null;let o=luxon.DateTime.fromMillis(n,{zone:"utc"});return o.isValid?o.diff(luxon.DateTime.utc(),"days").days:null}function Q(t){let e=String(t.option_type||"").toLowerCase();if(e==="call")return"Call";if(e==="put")return"Put";let n=String(t.short_instrument_name||"");return/-C$/i.test(n)||n.endsWith("-C")?"Call":"Put"}function nn(t,e){for(let n of["BTC","ETH"]){let o=a(t?.underlying_index_usd?.[n]),r=a(e?.underlying_index_usd?.[n]),s=o>0?o:r>0?r:null;s!==null&&(l.lastUnderlyingIndexUsd[n]=s)}}function lo(t,e){let n={};for(let o of["BTC","ETH"]){let r=a(t?.underlying_index_usd?.[o]),s=a(e?.underlying_index_usd?.[o]),i=a(l.lastUnderlyingIndexUsd[o]),c=r>0?r:s>0?s:i>0?i:null;c!==null&&(n[o]=c)}return n}function co(t,e,n){let o=String(n||"").toUpperCase(),r=lo(t,e);return a(r[o])}function F(t){let e=String(t.collateral_currency||"").toUpperCase();if(e==="BTC"||e==="ETH"||e==="USDC")return e;let n=String(t.short_instrument_name||"");return n.includes("_USDC-")?"USDC":n.startsWith("BTC-")?"BTC":n.startsWith("ETH-")?"ETH":String(t.currency||"").toUpperCase()||"BTC"}function on(t){let e=F(t);return e==="BTC"||e==="ETH"?e:String(t.currency||"BTC").toUpperCase()}function uo(t,e){let n=F(e);if(n!=="BTC"&&n!=="ETH")return null;let o=n==="BTC"?"BTC-":"ETH-",r=t?.positions;if(!r?.length)return null;let s=String(e?.account_name||"");for(let i of r){if(s&&String(i.account_name||"")!==s)continue;let c=String(i.instrument_name||""),d=String(i.kind||"").toLowerCase();if(!c.startsWith(o)||d!=="option"&&d!=="future")continue;let p=a(i.index_price);if(p!==null&&p>0)return p}return null}function po(t,e,n){let o=on(t),r=a(l.lastSpotUsd[o]);if(r!==null&&r>0)return r;let s=co(e,n,o);if(s!==null&&s>0)return s;let i=X(e,t),c=a(i?.index_price);if(c!==null&&c>0)return c;let d=uo(e,t);return d!==null&&d>0?d:null}function Pt(t,e,n){let o=F(t);if(o==="USDC")return 1;if(o==="BTC"||o==="ETH"){let r=po(t,e,n);return r!==null&&r>0?r:null}return null}function rn(t){return _e(t)}function tt(t,e,n,o=null){let r=o??l.groups,s=It(t,n);if(s!==null&&yt(e,r,t,n)>1)return s;let i=xe(e,t,n),c=_e(i);return c!==null?c:s}function et(t,e,n,o){if(n==="short"&&$e(t,`short_${o}`)){let s=t[`short_${o}`];if(s!=null&&s!=="")return s}return xe(e,t,n)?.[o]??null}function fo(t,e,n,o=null){let r=a(et(e,t,n,"average_price")),s=a(et(e,t,n,"mark_price")),i=tt(e,t,n,o);return r===null||s===null||i===null?null:(s-r)*i}function it(t,e,n,o){let r=fo(t,e,o,n);if(r===null)return null;let s=Pt(e,t,n);return s===null||s<=0?null:r*s}function mo(t,e,n=null){let o=n??l.groups;if(yt(t,o,e,"short")>1){let d=X(t,e);if(!d)return null;let p=a(d.average_price),f=a(d.mark_price),m=It(e,"short");return p===null||f===null||m===null?null:(f-p)*m}let r=X(t,e);if(!r)return null;let s=a(r.average_price),i=a(r.mark_price),c=rn(r);return s===null||i===null||c===null?null:(i-s)*c}function vo(t,e){let n=mo(e,t);if(n!==null)return n;if(t.short_has_floating_profit_loss){let o=a(t.short_floating_profit_loss);if(o!==null)return o}return null}function yo(t,e,n){let o=n??l.groups;if(yt(t,o,e,"short")>1){let p=X(t,e);if(!p)return null;let f=a(p.average_price),m=a(p.mark_price),v=It(e,"short");if(f===null||m===null||v===null)return null;let y=Pt(e,t,n);return y===null||y<=0?null:(m-f)*v*y}let r=X(t,e);if(!r)return null;let s=a(r.average_price),i=a(r.mark_price),c=rn(r);if(s===null||i===null||c===null)return null;let d=Pt(e,t,n);return d===null||d<=0?null:(i-s)*c*d}function Ye(t,e,n){let o=yo(e,t,n);if(o!==null)return o;if(t.short_has_floating_profit_loss_usd){let r=a(t.short_floating_profit_loss_usd);if(r!==null)return r}return null}function ho(t){let e=a(t);return e===null?"\u2014":`<span class="text-slate-500">($)</span>\xA0${new Intl.NumberFormat("en-US",{maximumFractionDigits:2,minimumFractionDigits:2}).format(e)}`}function je(t){let e=a(t.unrealized_usdc_estimate);if(e!==null)return e;let n=a(t.entry_credit),o=a(t.current_debit);return n!==null&&o!==null?n-o:null}function _o(t,e,n){let o=it(t,e,n,"short"),r=it(t,e,n,"long");return o===null&&r===null?null:(o||0)+(r||0)}function xo(t,e,n){let o=it(t,e,n,"short"),r=it(t,e,n,"long");return o===null||r===null?null:o+r}function ht(t,e,n){return U(t)==="bull_put_spread"?xo(e,t,n)??je(t)??_o(e,t,n)??Ye(t,e,n):Ye(t,e,n)??je(t)}function ot(t,e,n){return a(t.entry_credit)}function Ot(t,e){let n=String(e||"").toUpperCase();return n==="USDC"?t===null?"\u2014":ho(t):t===null?"\u2014":n==="BTC"?`<span class="text-slate-500">\u20BF</span>\xA0${S(t,8)}`:n==="ETH"?`<span class="text-slate-500">\u2666</span>\xA0${S(t,8)}`:S(t,8)}function be(t,e,n){if(U(t)!=="bull_put_spread")return vo(t,e);let o=a(t.unrealized_coin_native);if(o!==null)return o;let r=ht(t,e,n),s=Pt(t,e,n);return r===null||s===null||s<=0?null:r/s}function S(t,e=4){let n=a(t);return n===null?"\u2014":(e>=8?H.num8:H.num4).format(n)}function $e(t,e){return Object.prototype.hasOwnProperty.call(t||{},e)}function Mt(t,e){let n=String(t||"").toUpperCase(),o=e?.portfolio||{},r=a(o?.equity_by_book?.[n]);if(r!==null)return r;let s=a(e?.accounts?.[n]?.equity);if(s===null)return null;if(n==="USDC")return s;let i=a(e?.underlying_index_usd?.[n])??a(l.lastSpotUsd?.[n]);return i===null||i<=0?null:s*i}function Se(t,e,n){let o=a(t?.day_net_flow_usdc);return a(t?.day_pnl_usdc_ex_flow)??a(t?.day_pnl_usdc_ex_flow_ex_spot)??(e!==null&&n!==null?e-n-(o??0):null)}function sn(t,e,n,o){let r=String(t||"").toUpperCase(),s=e?.portfolio||{},i=a(s?.day_net_flow_usdc_by_book?.[r]);return a(s?.day_pnl_usdc_ex_flow_by_book?.[r])??a(s?.day_pnl_usdc_ex_flow_ex_spot_by_book?.[r])??(n!==null&&o!==null?n-o-(i??0):null)}function E(t){let e=a(t);return e===null||e===0?"":e>0?"pnl-pos":"pnl-neg"}function Ze(t){if(t==null)return"\u2014";let e;return typeof t=="number"?e=luxon.DateTime.fromMillis(t,{zone:"utc"}):e=luxon.DateTime.fromISO(String(t),{zone:"utc"}),e.isValid?e.toLocal().toFormat("yyyy-LL-dd HH:mm"):"\u2014"}function an(t){if(t==null)return"\u2014";let e;return typeof t=="number"?e=luxon.DateTime.fromMillis(t,{zone:"utc"}):e=luxon.DateTime.fromISO(String(t),{zone:"utc"}),e.isValid?e.toLocal().toFormat("yyyy-LL-dd"):"\u2014"}function go(t){let e=Math.round(t??30);return u(`Total profit (rolling ${e}d)`,`\u5DF2\u5BE6\u73FE\u640D\u76CA\uFF08\u6EFE\u52D5 ${e} \u65E5\u8996\u7A97\uFF09`)}function bo(t){let e=Math.round(t??30);return u(`Realized APR (rolling ${e}d)`,`\u5DF2\u5BE6\u73FE\u5E74\u5316\uFF08\u6EFE\u52D5 ${e} \u65E5\u8996\u7A97\uFF09`)}function ln(t){let e=Math.round(t??30);return u(`Closes in last ${e}d only`,`\u50C5\u8A08\u6700\u8FD1 ${e} \u65E5\u5167\u5E73\u5009`)}function cn(t){let e=Math.round(t??30);return u(`Last ${e}d closes \xF7 ledger total equity`,`\u8FD1 ${e} \u65E5\u5E73\u5009 \xF7 \u7576\u65E5\u7E3D\u6B0A\u76CA`)}function un(t=30){let e=`/api/realized_summary?days=${t}`,n=aprEffectiveCapitalUsdc();return n!==null&&(e+=`&effective_capital_usdc=${encodeURIComponent(String(n))}`),e}function Ce(t=30){let e=`/api/dashboard_bundle?days=${t}`,n=aprEffectiveCapitalUsdc();return n!==null&&(e+=`&effective_capital_usdc=${encodeURIComponent(String(n))}`),e}function we(t){t?.groups&&(l.groups=t.groups),t?.status&&(l.status=t.status,l.statusErrorOnce=!1,l.dataFreshness.source="live",l.dataFreshness.live=!0,l.dataFreshness.statusMs=0),t?.realized_summary&&(l.report=t.realized_summary)}function dn(t,e){let n=null,o=r=>{if(!r||a(r.realized_pnl)===null||!qt(r,l.status,e))return;let s=nt(r);s===null||s<=0||(n===null||s<n)&&(n=s)};for(let r of e?.closed||[])o(r);for(let r of t?.recent_closed_trades||[])o(r);return n}function Je(t){if(!t||Q(t).toLowerCase()!=="call")return!1;let e=a(t.covered_underlying_quantity);return e!==null&&e>0||String(t.short_label||"").startsWith("covered_call-")||String(t.account_name||"")==="covered_call"?!0:String(t.account_env_file||"").includes(".env.covered_call")}function J(t){let e=String(t||"").trim().toLowerCase().replaceAll("-","_").replaceAll(" ","_");return e?{naked:"naked_short",naked_put:"naked_short",naked_call:"naked_short",short_put:"naked_short",short_call:"naked_short",shortput:"naked_short",shortcall:"naked_short",naked_short_put:"naked_short",naked_short_call:"naked_short",put_spread:"bull_put_spread",short_put_spread:"bull_put_spread",bullputspread:"bull_put_spread",bull_put:"bull_put_spread",coveredcall:"covered_call"}[e]||e:""}function U(t){let e=J(t?.strategy),n=String(t?.long_instrument_name||"").trim();return(e===""||e==="naked_short")&&n&&Q(t).toLowerCase()==="put"?"bull_put_spread":e==="naked_short"&&Je(t)?"covered_call":e||(Q(t).toLowerCase()==="call"&&Je(t)?"covered_call":"naked_short")}function rt(t){let e=J(t);if(V[e]){let o=V[e];return!_||!L?o:{...o,title:o.titleZh||o.title,short:o.shortZh||o.short,chipShort:o.chipShortZh||o.chipShort||o.shortZh||o.short,description:o.descriptionZh||o.description}}let n=e?e.replaceAll("_"," "):"\u2014";return{id:e||"",title:n,short:n,chipShort:n,accentClass:"border-slate-700",description:""}}function Ht(t){return rt(t).title}function $o(t){let e=J(t);return e==="naked_short"?"chip-strategy-naked":e==="bull_put_spread"?"chip-strategy-spread":e==="covered_call"?"chip-strategy-covered":"chip-strategy-unknown"}function K(t,{compact:e=!1}={}){let n=rt(t),o=$o(n.id||t),r=e&&n.chipShort||n.short;return`<span class="chip ${o}${e?" chip--compact":""}">${escapeHtml(r)}</span>`}function pt(t){return[String(t?.account_name||""),String(t?.group_id||""),String(t?.short_instrument_name||"")].join("\0")}var So=["realized_pnl_collateral_native","short_entry_average_price","short_close_average_price","entry_index_usd","close_index_usd","realized_close_debit","realized_close_fee","entry_fee","entry_credit","collateral_currency","strategy","option_type","covered_underlying_quantity","realized_apr_on_equity","close_book_equity","quantity","realized_pnl","contract_size","short_strike"];function Ke(t){return!(t==null||t===""||typeof t=="number"&&!Number.isFinite(t))}function Co(t,e){let n={...e,...t};for(let o of So)Ke(t[o])?n[o]=t[o]:Ke(e[o])&&(n[o]=e[o]);return n}function Ft(t){let e=new Map;for(let n of t||[]){let o=pt(n),r=e.get(o);e.set(o,r?Co(r,n):n)}return[...e.values()]}function Nt(t){return String(t?.status||"open").toLowerCase()!=="closed"}function zt(t){return String(t?.status||"").toLowerCase()==="closed"?!0:closedTimestampMs(t)!==null}var wo=3e5;function ko(t,e){let n=new Set;for(let o of _t(t,e)){let r=String(o?.short_instrument_name||"").trim();r&&n.add(r)}return n}function Eo(t,e,n){if(!zt(t)||String(t?.close_reason||"").toLowerCase()!=="reconciled_external")return!1;let o=nt(t),r=closedTimestampMs(t);if(o===null||r===null||r<=o||r-o>wo)return!1;let s=String(t?.short_instrument_name||"").trim();return s?ko(e,n).has(s):!1}function qt(t,e,n){return zt(t)&&!Eo(t,e,n)}function _t(t,e){let n=[],o=new Set;for(let r of t?.trade_groups||[]){if(!Nt(r))continue;let s=pt(r);o.has(s)||(o.add(s),n.push(r))}for(let r of e?.open||[]){if(!Nt(r))continue;let s=pt(r);o.has(s)||(o.add(s),n.push(r))}return n.map(r=>ao(t,r,e))}function pn(t,e,n=20,o=null){let r=o??l.status,s=Ft([...e?.closed||[],...t?.recent_closed_trades||[]]).filter(i=>qt(i,r,e));return s.sort((i,c)=>(closedTimestampMs(c)||0)-(closedTimestampMs(i)||0)),s.slice(0,n)}function ke(t,e){return pn(t,e,500)}function xt(t){let e=q.map(r=>r.id),n=e.filter(r=>t.has(r)),o=[...t].filter(r=>!e.includes(r)).sort();return n.concat(o)}function To(t){let e=String(t||"").match(/-([0-9]+(?:\.[0-9]+)?)-[CP]$/i);return e?a(e[1]):null}function Z(t,e){let n=a(e==="long"?t?.long_strike:t?.short_strike);return n!==null?n:To(j(t,e))}function ct(t){let e=a(t);return e===null?"\u2014":g(e,0)}function Ee(t){let e=Z(t,"short"),n=Z(t,"long");return e===null||n===null?null:e-n}function Vt(t,e,n){let o=a(et(t,e,"short",n)),r=a(et(t,e,"long",n));return o===null||r===null?null:o-r}function Te(t){let e=String(t?.long_instrument_name||"").trim();if(e)return u(`Long ${e}`,`\u8CB7\u817F ${e}`);let n=a(t?.covered_underlying_quantity);return n!==null&&n>0?u(`Covered ${S(n,4)} ${String(t.currency||"").toUpperCase()}`,`\u5099\u514C ${S(n,4)} ${String(t.currency||"").toUpperCase()}`):u("Single short leg","\u55AE\u908A\u8CE3\u51FA")}function ft(t){let e=String(t?.account_name||"").trim();return e?`Account ${e}`:""}function gt(t){let e=a(t?.holding_days);if(e!==null)return e;let n=closedTimestampMs(t),o=nt(t);return n===null||o===null||o<=0?null:Math.max(n-o,0)/864e5}function nt(t){let e=a(t?.entry_timestamp_ms);if(e!==null)return e;if(t?.entry_timestamp){let n=luxon.DateTime.fromISO(String(t.entry_timestamp),{zone:"utc"});if(n.isValid)return n.toMillis()}return null}function Ro(t){let e=nt(t),n=en(t);return e===null||n===null||n<=e?null:(n-e)/864e5}function D(t){return F(t)}function bt(t,e){let n=D(t);return n==="USDC"?null:a(e?.underlying_index_usd?.[n])??a(l.groups?.underlying_index_usd?.[n])??a(l.lastSpotUsd?.[n])??a(t?.close_index_usd)}function Re(t,e){let n=a(t?.realized_pnl_collateral_native);if(n!==null)return n;if(D(t)==="USDC")return a(t?.realized_pnl);let r=a(t?.quantity);if(r===null||r<=0)return null;let s=a(t?.entry_index_usd),i=a(t?.close_index_usd)??s,c=a(t?.entry_fee)??0,d=a(t?.realized_close_fee)??0,p=null,f=null,m=a(t?.short_entry_average_price),v=a(t?.short_close_average_price),y=a(t?.entry_credit),h=a(t?.realized_close_debit);if(m!==null&&m>0?(p=m*r,(s===null||s<=0)&&y!==null&&(s=(y+c)/(m*r))):y!==null&&s!==null&&s>0&&(p=(y+c)/s),v!==null&&v>0?(f=v*r,(i===null||i<=0)&&h!==null&&(i=Math.max(0,h-d)/(v*r))):h!==null&&i!==null&&i>0&&(f=Math.max(0,h-d)/i),p===null||f===null)return null;let x=0;if(c>0){if(s===null||s<=0)return null;x+=c/s}if(d>0){if(i===null||i<=0)return null;x+=d/i}return p-f-x}function Bo(t){let e=D(t);return e==="BTC"||e==="ETH"}function Gt(t,e){if(D(t)==="USDC")return a(t?.realized_pnl);let o=Re(t,e),r=bt(t,e);return o!==null&&r!==null&&r>0?o*r:null}function $t(t,e){let n=D(t);if(n==="USDC")return a(t?.realized_pnl);let o=Re(t,e);if(o!==null)return o;let r=a(t?.realized_pnl);if(r===null)return null;let s=a(t?.close_index_usd)??a(e?.underlying_index_usd?.[n])??a(l.groups?.underlying_index_usd?.[n])??a(l.lastSpotUsd?.[n]);return s===null||s<=0?null:r/s}function Lo(t){let e=a(t?.contract_size);return e!==null&&e>0?e:1}function Ao(t,e){let n=a(t?.quantity);if(n===null||n<=0)return null;let o=Lo(t),r=U(t),s=a(t?.estimated_im_collateral);if(r==="bull_put_spread"&&s!==null&&s>0)return s/n;if(D(t)==="USDC"){if(Q(t).toLowerCase()==="call"){let d=Uo(t,e)??jt(t,e)??bt(t,e)??yn(t,e);if(d!==null&&d>0)return d}else{let d=Z(t,"short");if(d!==null&&d>0)return d}return null}return o}function fn(t,e){let n=Ao(t,e),o=a(t?.quantity);if(n===null||o===null||o<=0)return null;let r=U(t);if(r==="covered_call"){let i=a(t?.covered_underlying_quantity);return i!==null&&i>0?i:o}return D(t)==="USDC"||r==="bull_put_spread",n*o}function Wt(t,e){return fn(t,e)}function mn(t,e){let n=$t(t,e),o=gt(t),r=Wt(t,e);return n===null||r===null||r<=0||o===null||o<=0?null:n/r*(365/o)}function Do(t,e){let n=D(t);if(!Bo(t)){let c=a(t?.realized_pnl);return c===null?"\u2014":g(c)}let o=Re(t,e);if(o===null){let c=a(t?.realized_pnl);return c===null?"\u2014":g(c)}let r=Gt(t,e),i=`${S(o,n==="BTC"?5:4)} ${n}`;return L?`${g(r)}\uFF08${i}\uFF09`:`${g(r)} (${i})`}function vn(t,e){let n=a(t);return n===null?`\u2014 ${e||""}`.trim():`${new Intl.NumberFormat("en-US",{maximumFractionDigits:8}).format(n)} ${e}`}function Ut(t,e,n){let o=g(t);if(e===null||!n||n==="USDC")return o;let r=vn(e,n);return L?`${o}\uFF08${r}\uFF09`:`${o} (${r})`}function Yt(t,e,n){let o=g(t);if(e===null||!n||n==="USDC")return o;let r=escapeHtml(vn(e,n));return`<span class="open-position-value-stack"><span class="open-position-value-line">${o}</span><span class="open-position-value-sub">${r}</span></span>`}function Be(t,e){let n=a(t),o=a(e);return n===null||o===null||o<=0?null:n/o}function jt(t,e){let n=D(t);return n==="USDC"?null:a(t?.entry_index_usd)??a(e?.underlying_index_usd?.[n])??a(l.groups?.underlying_index_usd?.[n])??a(l.lastSpotUsd?.[n])}function yn(t,e){let n=D(t);return n==="USDC"?null:a(t?.close_index_usd)??a(e?.underlying_index_usd?.[n])??a(l.groups?.underlying_index_usd?.[n])??a(l.lastSpotUsd?.[n])??a(t?.entry_index_usd)}function Uo(t,e){let n=on(t);if(n!=="BTC"&&n!=="ETH")return null;let o=[a(t?.entry_index_usd),a(t?.close_index_usd),a(e?.underlying_index_usd?.[n]),a(l.groups?.underlying_index_usd?.[n]),a(l.lastSpotUsd?.[n]),Z(t,"short")];for(let r of o)if(r!==null&&r>100)return r;return null}function Le(t,e){let n=gt(t);if(n===null||n<=0)return null;let o=a(t?.realized_apr_on_equity)??a(t?.realized_annualized_return);return o!==null?o:mn(t,e)}function Po(t,e,n=null){let o=n??l.groups;if(U(t)==="bull_put_spread"){let i=tt(t,e,"short",o),c=tt(t,e,"long",o);if(i===null&&c===null){let p=a(t.quantity);return p===null?null:`${S(-Math.abs(p),4)} / ${S(Math.abs(p),4)}`}let d=[];return i!==null&&d.push(S(i,4)),c!==null&&d.push(S(c,4)),d.length?d.join(" / "):null}if(!zt(t)){let i=io(t,e,o);return i==="\u2014"?null:i}let s=a(t.quantity);return s===null?null:S(-Math.abs(s),4)}function No(t,e){let n=a(t?.entry_credit);if(n===null)return null;let o=a(t?.entry_fee)??0,r=D(t),s=a(t?.short_entry_average_price),i=a(t?.quantity),c=jt(t,e),d=n;if(o>0&&s!==null&&s>0&&i!==null&&i>0&&c!==null&&c>0){let p=s*i*c,f=Math.max(.01,Math.abs(p)*.001);Math.abs(p-n)<=f?d=n-o:Math.abs(p-(n+o))<=f&&(d=n)}return r==="USDC"?d:c===null||c<=0?null:d/c}function Zt(t,e){let n=Ro(t),o=fn(t,e),r=No(t,e);return r===null||r<=0||n===null||n<=0||o===null||o<=0?a(t?.entry_net_apr):r/o*(365/n)}function Jt(t){return a(t?.entry_fee)}function Ae(t,e){return Be(Jt(t),jt(t,e))}function Kt(t){let e=a(t?.current_close_fee);return e!==null&&e>0?e:a(t?.realized_close_fee)}function De(t,e){let n=a(t?.current_close_fee),o=n!==null&&n>0?bt(t,e):yn(t,e);return Be(Kt(t),o)}function Xt(t,e){return Be(a(t?.entry_credit),jt(t,e))}function Io(t,e){let n=[],o=new Set,r=s=>{if(!s)return;let i=pt(s);o.has(i)||(o.add(i),n.push(s))};for(let s of t?.trade_groups||[])r(s);for(let s of e?.open||[])r(s);for(let s of e?.closed||[])r(s);return n}function hn(t,e){return Ft(Io(t,e)).filter(n=>Nt(n)).sort((n,o)=>(nt(o)||0)-(nt(n)||0))}function _n(t,e,n){return pn(e,n,500,t)}function Ue(t,e,n){let o=t.length,r=Math.max(1,Math.ceil(o/n)),s=Math.min(Math.max(1,e),r),i=(s-1)*n;return{rows:t.slice(i,i+n),page:s,totalPages:r,total:o,start:o?i+1:0,end:Math.min(i+n,o)}}function Pe(t,e){let{page:n,totalPages:o,total:r,start:s,end:i}=e;if(r<=dt)return"";let c=n<=1,d=n>=o,p=u(`${s}\u2013${i} of ${r} \xB7 page ${n} of ${o}`,`${s}\u2013${i} / \u5171 ${r} \u7B46 \xB7 \u7B2C ${n} / ${o} \u9801`);return`<div class="activity-pagination" data-activity-section="${escapeHtml(t)}">
      <button type="button" class="filter-chip activity-page-btn" data-activity-section="${escapeHtml(t)}" data-direction="prev"${c?" disabled":""}>${u("Prev","\u4E0A\u4E00\u9801")}</button>
      <span class="activity-pagination-label">${escapeHtml(p)}</span>
      <button type="button" class="filter-chip activity-page-btn" data-activity-section="${escapeHtml(t)}" data-direction="next"${d?" disabled":""}>${u("Next","\u4E0B\u4E00\u9801")}</button>
    </div>`}function Oo(t){let e=String(t?.currency||"").toUpperCase()||"Option",n=String(t?.short_instrument_name||"");if(n){let o=n.split("-").slice(-2).join(" ");return`${e} ${o}`.trim()}try{return openPositionTitle(t)}catch{return`${e} trade`}}function ye(t){return t.filter(e=>e).map(e=>typeof e=="string"?`<span>${escapeHtml(e)}</span>`:`<span>${escapeHtml(e[0])} <strong>${escapeHtml(String(e[1]))}</strong></span>`).join("")}function xn(t,e,n){let o=U(t),r=D(t)||"\u2014",s=Zt(t,e),i=Jt(t),c=Kt(t),d=a(t.entry_credit),p=Ae(t,e),f=De(t,e),m=Xt(t,e),v=nt(t),y=zt(t),h=Gt(t,e),x=gt(t),w=y?Le(t,e):null,T=Po(t,e,n),R=Oo(t),B=d===null?"\u2014":Ut(d,m,r),b=s===null?"\u2014":k(s,1),$=i===null?null:Ut(i,p,r),O=[[u("Opened","\u958B\u5009"),Ze(v)],T!==null?[u("Amount","\u6578\u91CF"),T]:null,$?[u("Entry fee","\u9032\u5834\u624B\u7E8C\u8CBB"),$]:null].filter(Boolean),M=`<div class="activity-entry-metrics">
      <div class="activity-entry-metric">
        <span class="activity-entry-metric-label">${u("Credit","\u6536\u6B0A\u5229\u91D1")}</span>
        <span class="activity-entry-metric-value ${E(d)}">${escapeHtml(B)}</span>
      </div>
      <div class="activity-entry-metric">
        <span class="activity-entry-metric-label">${u("Net APR","\u6DE8\u5E74\u5316\u5831\u916C\u7387")}</span>
        <span class="activity-entry-metric-value ${E(s)}">${escapeHtml(b)}</span>
      </div>
    </div>
    <div class="activity-phase-meta activity-phase-meta-secondary">
      ${ye(O)}
    </div>`,I="";if(y){let W=[[u("Closed","\u5E73\u5009"),Ze(closedTimestampMs(t))],c!==null?[u("Close fee","\u5E73\u5009\u624B\u7E8C\u8CBB"),Ut(c,f,r)]:null,x!==null?[u("Held","\u6301\u6709"),`${S(x,1)}${L?" \u5929":"d"}`]:null].filter(Boolean),Lt=h!==null?`<span class="activity-closed-pnl-value ${E(h)}">${Do(t,e)}</span>`:'<span class="activity-closed-pnl-value activity-closed-pnl-value-missing">\u2014</span>',At=w!==null?`<span class="activity-closed-pnl-value ${E(w)}">${k(w,1)}</span>`:'<span class="activity-closed-pnl-value activity-closed-pnl-value-missing">\u2014</span>';I=`${`<div class="activity-closed-metrics">
        <div class="activity-closed-pnl">
          <span class="activity-closed-pnl-label">${u("Realized PnL","\u5DF2\u5BE6\u73FE\u640D\u76CA")}</span>
          ${Lt}
        </div>
        <div class="activity-closed-pnl">
          <span class="activity-closed-pnl-label">${u("Realized APR","\u5BE6\u73FE\u5E74\u5316\u5831\u916C")}</span>
          ${At}
        </div>
      </div>`}<div class="activity-phase-meta activity-phase-meta-secondary">${ye(W)}</div>`}else{let W=[c!==null?[u("Est. close fee","\u9810\u4F30\u5E73\u5009\u8CBB"),Ut(c,f,r)]:null].filter(Boolean);I=`<div class="activity-phase-meta">
        <span class="activity-status-pill is-open">${u("Open","\u6301\u5009\u4E2D")}</span>
        ${W.length?ye(W):`<span>${u("Est. close fee","\u9810\u4F30\u5E73\u5009\u8CBB")} <strong>\u2014</strong></span>`}
      </div>`}let G=!_&&ft(t)?ft(t):"";return`
    <li class="activity-card">
      <div class="activity-card-head">
        ${K(o)}
        <span class="activity-card-title">${escapeHtml(R)}</span>
        <span class="text-[11px] text-slate-500">${escapeHtml(r)}</span>
        ${G?`<span class="text-[11px] text-slate-500">${escapeHtml(G)}</span>`:""}
      </div>
      <div class="activity-card-instrument">${escapeHtml(t.short_instrument_name||"")}</div>
      <div class="activity-lifecycle">
        <div class="activity-phase activity-phase-entry">
          <div class="activity-phase-label">${u("Entry","\u9032\u5834")}</div>
          ${M}
        </div>
        <div class="activity-phase-divider" aria-hidden="true"></div>
        <div class="activity-phase activity-phase-exit">
          <div class="activity-phase-label">${u("Exit","\u51FA\u5834")}</div>
          ${I}
        </div>
      </div>
    </li>`}function N(t,e){let n=document.getElementById(t);n&&(n.textContent=e)}function A(t){let e=document.getElementById("toast");e&&(e.textContent=t,e.classList.remove("hidden"),clearTimeout(A._t),A._t=setTimeout(()=>e.classList.add("hidden"),5e3))}function at(t){return new Promise(e=>setTimeout(e,t))}async function gn(t,e){let n=0,o=Math.max(1,Math.min(e||1,t.length));async function r(){for(;;){let s=n++;if(s>=t.length)break;await t[s]()}}await Promise.all(Array.from({length:o},()=>r()))}async function P(t,e={}){let n=Fe(t),o=We+1;for(let r=0;r<o;r++){let s;try{s=await fetch(n,e)}catch(c){if(r<o-1){await at(me*(r+1));continue}throw c}if(s.ok)return s.json();let i=`${s.status} ${s.statusText}`;try{let c=await s.json();c?.detail&&(i=`${s.status} ${c.detail}`)}catch{}if(Ge.has(s.status)&&r<o-1){await at(me*(r+1));continue}throw new Error(i)}}function ee(){return{responsive:!0,maintainAspectRatio:!1,animation:!1,interaction:{mode:"nearest",intersect:!1},plugins:{legend:{labels:{color:"rgb(203 213 225)",boxWidth:12,padding:8}},tooltip:{backgroundColor:"rgba(15,23,42,0.95)",borderColor:"rgb(51,65,85)",borderWidth:1,titleColor:"rgb(226,232,240)",bodyColor:"rgb(226,232,240)"}},scales:{x:{type:"time",time:{tooltipFormat:"yyyy-LL-dd HH:mm"},grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)"}},y:{grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)"}}}}}function wt(t){let e=l.charts[t];if(!e)return;let n=e.canvas;e.destroy(),l.charts[t]=null,n&&(n.removeAttribute("width"),n.removeAttribute("height"),n.style.width="",n.style.height="")}function kt(t){let e=document.getElementById(t);return e?e.getContext("2d"):null}function Qt(){Object.values(l.charts).forEach(t=>{try{t?.resize?.()}catch{}})}function Et(){requestAnimationFrame(()=>{Qt(),window.setTimeout(Qt,80),window.setTimeout(Qt,320)})}var bn=!1;function kn(){bn||typeof ResizeObserver>"u"||(bn=!0,document.querySelectorAll(".chart-panel-canvas").forEach(t=>{t.querySelector("canvas")?.id&&new ResizeObserver(()=>Qt()).observe(t)}))}function Ho(){let t=a(l.status?.portfolio?.total_equity_usdc);return t!==null&&t>0?t:null}function ne(){let t=`/api/apr_series?window_days=${l.aprWindow}`,e=Ho();return e!==null&&(t+=`&effective_capital_usdc=${encodeURIComponent(String(e))}`),t}function En(){let t=luxon.DateTime.now().toUTC().startOf("day");return{min:t.minus({days:Math.max(l.aprWindow,30)}).toMillis(),max:t.toMillis()}}function Fo(t){let e=document.getElementById(t);return e?e.closest(".chart-panel-canvas")||e.parentElement:null}function Tt(t,{empty:e,message:n=""}={}){let o=Fo(t);if(!o)return;let r=o.querySelector(".chart-empty-overlay");if(!e){r?.remove(),o.classList.remove("chart-panel-canvas--empty");return}o.classList.add("chart-panel-canvas--empty"),r||(r=document.createElement("div"),r.className="chart-empty-overlay",o.appendChild(r)),r.textContent=n}var $n={realized:{en:"No closed positions yet \u2014 this chart fills in after the first close.",zh:"\u5C1A\u7121\u5E73\u5009\u7D00\u9304 \u2014 \u9996\u6B21\u5E73\u5009\u5F8C\u6B64\u5716\u8868\u624D\u6703\u958B\u59CB\u7D2F\u7A4D\u3002"},apr:{en:"Rolling APR needs closed trades and daily equity snapshots.",zh:"\u6EFE\u52D5\u5E74\u5316\u9700\u6709\u5E73\u5009\u7D00\u9304\u8207\u6BCF\u65E5\u6B0A\u76CA\u5FEB\u7167\u3002"}};function zo(t){let e=$n[t]||$n.realized;return u(e.en,e.zh)}function qo({yPercent:t=!1,chartType:e="line"}={}){let n=En(),o=ee(),r=t?-.1:-50,s=t?.1:50;return{...o,plugins:{...o.plugins,legend:{display:!1},tooltip:{enabled:!1}},scales:{x:{...o.scales.x,...n,display:!0,offset:e==="bar",time:{unit:"day",round:"day",tooltipFormat:"yyyy-LL-dd"}},y:{...o.scales.y,display:!0,min:r,max:s,ticks:{...o.scales.y.ticks,maxTicksLimit:6,...t?{callback:i=>k(i,1)}:{}}}}}}function Ct(t,e,{yPercent:n=!1,chartType:o="line",messageKind:r="realized"}={}){let s=kt(t);if(!s)return;wt(e),Tt(t,{empty:!0,message:zo(r)});let i=En(),c=[{x:i.min,y:0},{x:i.max,y:0}];l.charts[e]=new Chart(s,{type:"line",data:{datasets:[{label:u("No realized history yet","\u5C1A\u7121\u5DF2\u5BE6\u73FE\u7D00\u9304"),data:c,borderWidth:1,pointRadius:0,borderColor:"rgba(148, 163, 184, 0.35)",backgroundColor:"transparent"}]},options:qo({yPercent:n,chartType:o})})}function Ne(){return l.bookFilter==="ALL"?Y:[l.bookFilter]}function Tn(t,e,n){let o=Object.fromEntries(q.map(r=>[r.id,0]));for(let r of t||[]){let s=U(r);if(!V[s])continue;let i=ot(r,e,n);i!==null&&(o[s]+=i)}return o}function Rn(t,e,n=null){let o=n??l.status;return Ft([...e?.closed||[],...t?.recent_closed_trades||[]]).filter(r=>qt(r,o,e)).filter(r=>a(r?.realized_pnl)!==null)}function Bn(t,e,n){let o={BTC:0,ETH:0,USDC:0};for(let r of Rn(t,e)){let s=D(r);if(s!=="BTC"&&s!=="ETH"&&s!=="USDC")continue;let i=$t(r,n);i!==null&&(o[s]+=i)}return o}function Ln(t,e,n,o){let r={BTC:0,ETH:0,USDC:0},s=o??30,i=Date.now()-s*24*3600*1e3;for(let c of Rn(t,e)){let d=closedTimestampMs(c);if(d===null||d<i)continue;let p=D(c);if(p!=="BTC"&&p!=="ETH"&&p!=="USDC")continue;let f=$t(c,n);f!==null&&(r[p]+=f)}return r}function An(t){let e=t?.accounts||{},n={},o=!1;for(let r of Y)n[r]=a(e[r]?.equity),n[r]!==null&&(o=!0);if(!o){let{portfolio:r}=mt();for(let s of Y)n[s]=a(r?.equity_by_book?.[s])}return n}function Vo(){return{responsive:!0,maintainAspectRatio:!1,animation:!1,interaction:{mode:"index",intersect:!1},plugins:{legend:{labels:{color:"rgb(203 213 225)",boxWidth:12,padding:8}},tooltip:{backgroundColor:"rgba(15,23,42,0.95)",borderColor:"rgb(51,65,85)",borderWidth:1,titleColor:"rgb(226,232,240)",bodyColor:"rgb(226,232,240)"}},scales:{x:{grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)"}},y:{beginAtZero:!0,grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)",maxTicksLimit:8}}}}}function Rt(){let t=kt("chart-risk-capital");if(!t)return;wt("riskCapital");let e=Ne(),n=l.status?.portfolio,o=e.map(p=>{let f=Mt(p,l.status);return f!==null?f:0}),r=a(n?.total_equity_usdc),s=o.reduce((p,f)=>p+f,0),i=u(`Total ${g(r)}`,`\u5408\u8A08 ${g(r)}`);r!==null&&s>0&&Math.abs(s-r)>1?i+=u(" \xB7 bars sum may differ from headline"," \xB7 \u5404\u5E33\u52A0\u7E3D\u53EF\u80FD\u8207\u7E3D\u89BD\u7565\u6709\u5DEE\u7570"):l.status||(i=u("Awaiting live snapshot","\u7B49\u5F85\u5373\u6642\u5FEB\u7167")),N("risk-capital-meta",i),N("risk-capital-hint",u("Per-book equity in USDC equivalent from the live snapshot (or last saved snapshot).","\u5404\u5E33\u672C\u6B0A\u76CA\u4EE5 USDC \u7D04\u7576\u986F\u793A\uFF0C\u4F86\u81EA\u5373\u6642\u6216\u6700\u8FD1\u5FEB\u7167\u3002"));let c=e.map(p=>st[p]||"#94a3b8"),d=Vo();Tt("chart-risk-capital",{empty:!1}),l.charts.riskCapital=new Chart(t,{type:"bar",data:{labels:e,datasets:[{label:u("Book equity (USDC eq.)","\u5E33\u672C\u6B0A\u76CA\uFF08USDC \u7D04\u7576\uFF09"),data:o,backgroundColor:c.map(p=>p+"cc"),borderColor:c,borderWidth:1}]},options:{...d,plugins:{...d.plugins,tooltip:{...d.plugins.tooltip,callbacks:{afterBody(p){if(!p?.length)return"";let f=p[0].dataIndex;if(f===void 0)return"";let m=o[f]??0,v=r>0?m/r:null;return[`${u("Share of total: ","\u4F54\u7E3D\u6B0A\u76CA\uFF1A")}${k(v,2)}`]}}}}}})}var te=864e5;function ut(t){let e=luxon.DateTime.fromISO(String(t||"").trim(),{zone:"utc"});return e.isValid?e.toMillis():NaN}function Sn(t){let e=t.filter(n=>Number.isFinite(n.x)&&n.y!==null&&Number.isFinite(n.y)).sort((n,o)=>n.x-o.x);if(e.length===0)return[];if(e.length===1){let n=e[0];return[{x:n.x-te,y:0},{x:n.x,y:n.y},{x:n.x+te,y:n.y}]}return e}function St(t){return t.filter(e=>Number.isFinite(e.x)&&e.y!==null&&Number.isFinite(e.y)).sort((e,n)=>e.x-n.x)}function Dn(t){let e=St(t);if(e.length===0)return[];if(e.length===1){let n=e[0];return[n,{x:n.x+te,y:n.y}]}return e}function Un(t){let e=(t||[]).map(i=>i.x).filter(Number.isFinite);if(!e.length)return{};let n=Math.min(...e),o=Math.max(...e),r=o-n,s=te;return e.length===1||r<s*.25?{min:n-s,max:o+s}:{}}function oe(){let t=kt("chart-cum-pnl");if(!t)return;wt("cumPnl");let e=l.cumulativePnl,n=e?.realized_count?`${e.realized_count} closed groups`:u("no closed groups","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44");if(N("cum-pnl-meta",n),!e){Ct("chart-cum-pnl","cumPnl");return}let o=[],r=Ne();for(let s of r){let i=e.cumulative_by_book?.[s]||[];if(i.length){let c=Sn(i.map(d=>({x:ut(d.date),y:a(d.pnl_usdc)})));c.length&&o.push({label:`${s} cum. PnL`,data:c,borderColor:st[s],backgroundColor:st[s]+"22",stepped:!0,pointRadius:0,borderWidth:2})}}if(l.bookFilter==="ALL"&&e.cumulative_total?.length){let s=Sn(e.cumulative_total.map(i=>({x:ut(i.date),y:a(i.pnl_usdc)})));s.length&&o.push({label:"Total cum. PnL",data:s,borderColor:st.TOTAL,backgroundColor:st.TOTAL+"22",stepped:!0,pointRadius:0,borderWidth:2,borderDash:[4,4]})}if(!o.length){Ct("chart-cum-pnl","cumPnl");return}Tt("chart-cum-pnl",{empty:!1}),l.charts.cumPnl=new Chart(t,{type:"line",data:{datasets:o},options:ee()})}function Go(t){return t.filter(e=>Math.abs(e.y)>1e-12)}var Wo="rgba(52, 211, 153, 0.67)",Yo="#34d399",jo="rgba(251, 113, 133, 0.67)",Zo="#fb7185";function Cn(t){return t.map(e=>{let n=a(e.y)??0;return n>0?Wo:n<0?jo:"rgba(148, 163, 184, 0.4)"})}function wn(t){return t.map(e=>{let n=a(e.y)??0;return n>0?Yo:n<0?Zo:"#94a3b8"})}function re(){let t=kt("chart-daily-pnl");if(!t)return;wt("dailyPnl");let e=30,n=l.cumulativePnl;if(!n){N("daily-pnl-meta",u("no closed groups","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44")),Ct("chart-daily-pnl","dailyPnl",{chartType:"bar"});return}let o=Ne(),r=(n.daily_total||[]).filter(m=>Number.isFinite(ut(m.date))),s=n?.daily_total?.length?`${n.daily_total.length} ${u("active days","\u500B\u6709\u6548\u4EA4\u6613\u65E5")}`:u("no closed groups","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44");l.bookFilter==="ALL"&&r.length>=e&&(s+=" \xB7 30d SMA"),N("daily-pnl-meta",s);let i=m=>({x:ut(m.date),y:a(m.pnl_usdc)}),c=[];if(l.bookFilter==="ALL"){let m=St((n.daily_total||[]).map(i));m.length&&c.push({type:"bar",label:u("Daily total","\u6BCF\u65E5\u5408\u8A08"),data:m,order:1,backgroundColor:Cn(m),borderColor:wn(m),borderWidth:1})}else for(let m of o){let v=n.daily_by_book?.[m]||[],y=St(v.map(i));y=Go(y),y.length&&c.push({type:"bar",label:`${m} ${u("daily","\u6BCF\u65E5")}`,data:y,order:1,backgroundColor:Cn(y),borderColor:wn(y),borderWidth:1})}if(l.bookFilter==="ALL"&&r.length>=e){let m=[];for(let y=e-1;y<r.length;y++){let h=0;for(let x=y-e+1;x<=y;x++)h+=a(r[x].pnl_usdc)||0;m.push({x:ut(r[y].date),y:h/e})}let v=Dn(St(m));v.length&&c.push({type:"line",label:`30d SMA (${e}-day realized avg.)`,data:v,order:2,borderColor:"#f472b6",backgroundColor:"#f472b633",tension:.15,pointRadius:0,borderWidth:2})}if(!c.length){Ct("chart-daily-pnl","dailyPnl",{chartType:"bar"});return}Tt("chart-daily-pnl",{empty:!1});let d=c.flatMap(m=>m.data||[]),p=Un(d),f=ee();l.charts.dailyPnl=new Chart(t,{type:"bar",data:{datasets:c},options:{...f,scales:{x:{...f.scales.x,...p,offset:!0,time:{unit:"day",tooltipFormat:"yyyy-LL-dd"}},y:{...f.scales.y,ticks:{...f.scales.y.ticks,maxTicksLimit:10}}}}})}function se(){let t=kt("chart-apr");if(!t)return;wt("apr");let e=l.aprSeries?.rows||[],n=Dn(St(e.map(s=>({x:ut(s.date),y:a(s.apr)}))));if(!n.length){Ct("chart-apr","apr",{yPercent:!0,messageKind:"apr"});return}Tt("chart-apr",{empty:!1});let o=Un(n),r=ee();l.charts.apr=new Chart(t,{type:"line",data:{datasets:[{label:`Rolling APR (${l.aprWindow}d)`,data:n,borderColor:"#facc15",backgroundColor:"rgba(250,204,21,0.15)",tension:.25,pointRadius:0,borderWidth:2,fill:!0}]},options:{...r,scales:{x:{...r.scales.x,...o,time:{unit:"day",tooltipFormat:"yyyy-LL-dd"}},y:{...r.scales.y,ticks:{...r.scales.y.ticks,callback:s=>k(s,1)}}}}})}function Ko(t){if(!_||!t)return;let e=String(t.investor_display_name||t.investor_id||"").trim(),n=document.querySelector(".app-header h1");n&&e&&(n.textContent=`${e} \xB7 ${L?"\u6295\u8CC7\u7D44\u5408\u7E3D\u89BD":"Investor summary"}`);let o=document.querySelector(".app-header h1 + p");if(!o)return;o.dataset.investorBaseCopy||(o.dataset.investorBaseCopy=o.textContent||"");let r=o.dataset.investorBaseCopy,s=String(t.investor_id||"").trim();o.textContent=s&&s!==e?`${u("Investor id","\u6295\u8CC7\u4EBA ID")}: ${s} \xB7 ${r}`:r}function Xo(t){return _?t==="mainnet"?"border-sky-500/50 bg-sky-500/10 text-sky-200":t==="test"?"border-amber-500/50 bg-amber-500/10 text-amber-200":"border-slate-500/50 bg-slate-500/10 text-slate-200":t==="mainnet"?"border-rose-500/50 bg-rose-500/10 text-rose-200":"border-emerald-500/50 bg-emerald-500/10 text-emerald-200"}function ie(t){if(!t)return;Ko(t);let e=(t.env||"").toLowerCase(),n=document.getElementById("env-badge");n&&(n.textContent=_?e==="mainnet"?u("Network: Mainnet","\u7DB2\u8DEF\uFF1A\u4E3B\u7DB2"):e==="multi"?u("Network: Multi-account","\u7DB2\u8DEF\uFF1A\u591A\u5E33\u6236"):e==="test"?u("Network: Test","\u7DB2\u8DEF\uFF1A\u6E2C\u8A66"):`${u("Network:","\u7DB2\u8DEF\uFF1A")} ${e||"\u2014"}`:`env: ${e||"?"}`,n.className="text-xs px-2 py-0.5 rounded-full border "+Xo(e));let o=document.getElementById("strategy-badge");if(o){let i=J(t.option_strategy||""),c=t.accounts?.length||0;o.textContent=t.multi_account?u(`strategy: multi (${c} accounts)`,`\u7B56\u7565\uFF1A\u591A\u5E33\u6236\uFF08${c}\uFF09`):_?`${u("Strategy:","\u7B56\u7565\uFF1A")} ${i?Ht(i):"\u2014"}`:`strategy: ${i?Ht(i):"?"}`,o.className="text-xs px-2 py-0.5 rounded-full border border-sky-500/50 bg-sky-500/10 text-sky-200"}let r=document.getElementById("creds-badge");r&&(r.textContent=t.has_private_creds?"creds: ok":"creds: missing",r.className="text-xs px-2 py-0.5 rounded-full border "+(t.has_private_creds?"border-emerald-500/50 bg-emerald-500/10 text-emerald-200":"border-rose-500/50 bg-rose-500/10 text-rose-200"));let s=document.getElementById("scheduler-badge");if(s)if(t.scheduler_running){let i=t.snapshot_interval_sec||300,c=Math.round(i/60);s.textContent=u(`scheduler: on (every ${c} min)`,`\u5FEB\u7167\u6392\u7A0B\uFF1A\u6BCF ${c} \u5206\u9418`),s.className="text-xs px-2 py-0.5 rounded-full border border-emerald-500/50 bg-emerald-500/10 text-emerald-200"}else s.textContent=u("scheduler: off","\u5FEB\u7167\u6392\u7A0B\uFF1A\u95DC\u9589"),s.className="text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-700/30 text-slate-300";he()}function On(t){let e=document.getElementById("regime-badge");if(!e)return;let n=t?.portfolio?.regime||"?",o=String(n).toLowerCase(),r={normal:"\u6B63\u5E38",elevated:"\u504F\u9AD8",crisis:"\u8B66\u6212"},s={normal:"Normal",elevated:"Elevated",crisis:"Crisis"};e.textContent=_?`${u("Risk posture:","\u98A8\u63A7\u72C0\u614B\uFF1A")} ${L?r[o]||n:s[o]||n}`:`regime: ${n}`;let i=n==="normal"?"border-emerald-500/50 bg-emerald-500/10 text-emerald-200":n==="elevated"?"border-amber-500/50 bg-amber-500/10 text-amber-200":n==="crisis"?"border-rose-500/50 bg-rose-500/10 text-rose-200":"border-slate-600 bg-slate-700/30 text-slate-300";e.className=`text-xs px-2 py-0.5 rounded-full border ${i}`}function Qo(t,e){let n=e?.portfolio||{},r=(e?.accounts||{})[t]||{},s=$e(n?.equity_by_book,t),i=a(r.equity),c=Mt(t,e),d=s?a(n?.day_start_equity_by_book?.[t]):null,p=a(n?.day_drawdown_pct_by_book?.[t]),f=sn(t,e,c,d),m=n?.margin_ratios_by_currency?.[t]||{},v=a(m.im_ratio),y=a(m.mm_ratio),h=a(n?.delta_totals_by_currency?.[t]),x=n?.regime_by_currency?.[t],w=n?.cooling_down_by_book?.[t],T=n?.hard_derisk_by_book?.[t],R=n?.halt_entries_by_book?.[t],B=n?.halt_entry_reasons_by_book?.[t]||[],b=t==="BTC"?"book-card-btc":t==="ETH"?"book-card-eth":"book-card-usdc",$=[];if(s||$.push('<span class="chip chip-muted">not traded</span>'),x&&s){let W=x==="normal"?"chip-ok":x==="elevated"?"chip-warn":"chip-bad";$.push(`<span class="chip ${W}">${x}</span>`)}w&&$.push('<span class="chip chip-warn">cooling</span>'),T&&$.push('<span class="chip chip-bad">hard derisk</span>'),R&&$.push('<span class="chip chip-warn">halt entries</span>'),$.length===0&&$.push('<span class="chip chip-ok">healthy</span>');let O=v!==null?Math.min(1,Math.max(0,v)):0,M=v===null?"bar-ok":v>=.45?"bar-bad":v>=.35?"bar-warn":"bar-ok",I=y!==null?Math.min(1,Math.max(0,y)):0,G=y===null?"bar-ok":y>=.33?"bar-bad":y>=.22?"bar-warn":"bar-ok";return`
    <div class="rounded-2xl border ${b} bg-slate-900/60 p-4 shadow">
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-sm font-semibold tracking-wide text-slate-200">${t} BOOK</h3>
        <div class="flex flex-wrap gap-1">${$.join("")}</div>
      </div>
      <div class="text-2xl font-mono">${g(c)}</div>
      <div class="text-xs text-slate-500 mb-3">
        ${i!==null?S(i,8)+" "+t:""}
        ${d!==null?"\xB7 day-start "+g(d):""}
      </div>
      <div class="kv"><span class="k">Day P&amp;L</span><span class="v ${E(f)}">${g(f)}</span></div>
      <div class="kv"><span class="k">Day drawdown</span><span class="v ${E(p===null?null:-p)}">${k(p)}</span></div>
      <div class="kv"><span class="k">Delta total</span><span class="v">${S(h,4)}</span></div>
      <div class="mt-3 space-y-2">
        <div>
          <div class="flex justify-between text-xs text-slate-400">
            <span>IM ratio</span><span class="font-mono">${k(v,2)}</span>
          </div>
          <div class="mini-bar"><span class="${M}" style="width:${(O*100).toFixed(1)}%"></span></div>
        </div>
        <div>
          <div class="flex justify-between text-xs text-slate-400">
            <span>MM ratio</span><span class="font-mono">${k(y,2)}</span>
          </div>
          <div class="mini-bar"><span class="${G}" style="width:${(I*100).toFixed(1)}%"></span></div>
        </div>
      </div>
      ${B.length?`<p class="mt-3 text-xs text-rose-300">${B.map(C).join("<br>")}</p>`:""}
    </div>
  `}function C(t){return String(t??"").replace(/[&<>"']/g,e=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[e])}function Mn(t){let e=document.getElementById("book-cards");if(!e)return;if(!t){e.innerHTML=`
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm md:col-span-3">
        Need DERIBIT_CLIENT_ID/SECRET in <code>.env</code> to load live status.
        Read-only views (closed trades, cumulative PnL) still work below.
      </div>`;return}let n=Object.keys(t?.portfolio?.equity_by_book||{}).map(s=>String(s).toUpperCase()).filter(s=>Y.includes(s)),r=(n.length?n:Y).map(s=>Qo(s,t)).join("");e.innerHTML=r}function Hn(t,e){let n=document.getElementById("account-cards");if(!n)return;let o=t?.accounts||e?.dashboard_accounts||[],r=new Map((e?.account_statuses||[]).map(i=>[String(i.name||""),i])),s=o.length?o:e?.account_statuses||[];if(!s.length){n.innerHTML=`
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm md:col-span-3">
        No dashboard account metadata yet.
      </div>`;return}n.innerHTML=s.map(i=>{let c=String(i.name||""),d=r.get(c)||i,p=d.portfolio||{},f=a(p.total_equity_usdc),m=a(p.day_start_equity_usdc),v=Se(p,f,m),y=p.regime||"\u2014",h=a(d.trade_group_count),x=i.has_private_creds,w=d.option_strategy||i.option_strategy||"",T=d.env||i.env||"",R=i.state_file||d.state_file||"",B=[w?K(w):"",x===void 0?"":`<span class="chip ${x?"chip-ok":"chip-bad"}">creds ${x?"ok":"missing"}</span>`].filter(Boolean);return`
        <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 shadow">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h3 class="text-sm font-semibold tracking-wide text-slate-100">${C(c||"account")}</h3>
              <p class="text-xs text-slate-500 mt-1 break-all">${C(T)} \xB7 ${C(R)}</p>
            </div>
            <div class="flex flex-wrap justify-end gap-1 flex-shrink-0">${B.join("")}</div>
          </div>
          <div class="stat-grid mt-4">
            <div class="stat-tile">
              <div class="label">Equity</div>
              <div class="value">${g(f)}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Day P&amp;L</div>
              <div class="value ${E(v)}">${g(v)}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Open groups</div>
              <div class="value">${h??"\u2014"}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Regime</div>
              <div class="value">${C(y)}</div>
            </div>
          </div>
        </div>
      `}).join("")}function Fn(t,e){let n=document.getElementById("aggregate-card");if(!n)return;let{portfolio:o,source:r}=mt(),s=e?.summary;if(!o&&!s){_&&!l.investorReady?n.innerHTML=Xe():n.innerHTML=`<p class="text-sm text-slate-400">${u("No status / report data yet.","\u5C1A\u7121\u5373\u6642\u5E33\u6236\u6216\u7E3E\u6548\u6458\u8981\u8CC7\u6599\u3002")}</p>`;return}let i=a(o?.total_equity_usdc),c=a(o?.day_start_equity_usdc),d=Se(o,i,c),p=a(o?.day_drawdown_pct),f=_t(t,l.groups),m=f.reduce((Xn,Qn)=>Xn+(ot(Qn,t,l.groups)||0),0),v=Tn(f,t,l.groups),y=a(s?.realized_pnl_usdc),h=a(s?.lifetime_realized_apr),x=a(s?.realized_win_rate),w=a(s?.avg_holding_days),T=a(s?.realized_closed_group_count),R=a(s?.window_days_used),B=a(s?.window_realized_pnl_usdc),b=a(s?.window_realized_apr),$=dn(e,l.groups),O=Bn(e,l.groups,t),M=R??30,I=Ln(e,l.groups,t,M),G=An(t),W=$!==null?`${u("since","\u81EA")} ${an($)}`:u("no realized history yet","\u5C1A\u7121\u5DF2\u5BE6\u73FE\u7D00\u9304"),Lt=r==="snapshot"&&_?`<p class="text-xs text-amber-200/80 mt-3">${u("Equity from last snapshot; live sync continues in background.","\u6B0A\u76CA\u4F86\u81EA\u6700\u8FD1\u5FEB\u7167\uFF1B\u5373\u6642\u540C\u6B65\u65BC\u80CC\u666F\u9032\u884C\u4E2D\u3002")}</p>`:r==="live"&&_?`<p class="text-xs text-emerald-200/70 mt-3">${u("Live Deribit sync","\u5DF2\u540C\u6B65 Deribit \u5373\u6642\u8CC7\u6599")}</p>`:"",At={totalEquity:i,dayStart:c,dayPnl:d,dayDrawdown:p,openCredit:m,creditByStrategy:v,summary:s,winRate:x,avgHolding:w,sinceLine:W,lifetimePnl:y,lifetimeNativeByBook:O,closedCount:T,windowLabelDays:M,windowPnl:B,windowNativeByBook:I,lifetimeApr:h,windowApr:b,equityNativeByBook:G},pe=Qe(At);_?n.innerHTML=`
      <div class="investor-view-desktop">${pe}</div>
      <div class="investor-view-mobile">${tn(At)}</div>
      ${Lt}`:n.innerHTML=`${pe}${Lt}`,he()}function Ie(t){return{id:t,openCount:0,closedCount:0,wins:0,openEntryCredit:0,unrealizedUsd:0,realizedPnl:0,annualizedSum:0,annualizedCount:0,annualizedWeightedSum:0,annualizedWeight:0,aprPnlUsdSum:0,aprCapitalDays:0,holdingSum:0,holdingCount:0,books:new Set}}function Pn(t,e,n){let o=n||"";return e.add(o),t.has(o)||t.set(o,Ie(o)),t.get(o)}function tr(t,e,n){if(n===null||n<=0)return null;let o=Wt(t,e);if(o===null||o<=0)return null;let r=D(t);if(r==="USDC")return o*n;let s=a(e?.underlying_index_usd?.[r])??a(l.lastSpotUsd?.[r]);return s===null||s<=0?null:o*s*n}function er(t){return t.aprCapitalDays>0?t.aprPnlUsdSum/t.aprCapitalDays*365:null}function nr(t,e,n){let o=new Set(q.map(d=>d.id)),r=new Map;for(let d of o)r.set(d,Ie(d));let s=_t(t,n);for(let d of s){let p=U(d);if(!V[p])continue;let f=Pn(r,o,p);f.openCount+=1;let m=ot(d,t,n);m!==null&&(f.openEntryCredit+=m);let v=ht(d,t,n);v!==null&&(f.unrealizedUsd+=v);let y=F(d);y&&f.books.add(y)}let i=ke(e,n);for(let d of i){let p=U(d);if(!V[p])continue;let f=Pn(r,o,p);f.closedCount+=1;let m=Gt(d,t);m!==null&&(f.realizedPnl+=m,m>0&&(f.wins+=1));let v=gt(d);v!==null&&(f.holdingSum+=v,f.holdingCount+=1);let y=Wt(d,t);if(m!==null&&y!==null&&y>0&&v!==null&&v>0){let w=D(d),T=y;if(w==="BTC"||w==="ETH"){let R=bt(d,t);R===null||R<=0?T=null:T=y*R}T!==null&&(f.aprPnlUsdSum+=m,f.aprCapitalDays+=T*v)}let h=Le(d,t);if(h!==null){f.annualizedSum+=h,f.annualizedCount+=1;let w=tr(d,t,v);w!==null&&(f.annualizedWeightedSum+=h*w,f.annualizedWeight+=w)}let x=String(d.collateral_currency||d.currency||"").toUpperCase();x&&f.books.add(x)}return xt(o).map(d=>r.get(d)||Ie(d))}function or(t){let e=rt(t.id),n=t.closedCount>0?t.wins/t.closedCount:null,o=er(t),r=t.holdingCount>0?t.holdingSum/t.holdingCount:null,s=Array.from(t.books).sort().join(" / ")||"\u2014";return`
    <div class="rounded-2xl border ${e.accentClass} bg-slate-900/60 p-4 shadow">
      <div class="flex items-start justify-between gap-3 mb-2">
        <div>
          <h3 class="text-sm font-semibold tracking-wide text-slate-100">${C(e.title)}</h3>
          <p class="text-xs text-slate-500 mt-1">${C(e.description)}</p>
        </div>
        ${K(t.id)}
      </div>
      <div class="stat-grid mt-4">
        <div class="stat-tile">
          <div class="label">${u("Open groups","\u6301\u5009\u7B46\u6578")}</div>
          <div class="value">${t.openCount}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Realized APR","\u5DF2\u5BE6\u73FE\u5E74\u5316\uFF08\u52A0\u6B0A\uFF09")}</div>
          <div class="value">${k(o,1)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Unrealized P&amp;L","\u672A\u5BE6\u73FE\u640D\u76CA")}</div>
          <div class="value ${E(t.unrealizedUsd)}">${g(t.unrealizedUsd)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Realized P&amp;L","\u5DF2\u5BE6\u73FE\u640D\u76CA")}</div>
          <div class="value ${E(t.realizedPnl)}">${g(t.realizedPnl)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Win rate","\u52DD\u7387")}</div>
          <div class="value">${k(n,1)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Avg holding","\u5E73\u5747\u6301\u6709")}</div>
          <div class="value">${r===null?"\u2014":S(r,2)+(L?" \u5929":"d")}</div>
        </div>
      </div>
      <div class="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <span>${t.closedCount} ${u("closed \xB7 books","\u7B46\u5DF2\u5E73 \xB7 \u5E33\u672C")} ${C(s)}</span>
        <span>${u("weighted annualized","\u52A0\u6B0A\u5E74\u5316")} ${k(o,1)}</span>
      </div>
    </div>
  `}function zn(t){let e=J(t);return e==="covered_call"?"open-position-call":e==="bull_put_spread"?"open-position-spread":"open-position-put"}function qn(t){let e=a(t);return e===null||Math.abs(e)<.005?"open-position-flat":e>0?"open-position-profit":"open-position-loss"}function Vn(t){let e=a(t);return e===null||Math.abs(e)<.005?u("Flat","\u6301\u5E73"):e>0?u("In profit","\u6D6E\u76C8"):u("Underwater","\u6D6E\u8667")}function Gn(t){let e=a(t),n=e===null?0:Math.max(0,Math.min(100,e*100));return`<span class="credit-capture-bar"><span class="${e===null?"bar-muted":e>=.5?"bar-ok":e>=.15?"bar-warn":"bar-bad"}" style="width:${n}%"></span></span>`}function z(t,e,n="",{secondary:o=!1}={}){return`
    <div class="open-position-metric${o?" open-position-kpi-secondary":""} ${n}">
      <span class="open-position-label">${t}</span>
      <span class="open-position-value">${e}</span>
    </div>`}function Wn(t){let e=String(t.currency||"").toUpperCase()||"Option";if(U(t)==="bull_put_spread")return L?`${e} \u8CE3\u6B0A\u50F9\u5DEE`:`${e} put spread`;let o=Q(t);if(L){let r=o.toLowerCase()==="call"?"\u8CB7\u6B0A":"\u8CE3\u6B0A";return`${e} \u8CE3\u51FA${r}`}return`${e} short ${o.toLowerCase()}`}function Nn(t,e,n,o){let r=o==="short",s=Q(t),i=r?L?`\u8CE3\u51FA${s==="Call"?"\u8CB7\u6B0A":"\u8CE3\u6B0A"}`:`Short ${s}`:u("Long protection","\u4FDD\u8B77\u8CB7\u817F"),c=j(t,o),d=tt(t,e,o),p=Z(t,o),f=et(t,e,o,"average_price"),m=et(t,e,o,"mark_price"),v=it(e,t,n,o),y=F(t)||t.collateral_currency||"";return`
    <div class="open-position-leg ${r?"leg-short":"leg-long"}">
      <div class="open-position-leg-head">
        <span class="chip ${r?"chip-warn":"chip-ok"}">${i}</span>
        <span class="open-position-leg-amount">${d===null?"\u2014":S(d,4)}</span>
      </div>
      <div class="open-position-leg-instrument">${C(c||"\u2014")}</div>
      <div class="open-position-leg-metrics">
        ${z(u("Strike","\u5C65\u7D04\u50F9"),ct(p))}
        ${z(u("Entry","\u9032\u5834\u50F9"),lt(f,y))}
        ${z(u("Mark","\u6A19\u8A18\u50F9"),lt(m,y))}
        ${z(u("Leg PNL","\u55AE\u817F\u640D\u76CA"),v===null?"\u2014":g(v),E(v))}
      </div>
    </div>`}function rr(t,e,n){let o=U(t),r=F(t)||t.collateral_currency||"";if(o==="bull_put_spread"){let s=Ee(t),i=Vt(t,e,"average_price"),c=Vt(t,e,"mark_price");return`
      <span>${u("Width","\u50F9\u5DEE\u5BEC\u5EA6")} ${ct(s)}</span>
      <span>${u("Entry gap","\u9032\u5834\u50F9\u5DEE")} ${lt(i,r)}</span>
      <span>${u("Mark gap","\u5E02\u50F9\u50F9\u5DEE")} ${lt(c,r)}</span>`}return`
    <span>${u("Strike","\u5C65\u7D04\u50F9")} ${ct(Z(t,"short"))}</span>
    <span>${C(Te(t))}</span>`}function sr(t,e,n){let o=U(t),r=o==="bull_put_spread",s=ge(t),i=ht(t,e,n),c=be(t,e,n),d=F(t)||t.collateral_currency||"",p=a(t.profit_capture),f=ot(t,e,n),m=Xt(t,e),v=j(t,"long"),y=tt(t,e,"short"),h=tt(t,e,"long"),x=I=>I===null?"":` \xB7 ${S(I,4)}`,w=zn(o),T=qn(i),R=Vn(i),B=f===null?"\u2014":g(f),b=m===null?"":`<span class="inv-pos-metric-sub font-mono">${Ot(m,d)}</span>`,$=Zt(t,e),O=$===null?"\u2014":k($,1),M="";if(r){let I=Ee(t),G=Vt(t,e,"average_price");M=`
      <span class="inv-pos-tag">${u("Width","\u50F9\u5DEE")} ${ct(I)}</span>
      <span class="inv-pos-tag">${u("Entry gap","\u9032\u5834")} ${lt(G,d)}</span>`}else M=`
      <span class="inv-pos-tag">${u("Strike","\u5C65\u7D04")} ${ct(Z(t,"short"))}</span>
      <span class="inv-pos-tag">${C(Te(t))}</span>`;return`
    <article class="inv-position ${w} ${T}">
      <header class="inv-position-head">
        <div class="inv-position-main">
          <div class="inv-position-titleline">
            ${K(o,{compact:!0})}
            <h3 class="inv-position-name">${C(Wn(t))}</h3>
          </div>
          <p class="inv-position-contract font-mono">${C(t.short_instrument_name||"\u2014")}<span class="inv-position-size tabular-nums">${x(y)}</span></p>
          ${r&&v?`<p class="inv-position-contract font-mono inv-position-contract--long">${u("Long","\u8CB7\u817F")} ${C(v)}<span class="inv-position-size tabular-nums">${x(h)}</span></p>`:""}
          <div class="inv-position-tags">
            <span class="inv-pos-tag">${C(d)}</span>
            <span class="inv-pos-tag inv-pos-tag--status">${C(R)}</span>
            ${M}
          </div>
        </div>
        <div class="inv-position-pnl">
          <span class="inv-position-pnl-label">${u("Unrealized","\u672A\u5BE6\u73FE")}</span>
          <span class="inv-position-pnl-value font-mono tabular-nums ${E(i)}">${i===null?"\u2014":g(i)}</span>
          <span class="inv-position-pnl-native font-mono tabular-nums ${E(c)}">${Ot(c,d)}</span>
        </div>
      </header>
      <div class="inv-position-strip" role="list">
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("DTE","\u5230\u671F")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${s!==null?`${S(s,1)}${L?"\u5929":"d"}`:"\u2014"}</span>
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("Credit kept","\u6B0A\u5229\u91D1")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${k(p,1)}</span>
          ${Gn(p)}
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("Entry","\u9032\u5834")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${B}</span>
          ${b}
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("Entry APR","\u9032\u5834\u5E74\u5316")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums ${$!==null&&$>=.15?"pnl-pos":""}">${O}</span>
        </div>
      </div>
    </article>`}function ir(t,e,n){let o=U(t),r=o==="bull_put_spread",s=ge(t),i=ht(t,e,n),c=be(t,e,n),d=F(t)||t.collateral_currency||"",p=a(t.profit_capture),f=ot(t,e,n),m=Xt(t,e),v=Jt(t),y=Ae(t,e),h=Kt(t),x=De(t,e),w=j(t,"long"),T=!_&&ft(t)?ft(t):"",R=zn(o),B=qn(i),b=_?u(`${d} book`,`${d} \u5E33\u672C`):`${d} book`;return`
    <article class="open-position-card ${R} ${B}">
      <div class="open-position-glow"></div>
      <div class="open-position-header">
        <div class="open-position-main">
          <div class="open-position-title-row">
            ${K(o)}
            <h3>${C(Wn(t))}</h3>
            <span class="open-book-pill">${C(b)}</span>
            <span class="open-status-pill">${Vn(i)}</span>
          </div>
          <div class="open-position-instruments">
            <span>${C(t.short_instrument_name||"\u2014")}</span>
            ${r&&w?`<span>${u("Long","\u8CB7\u5165\u4FDD\u8B77")} ${C(w)}</span>`:""}
          </div>
          <div class="open-position-detail-row">
            ${rr(t,e,n)}
            ${T?`<span>${C(T)}</span>`:""}
          </div>
        </div>
        <div class="open-position-pnl-panel">
          <span class="open-position-label"${r?` title="${u("Sum of leg mark MTM when both legs load; otherwise engine entry\u2212debit (bid/ask close est.).","\u5169\u817F\u7686\u8F09\u5165\u6642\u70BA\u6A19\u8A18\u640D\u76CA\u52A0\u7E3D\uFF1B\u5426\u5247\u70BA\u5F15\u64CE\u9032\u5834\u6536\u6582\u8207\u73FE\u4F30\u5E73\u5009\u5DEE\u984D\u3002")}"`:""}>${u("Unrealized PNL","\u672A\u5BE6\u73FE\u640D\u76CA")}</span>
          <strong class="${E(i)}">${i===null?"\u2014":g(i)}</strong>
          <span class="open-position-native ${E(c)}">${Ot(c,d)}</span>
        </div>
      </div>
      <div class="open-position-kpis open-position-kpis-extended">
        ${z(u("DTE","\u8DDD\u5230\u671F\u5929\u6578"),s!==null?`${S(s,2)}${L?" \u5929":"d"}`:"\u2014")}
        ${z(u("Credit kept","\u5DF2\u6536\u6B0A\u5229\u91D1\u6BD4\u4F8B"),`${k(p,1)}${Gn(p)}`)}
        ${z(u("Entry credit","\u9032\u5834\u6536\u6582"),f===null?"\u2014":Yt(f,m,d))}
        ${(()=>{let $=Zt(t,e),O=$!==null&&$>=.15?"pnl-pos":"";return z(u("Entry net APR","\u9032\u5834\u6DE8\u5E74\u5316"),$===null?"\u2014":k($,1),O)})()}
        ${z(u("Entry fee","\u9032\u5834\u624B\u7E8C\u8CBB"),v===null?"\u2014":Yt(v,y,d))}
        ${z(u("Est. close fee","\u9810\u4F30\u5E73\u5009\u8CBB"),h===null?"\u2014":Yt(h,x,d))}
      </div>
      <div class="open-position-legs ${r?"has-two-legs":"has-one-leg"}">
        ${Nn(t,e,n,"short")}
        ${r?Nn(t,e,n,"long"):""}
      </div>
    </article>`}function ar(t,e,n){let o=ir(t,e,n);return _?`<div class="investor-view-desktop">${o}</div><div class="investor-view-mobile">${sr(t,e,n)}</div>`:o}function lr(t,e,n,o){let r=J(t)||t,s=rt(r),i=e.map(c=>ar(c,n,o)).join("");return`
    <div class="rounded-2xl border ${s.accentClass} bg-slate-900/60 shadow overflow-hidden">
      <div class="flex flex-wrap items-baseline justify-between gap-3 px-4 py-3 border-b border-slate-800 bg-slate-950/40">
        <div class="flex flex-wrap items-center gap-2 min-w-0">
          <h3 class="text-sm font-semibold text-slate-200">${C(s.title)}</h3>
          ${K(r)}
        </div>
        <span class="text-xs text-slate-500">${e.length} ${u("open","\u7B46\u6301\u5009")}</span>
      </div>
      <div class="p-4">
        <div class="open-position-list">
          ${i}
        </div>
      </div>
    </div>`}function ae(t,e,n){let o=document.getElementById("strategy-cards"),r=document.getElementById("strategy-open-groups");if(!o&&!r)return;let s=nr(t,e,n),i=_t(t,n),c=i.length,d=ke(e,n).length,p=s.filter(v=>v.openCount||v.closedCount).length;if(N("strategy-meta",_?u(`${c} open \xB7 ${d} closed \xB7 ${p||0} active strategy groups`,`${c} \u7B46\u6301\u5009 \xB7 ${d} \u7B46\u5DF2\u5E73 \xB7 ${p||0} \u985E\u7B56\u7565`):`${c} open \xB7 ${d} closed \xB7 ${p||0} active strategy groups`),o&&(o.innerHTML=s.map(or).join("")),!r)return;if(!i.length){r.innerHTML=`
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-sm text-slate-400">
        ${u("No open strategy positions.","\u76EE\u524D\u6C92\u6709\u958B\u5009\u4E2D\u7684\u7B56\u7565\u90E8\u4F4D\u3002")}
      </div>`;return}let f=new Map,m=new Set(q.map(v=>v.id));for(let v of i){let y=U(v);V[y]&&(f.has(y)||f.set(y,[]),f.get(y).push(v))}r.innerHTML=xt(m).filter(v=>f.has(v)).map(v=>lr(v,f.get(v),t,n)).join("")}function In(t,e,n,o,r){if(!t)return;if(!e.length){t.innerHTML=`<li class="activity-empty">${C(r)}</li>`;return}let s=[];for(let i of e)try{s.push(xn(i,n,o))}catch(c){console.warn("activity card skipped",i?.group_id,c)}t.innerHTML=s.length?s.join(""):`<li class="activity-empty">${C(r)}</li>`}function Bt(t,e,n){let o=document.getElementById("activity-open-list"),r=document.getElementById("activity-closed-list");if(!o&&!r)return;let s=hn(t,n),i=_n(t,e,n),c=Ue(s,l.activityOpenPage,dt),d=Ue(i,l.activityClosedPage,dt);l.activityOpenPage=c.page,l.activityClosedPage=d.page,N("activity-meta",u(`${s.length} open \xB7 ${i.length} closed`,`${s.length} \u6301\u5009\u4E2D \xB7 ${i.length} \u5DF2\u5E73\u5009`)),In(o,c.rows,t,n,u("No open positions","\u5C1A\u7121\u6301\u5009")),In(r,d.rows,t,n,u("No closed trades","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D00\u9304"));let p=document.getElementById("activity-open-pagination"),f=document.getElementById("activity-closed-pagination");p&&(p.innerHTML=Pe("open",c),p.hidden=!p.innerHTML),f&&(f.innerHTML=Pe("closed",d),f.hidden=!f.innerHTML)}function cr(t){let e=Array.isArray(t?.strategy_stresses)?t.strategy_stresses.filter(Boolean):[];return e.length?e:[t]}function ur(t,e){let n=t.equity_usdc_by_book||{},o=t.strategy_analysis||{},r=J(t.option_strategy||o.label||"naked_short"),s=Object.values(n).reduce((m,v)=>m+(a(v)||0),0),i=(t.accounts||[]).map(m=>m?.name).filter(Boolean).join(", "),c=Array.isArray(o.actions)?o.actions:[],d=Y.map(m=>`
        <div class="rounded-xl bg-slate-800/40 px-3 py-2">
          <div class="text-[11px] text-slate-400 uppercase tracking-wide">${m} book</div>
          <div class="font-mono text-sm">${g(n[m])}</div>
        </div>`).join(""),p=(t.scenarios||[]).map(m=>{let v=a(m.loss_usdc_total),y=a(m.loss_usdc_pct_of_total_equity),h=m.loss_by_book_usdc||{};return`
        <tr>
          <td class="px-3 py-2 font-mono">${k(a(m.shock),0)}</td>
          <td class="px-3 py-2 font-mono">${k(a(m.slippage),0)}</td>
          <td class="px-3 py-2 text-right font-mono ${E(v)}">${g(v)}</td>
          <td class="px-3 py-2 text-right font-mono">${k(y,2)}</td>
          <td class="px-3 py-2 text-right font-mono ${E(a(h.BTC))}">${g(h.BTC)}</td>
          <td class="px-3 py-2 text-right font-mono ${E(a(h.ETH))}">${g(h.ETH)}</td>
          <td class="px-3 py-2 text-right font-mono ${E(a(h.USDC))}">${g(h.USDC)}</td>
        </tr>`}).join(""),f=c.length?`<ul class="mt-2 list-disc list-inside text-xs text-slate-500 space-y-1">
        ${c.map(m=>`<li>${C(m)}</li>`).join("")}
      </ul>`:"";return`
    <div class="${e>1?"rounded-2xl border border-slate-800 bg-slate-900/40 p-4":""}">
      <div class="rounded-xl bg-slate-800/40 px-3 py-3 mb-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div class="text-[11px] text-slate-400 uppercase tracking-wide">Strategy black swan read</div>
            <div class="mt-1 flex items-center gap-2 text-sm text-slate-200">
              <span>${C(Ht(r))}</span>
              ${K(r)}
            </div>
          </div>
          <div class="text-[11px] text-slate-500">
            ${C(i||`${t.scenarios?.length||0} scenarios \xB7 ${t.positions?.length||0} legs`)}
          </div>
        </div>
        <p class="mt-2 text-xs text-slate-400">${C(o.summary||"")}</p>
        <p class="mt-1 text-xs text-slate-500">${C(o.focus||"")}</p>
        ${f}
      </div>
      <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
        ${d}
        <div class="rounded-xl bg-slate-800/40 px-3 py-2">
          <div class="text-[11px] text-slate-400 uppercase tracking-wide">Total equity (USDC)</div>
          <div class="font-mono text-sm">${g(s)}</div>
        </div>
      </div>
      <div class="overflow-x-auto rounded-xl border border-slate-800">
        <table class="w-full text-sm">
          <thead class="bg-slate-900/80 text-slate-400">
            <tr>
              <th class="text-left px-3 py-2">Spot shock</th>
              <th class="text-left px-3 py-2">Slippage</th>
              <th class="text-right px-3 py-2">Total loss</th>
              <th class="text-right px-3 py-2">% of equity</th>
              <th class="text-right px-3 py-2">BTC book</th>
              <th class="text-right px-3 py-2">ETH book</th>
              <th class="text-right px-3 py-2">USDC book</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-800">
            ${p||'<tr><td colspan="7" class="px-3 py-4 text-center text-slate-500">No stress scenarios.</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>
  `}function Yn(t){if(_)return;let e=document.getElementById("stress-card");if(!e)return;if(!t){e.innerHTML='<p class="text-sm text-slate-400">Set DERIBIT_CLIENT_ID and DERIBIT_CLIENT_SECRET to load live stress data.</p>',N("stress-meta","\u2014");return}let n=cr(t),o=n.reduce((s,i)=>s+(i.scenarios?.length||0),0),r=n.reduce((s,i)=>s+(i.positions?.length||0),0);N("stress-meta",`${n.length} strategy view${n.length===1?"":"s"} \xB7 ${o} scenarios \xB7 ${r} legs`),e.innerHTML=`
    <div class="space-y-4">
      ${n.map(s=>ur(s,n.length)).join("")}
    </div>
    <p class="text-xs text-slate-500 mt-3">
      Per-book loss is capped at that book's equity (liquidation-style floor). Spot shock is a negative index move.
      For bull put spread, long option legs are netted when present; for covered call, BTC/ETH spot cover drawdown is included.
    </p>
  `}function Oe(){let t=document.getElementById("header-spot-btc"),e=document.getElementById("header-spot-eth"),n=l.lastSpotUsd.BTC,o=l.lastSpotUsd.ETH;t&&(t.textContent=n!==null&&n>0?`BTC ${H.usd2.format(n)}`:"BTC \u2014"),e&&(e.textContent=o!==null&&o>0?`ETH ${H.usd2.format(o)}`:"ETH \u2014")}function ce(){let t=[["risk-capital",Rt],["cum-pnl",oe],["daily-pnl",re],["apr",se]];for(let[e,n]of t)try{n()}catch(o){console.error(`${e} chart render failed`,o)}Et()}var pr={spot:{en:"Fetching BTC / ETH market prices\u2026",zh:"\u6B63\u5728\u53D6\u5F97 BTC / ETH \u5373\u6642\u5831\u50F9\u2026"},snapshot:{en:"Loading last equity snapshot\u2026",zh:"\u6B63\u5728\u8B80\u53D6\u6700\u8FD1\u6B0A\u76CA\u5FEB\u7167\u2026"},health:{en:"Checking account connection\u2026",zh:"\u6B63\u5728\u78BA\u8A8D\u5E33\u6236\u9023\u7DDA\u2026"},groups:{en:"Loading open positions and spreads\u2026",zh:"\u6B63\u5728\u8B80\u53D6\u6301\u5009\u8207\u50F9\u5DEE\u90E8\u4F4D\u2026"},cumulative:{en:"Loading realized P&L history\u2026",zh:"\u6B63\u5728\u8F09\u5165\u5DF2\u5BE6\u73FE\u640D\u76CA\u6B77\u53F2\u2026"},apr:{en:"Calculating rolling performance (APR)\u2026",zh:"\u6B63\u5728\u8A08\u7B97\u6EFE\u52D5\u5E74\u5316\u5831\u916C\u2026"},status:{en:"Syncing live equity and margin\u2026",zh:"\u6B63\u5728\u540C\u6B65\u5373\u6642\u6B0A\u76CA\u8207\u4FDD\u8B49\u91D1\u2026"},summary:{en:"Loading performance summary from local records\u2026",zh:"\u6B63\u5728\u5F9E\u672C\u5730\u7D00\u9304\u8F09\u5165\u7E3E\u6548\u6458\u8981\u2026"},render:{en:"Preparing your dashboard\u2026",zh:"\u6B63\u5728\u6574\u7406\u5100\u8868\u677F\u986F\u793A\u2026"},done:{en:"Done",zh:"\u5B8C\u6210"}};function fr(t){let e=pr[t];return e?u(e.en,e.zh):""}function mr(t,{includeCharts:e=!0}={}){let n=3+(t?2:0)+1;return e&&(n+=2),n}function Me(t,e){let n=Math.min(100,Math.max(0,Math.round(t*100))),o=document.getElementById("investor-load-bar-fill");o&&(o.style.width=`${n}%`);let r=document.querySelector("[data-investor-load-pct]");r&&(r.textContent=`${n}%`);let s=document.querySelector("[data-investor-load-step]");s&&e&&(s.textContent=fr(e))}function Zn(){if(!_)return;let t=(e,n,o)=>{let r=document.querySelector(`[data-investor-load-${e}]`);r&&(r.textContent=u(n,o))};t("eyebrow","Please wait","\u8ACB\u7A0D\u5019"),t("title","Loading your portfolio","\u6B63\u5728\u8F09\u5165\u60A8\u7684\u6295\u8CC7\u7D44\u5408"),t("hint","Showing snapshot first; live positions and P&L sync in the background.","\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\uFF1B\u6301\u5009\u8207\u640D\u76CA\u65BC\u80CC\u666F\u540C\u6B65\u4E2D\u3002")}function Jn({blocking:t=!0}={}){if(!_)return;l.investorLoadDone=0,l.investorLoadTotal=mr(!1),document.body.classList.toggle("investor-blocking-load",t);let e=document.getElementById("investor-load-overlay");e&&(e.classList.remove("hidden"),e.classList.toggle("investor-load-overlay--refresh",!t),e.setAttribute("aria-busy","true"));let n=document.getElementById("refresh-now");n&&(n.disabled=!0),Me(0,"spot")}function le(t){if(!_)return;l.investorLoadDone=Math.min(l.investorLoadTotal||1,l.investorLoadDone+1);let e=l.investorLoadTotal>0?l.investorLoadDone/l.investorLoadTotal:0;Me(e,t)}function vr(t){if(!_)return;if(!t){Jn({blocking:!l.investorReady});return}Me(1,"done"),l.investorReady=!0,document.body.classList.remove("investor-blocking-load"),document.body.classList.add("investor-ready");let e=document.getElementById("investor-load-overlay");e&&(e.classList.add("hidden"),e.classList.remove("investor-load-overlay--refresh"),e.setAttribute("aria-busy","false"));let n=document.getElementById("refresh-now");n&&(n.disabled=!1),Et()}async function yr({renderDependentViews:t=!0,updateDom:e=!0}={}){try{let n=await P("/api/spot");l.lastSpotUsd.BTC=a(n.BTC),l.lastSpotUsd.ETH=a(n.ETH),e&&(Oe(),t&&(ae(l.status,l.report,l.groups),Bt(l.status,l.report,l.groups)))}catch{}}function hr(){return!!document.getElementById("charts-section")?.open}async function jn(){try{let t=await P("/api/portfolio/snapshot");l.portfolioSnapshot=t,t?.source==="ledger"&&(l.dataFreshness.source="snapshot",l.dataFreshness.snapshotMs=a(t.freshness_ms),l.dataFreshness.live=!1)}catch{}}async function _r(){let t=fe,e=!1,n=at(t).then(()=>{throw e=!0,new Error("status timeout")});try{let o=await Promise.race([P("/api/status"),n]);return l.status=o,l.statusErrorOnce=!1,l.dataFreshness.source="live",l.dataFreshness.live=!0,l.dataFreshness.statusMs=0,o}catch(o){return e&&l.portfolioSnapshot?.portfolio?(l.statusErrorOnce||(A(u("Live sync is slow; showing last snapshot.","\u5373\u6642\u540C\u6B65\u8F03\u6162\uFF0C\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\u3002")),l.statusErrorOnce=!0),P("/api/status").then(r=>{l.status=r,l.dataFreshness.source="live",l.dataFreshness.live=!0,renderDashboardFn?.()}).catch(()=>{}),null):(l.status=null,l.statusErrorOnce||(A(`status: ${o.message}`),l.statusErrorOnce=!0),null)}}async function xr({backgroundOnTimeout:t=!1}={}){let e=fe,n=!1,o=P(Ce(30)),r=_?Promise.race([o,at(e).then(()=>{throw n=!0,new Error("dashboard bundle timeout")})]):o;try{return we(await r),!0}catch(s){return _&&n&&l.portfolioSnapshot?.portfolio?(l.statusErrorOnce||(A(u("Live sync is slow; showing last snapshot.","\u5373\u6642\u540C\u6B65\u8F03\u6162\uFF0C\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\u3002")),l.statusErrorOnce=!0),t&&P(Ce(30)).then(i=>{we(i),renderDashboardFn?.()}).catch(()=>{}),!1):((!_||!n)&&A(`dashboard bundle: ${s.message}`),!1)}}async function He({force:t=!1,investorFetchWrap:e=null}={}){if(!t&&l.chartsDataLoaded){ce();return}if(!l.chartsLoadInFlight){l.chartsLoadInFlight=!0;try{let n=()=>P("/api/cumulative_pnl_series").then(r=>{l.cumulativePnl=r}).catch(r=>A(`cumulative pnl: ${r.message}`)),o=()=>P(ne()).then(r=>{l.aprSeries=r}).catch(r=>A(`apr series: ${r.message}`));e?await Promise.all([e("cumulative",n),e("apr",o)]):await Promise.all([n(),o()]),l.chartsDataLoaded=!0,ce()}finally{l.chartsLoadInFlight=!1}}}function gr(){return l.lastRefreshStartedMs?Math.max(0,Dt-(Date.now()-l.lastRefreshStartedMs)):0}async function ue({force:t=!1,silentIfLimited:e=!1,renderDashboard:n}={}){if(l.refreshInFlight){e||A(u("refresh already running","\u5DF2\u6709\u66F4\u65B0\u6B63\u5728\u9032\u884C"));return}let o=gr();if(!t&&o>0){e||A(u(`refresh rate limited; wait ${Math.ceil(o/1e3)}s`,`\u8ACB\u7A0D\u5019 ${Math.ceil(o/1e3)} \u79D2\u5F8C\u518D\u8A66`));return}l.refreshInFlight=!0,l.lastRefreshStartedMs=Date.now();let r=_&&!l.investorReady;r?Jn({blocking:!0}):_&&vt(!0,{indeterminate:!0});try{let i=function(){s||(s=!0,requestAnimationFrame(()=>{s=!1,n?.()}))},c=function(b,$){return r?$().finally(()=>le(b)):$()},v=function(){return P("/api/groups").then(b=>{l.groups=b,i()}).catch(b=>{A(`groups: ${b.message}`)})},y=function(){return P(un(30)).then(b=>{l.report=b,i()}).catch(b=>A(`realized summary: ${b.message}`))},h=function(){return P("/api/status").then(b=>{l.status=b,l.statusErrorOnce=!1,i()}).catch(b=>{l.status=null,l.statusErrorOnce||(A(`status: ${b.message}`),l.statusErrorOnce=!0)})},x=function(){return P("/api/stress?shocks=0.1,0.2,0.3,0.4,0.5").then(b=>{l.stress=b,i()}).catch(b=>A(`stress: ${b.message}`))},s=!1;try{let b=c("spot",()=>yr({renderDependentViews:!_,updateDom:!0})),$=c("health",()=>P("/api/health").then(O=>{l.health=O}));await Promise.all([b,$]),ie(l.health)}catch(b){A(`health failed: ${b.message}`)}let d=!!l.health?.has_private_creds,p=!1;if(_&&r){try{await Promise.race([c("snapshot",jn),at(Ve)])}catch{}p=!0,vr(!0),vt(!0,{indeterminate:!0}),i()}let f=r?(b,$)=>c(b,$):null,m=(b,$)=>f?f(b,$):$();async function w(){await m("groups",v),_?(await m("status",()=>_r().then(()=>i())),await m("summary",y)):(await h(),await y())}async function T(){if(!d){await m("groups",v),l.status=null,l.report=null,l.stress=null;return}if(qe&&await xr({backgroundOnTimeout:_})){r&&(le("groups"),le("status"),le("summary")),i();return}await w()}let R=[()=>T()];d&&!_&&R.push(()=>x()),_&&!p&&R.push(()=>m("snapshot",jn));let B=!_||hr();B&&R.push(()=>He({force:!_,investorFetchWrap:f})),await gn(R,ze),_&&(l.stress=null),B||Rt(),(!_||l.investorReady)&&n?.(),vt(!1),N("last-refresh",`${u("last refresh:","\u4E0A\u6B21\u66F4\u65B0\uFF1A")} ${luxon.DateTime.now().toFormat("HH:mm:ss")}`)}finally{l.refreshInFlight=!1,vt(!1)}}function de(){nn(l.status,l.groups),On(l.status),ie(l.health),Oe(),Hn(l.health,l.status),Mn(l.status),Fn(l.status,l.report),ae(l.status,l.report,l.groups),ce(),Bt(l.status,l.report,l.groups),Yn(l.stress)}function $r(t){l.bookFilter=t;let e=document.querySelector("#book-filter");e&&e.querySelectorAll("button[data-book]").forEach(n=>{n.classList.toggle("filter-active",n.dataset.book===t)}),Rt(),oe(),re()}function Sr(){let t=document.getElementById("auto-refresh");if(!t)return;function e(){l.autoRefreshHandle&&(clearInterval(l.autoRefreshHandle),l.autoRefreshHandle=null),t.checked&&(l.autoRefreshHandle=setInterval(()=>ue({silentIfLimited:!0,renderDashboard:de}),Dt))}t.addEventListener("change",e),e()}function Cr(){document.getElementById("refresh-now")?.addEventListener("click",()=>ue({renderDashboard:de})),document.getElementById("book-filter")?.addEventListener("click",t=>{let e=t.target.closest("button[data-book]");e&&$r(e.dataset.book)}),document.getElementById("activity-section")?.addEventListener("click",t=>{let e=t.target.closest("button.activity-page-btn");if(!e||e.disabled)return;let n=e.dataset.activitySection,o=e.dataset.direction==="next"?1:-1;n==="open"?l.activityOpenPage+=o:n==="closed"&&(l.activityClosedPage+=o),Bt(l.status,l.report,l.groups)}),document.getElementById("apr-window")?.addEventListener("change",async t=>{l.aprWindow=parseInt(t.target.value,10)||30;try{l.aprSeries=await P(ne())}catch(e){A(`apr series: ${e.message}`)}se()})}function wr(){document.querySelectorAll("details.collapsible-section").forEach(t=>{t.addEventListener("toggle",()=>{t.open&&(Et(),_&&t.id==="charts-section"&&He({renderDashboard:de}))})})}function Kn(){let t=()=>{Zn(),kn(),Cr(),wr(),Sr(),ue({force:!0,renderDashboard:de})};document.readyState==="loading"?document.addEventListener("DOMContentLoaded",t):t()}Kn();})();
