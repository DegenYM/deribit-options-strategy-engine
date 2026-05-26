(()=>{var co=typeof window<"u"&&window.__DASHBOARD_MODE__==="investor"?"investor":"ops",x=co==="investor",uo=(()=>{if(!x)return"en";let t=String(typeof window<"u"&&window.__INVESTOR_LOCALE__||"en").trim().toLowerCase();return t==="zh-hant"||t==="zh_tw"||t==="zh-tw"||t==="zh-hk"||t==="zh"?"zh":"en"})(),L=x&&uo==="zh";function u(t,e){return x&&L?e:t}function po(){try{return document.querySelector('meta[name="dashboard-api-base"]')?.getAttribute("content")?.trim()||""}catch{return""}}function We(t){if(/^https?:\/\//i.test(t))return t;let n=((typeof window<"u"&&window.__API_BASE__?String(window.__API_BASE__).trim():"")||po()).replace(/\/$/,""),o=t.startsWith("/")?t:`/${t}`;return n?`${n}${o}`:o}var z={usd0:new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:0}),usd2:new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:2}),num4:new Intl.NumberFormat("en-US",{maximumFractionDigits:4}),num8:new Intl.NumberFormat("en-US",{maximumFractionDigits:8}),pct2:new Intl.NumberFormat("en-US",{style:"percent",maximumFractionDigits:2,minimumFractionDigits:2}),pct1:new Intl.NumberFormat("en-US",{style:"percent",maximumFractionDigits:1,minimumFractionDigits:1})},it={BTC:"#fb923c",ETH:"#818cf8",USDC:"#38bdf8",TOTAL:"#a3e635"},F=["BTC","ETH","USDC"],At=18e4,je=x?6:3,Ye=!0,ye=45e3,Ze=3e3,Je=new Set([502,503,504]),Ke=2,he=450,pt=10,H=[{id:"covered_call",title:"Covered Call",titleZh:"\u5099\u514C\u8CB7\u6B0A",short:"Covered Call",shortZh:"\u5099\u514C",chipShort:"CC",chipShortZh:"\u5099\u514C",accentClass:"strategy-card-call",description:"Short call backed by existing BTC/ETH spot collateral.",descriptionZh:"\u5728\u6301\u6709\u73FE\u8CA8\u64D4\u4FDD\u4E0B\u8CE3\u51FA\u8CB7\u6B0A\uFF0C\u4EE5\u6B0A\u5229\u91D1\u589E\u5F37\u6536\u76CA\u3002"},{id:"naked_short",title:"Naked Short",titleZh:"\u55AE\u8CE3\u9078\u64C7\u6B0A\uFF08\u88F8\u8CE3\uFF09",short:"Naked Short",shortZh:"\u88F8\u8CE3",chipShort:"Naked",chipShortZh:"\u88F8\u8CE3",accentClass:"strategy-card-put",description:"Single-leg short option (put / call / both) with uncapped tail risk on the chosen side.",descriptionZh:"\u55AE\u908A\u8CE3\u51FA\u8CB7\uFF0F\u8CE3\u6B0A\uFF1B\u5728\u5C0D\u61C9\u65B9\u5411\u5177\u5C3E\u90E8\u98A8\u96AA\uFF0C\u9700\u56B4\u683C\u98A8\u63A7\u3002"},{id:"bull_put_spread",title:"Bull Put Spread",titleZh:"\u725B\u52E2\u8CE3\u6B0A\u50F9\u5DEE",short:"Put Spread",shortZh:"\u8CE3\u6B0A\u50F9\u5DEE",chipShort:"Spread",chipShortZh:"\u50F9\u5DEE",accentClass:"strategy-card-spread",description:"Short put paired with a lower-strike long put protection leg.",descriptionZh:"\u8CE3\u51FA\u8F03\u9AD8\u5C65\u7D04\u50F9\u8CE3\u6B0A\uFF0C\u4E26\u8CB7\u5165\u8F03\u4F4E\u5C65\u7D04\u50F9\u8CE3\u6B0A\u4F5C\u4FDD\u8B77\u3002"}],G=Object.fromEntries(H.map(t=>[t.id,t]));var l={health:null,status:null,report:null,stress:null,groups:null,cumulativePnl:null,aprSeries:null,portfolioSnapshot:null,dataFreshness:{source:null,snapshotMs:null,statusMs:null,live:!1},chartsDataLoaded:!1,chartsLoadInFlight:!1,bookFilter:"ALL",aprWindow:30,charts:{},autoRefreshHandle:null,refreshInFlight:!1,investorReady:!1,investorLoadTotal:0,investorLoadDone:0,lastRefreshStartedMs:0,statusErrorOnce:!1,lastUnderlyingIndexUsd:{},lastSpotUsd:{BTC:null,ETH:null},activityOpenPage:1,activityClosedPage:1};function a(t){if(t==null||t==="")return null;let e=typeof t=="number"?t:Number(t);return Number.isFinite(e)?e:null}function $(t,e=2){let n=a(t);return n===null?"\u2014":e===0?z.usd0.format(n):z.usd2.format(n)}function k(t,e=2){let n=a(t);return n===null?"\u2014":e===1?z.pct1.format(n):z.pct2.format(n)}function vt(){if(l.status?.portfolio)return{portfolio:l.status.portfolio,source:"live",freshnessMs:l.dataFreshness.statusMs??0};let t=l.portfolioSnapshot?.portfolio;return t&&Object.keys(t).length>0?{portfolio:t,source:"snapshot",freshnessMs:a(l.portfolioSnapshot?.freshness_ms)}:{portfolio:null,source:null,freshnessMs:null}}function fo(t){let e=a(t);return e===null||e<0?null:Math.max(1,Math.round(e/6e4))}function mo(){let t=vt();if(t.source==="live"){let e=a(l.dataFreshness.statusMs);if(e!==null&&e<3e4)return`<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-emerald-500/40 bg-emerald-500/10 text-emerald-200">${u("Live","\u5373\u6642")}</span>`}if(t.source==="snapshot"){let e=fo(t.freshnessMs);return`<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-200">${e!==null?u(`Snapshot \xB7 ~${e}m ago`,`\u5FEB\u7167 \xB7 \u7D04 ${e} \u5206\u9418\u524D`):u("Snapshot","\u5FEB\u7167")}</span>`}return`<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-800/60 text-slate-400">${u("Loading\u2026","\u8F09\u5165\u4E2D\u2026")}</span>`}function xe(){if(!x)return;let t=document.getElementById("data-freshness-slot");t&&(t.innerHTML=mo())}function yt(t,{indeterminate:e=!1}={}){let n=document.getElementById("investor-progress-bar");n&&(n.classList.toggle("hidden",!t),n.classList.toggle("investor-progress-bar--indeterminate",t&&e))}function rn(){let e=`<div class="overview-metrics-grid">${'<div class="skeleton-block h-16 rounded-lg"></div>'.repeat(8)}</div>`;return x?`<div class="investor-view-desktop">${e}</div><div class="investor-view-mobile"><div class="inv-dashboard">
      <div class="inv-panel skeleton-block" style="height:5.5rem"></div>
      <div class="inv-panel skeleton-block" style="height:4rem"></div>
      <div class="inv-panel skeleton-block" style="height:7rem"></div>
    </div></div>`:e}function an(t){let{totalEquity:e,dayStart:n,dayPnl:o,dayDrawdown:s,openCredit:r,creditByStrategy:i,summary:c,winRate:d,avgHolding:p,sinceLine:f,lifetimePnl:m,lifetimeNativeByBook:v,closedCount:y,windowLabelDays:h,windowPnl:g,windowNativeByBook:w,lifetimeApr:T,windowApr:R,equityNativeByBook:D,equityUsdByBook:_}=t;return`
    <div class="overview-metrics-grid">
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Total equity","\u7E3D\u6B0A\u76CA")}</div>
        <div class="text-2xl font-mono">${$(e)}</div>
        <div class="text-[11px] text-slate-500">${u("USDC equivalent (all books)","USDC \u7D04\u7576\uFF08\u5168\u5E33\u672C\u5408\u8A08\uFF09")}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${_n(D,_)}</div>
          <div class="overview-metric-line">${u("day-start","\u65E5\u521D")} ${$(n)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Day P&L","\u672C\u65E5\u640D\u76CA")}</div>
        <div class="text-2xl font-mono ${E(o)}">${$(o)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${u("drawdown","\u56DE\u64A4")} ${k(s)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Open credit","\u672A\u5BE6\u73FE\u6B0A\u5229\u91D1\uFF08\u9032\u5834\u6536\u6582\uFF09")}</div>
        <div class="text-2xl font-mono">${$(r)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${Lo(i)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Win rate \xB7 avg holding","\u52DD\u7387 \xB7 \u5E73\u5747\u6301\u6709")}</div>
        <div class="text-2xl font-mono">${c?`${k(d,1)} \xB7 ${C(p,2)}${L?" \u5929":"d"}`:"\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${c?f:u("Loading performance\u2026","\u7E3E\u6548\u6458\u8981\u8F09\u5165\u4E2D\u2026")}</div>
        </div>
      </div>

      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Total profit (lifetime)","\u7D2F\u8A08\u5DF2\u5BE6\u73FE\u640D\u76CA")}</div>
        <div class="text-2xl font-mono ${E(m)}">${c?$(m):"\u2014"}</div>
        <div class="overview-metric-meta">
          ${c?`<div class="overview-metric-line">${nn(v)}</div>`:""}
          <div class="overview-metric-line">${c?`${y??0} ${u("closed groups","\u7B46\u5DF2\u5E73\u5009\u90E8\u4F4D")}`:""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${Ro(h)}</div>
        <div class="text-2xl font-mono ${E(g)}">${c?$(g):"\u2014"}</div>
        <div class="overview-metric-meta">
          ${c?`<div class="overview-metric-line">${nn(w)}</div>`:""}
          <div class="overview-metric-line">${c?yn(h):""}</div>
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
        <div class="text-xs text-slate-400">${Bo(h)}</div>
        <div class="text-2xl font-mono">${c?k(R):"\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line overview-metric-line--hint">${c?hn(h):""}</div>
        </div>
      </div>
    </div>`}function Xe(t,{pnl:e=!1,places:n={BTC:5,ETH:4,USDC:2}}={}){let o={BTC:"\u20BF",ETH:"\u25C6",USDC:"$"};return["BTC","ETH","USDC"].map(s=>{let r=a(t[s]),i=r===null?"\u2014":C(r,n[s]??4);return`<span class="inv-chip ${e?E(t[s]):""}"><span class="inv-chip-sym">${o[s]}</span><span class="inv-chip-val font-mono tabular-nums">${i}</span></span>`}).join("")}function vo(t){return gt(new Set(H.map(e=>e.id))).map(e=>{let n=b(rt(e).short),o=a(t[e]),s=o===null?"\u2014":$(o);return`<div class="inv-mini-row"><span class="inv-mini-label">${n}</span><span class="inv-mini-value font-mono tabular-nums">${s}</span></div>`}).join("")}function ln(t){let{totalEquity:e,dayStart:n,dayPnl:o,dayDrawdown:s,openCredit:r,creditByStrategy:i,summary:c,winRate:d,avgHolding:p,sinceLine:f,lifetimePnl:m,lifetimeNativeByBook:v,closedCount:y,windowLabelDays:h,windowPnl:g,windowNativeByBook:w,lifetimeApr:T,windowApr:R,equityNativeByBook:D,equityUsdByBook:_}=t,S=c!=null?`${k(d,1)} \xB7 ${C(p,2)}${L?" \u5929":"d"}`:"\u2014",N=c?f:u("Loading performance\u2026","\u7E3E\u6548\u6458\u8981\u8F09\u5165\u4E2D\u2026");return`<div class="inv-dashboard">
    <section class="inv-panel inv-panel--hero" aria-label="${u("Account snapshot","\u5E33\u6236\u5FEB\u7167")}">
      <div class="inv-split">
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Total equity","\u7E3D\u6B0A\u76CA")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${$(e)}</span>
          <span class="inv-kpi-foot">${u("USDC equivalent","USDC \u7D04\u7576")} \xB7 ${u("day-start","\u65E5\u521D")} ${$(n)}</span>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Day P&L","\u672C\u65E5\u640D\u76CA")}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${E(o)}">${$(o)}</span>
          <span class="inv-kpi-foot">${u("drawdown","\u56DE\u64A4")} ${k(s)}</span>
        </div>
      </div>
      <div class="inv-equity-dual">${_n(D,_)}</div>
    </section>

    <section class="inv-panel" aria-label="${u("Open risk","\u672A\u5E73\u5009\u98A8\u96AA")}">
      <div class="inv-split">
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Open credit","\u672A\u5BE6\u73FE\u6B0A\u5229\u91D1")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${$(r)}</span>
          <div class="inv-mini-list">${vo(i)}</div>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Win rate \xB7 hold","\u52DD\u7387 \xB7 \u6301\u6709")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${S}</span>
          <span class="inv-kpi-foot">${N}</span>
        </div>
      </div>
    </section>

    <section class="inv-panel" aria-label="${u("Realized performance","\u5DF2\u5BE6\u73FE\u7E3E\u6548")}">
      <h3 class="inv-panel-title">${u("Realized P&L","\u5DF2\u5BE6\u73FE\u640D\u76CA")}</h3>
      <div class="inv-compare">
        <div class="inv-compare-col">
          <span class="inv-compare-tag">${u("Lifetime","\u5B58\u7E8C")}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${E(m)}">${c?$(m):"\u2014"}</span>
          <div class="inv-chips-row inv-chips-row--compact">${c?Xe(v,{pnl:!0}):""}</div>
          <span class="inv-kpi-foot">${c?`${y??0} ${u("closed","\u7B46\u5E73\u5009")}`:""}</span>
        </div>
        <div class="inv-compare-col">
          <span class="inv-compare-tag">${u("Last","\u8FD1")} ${h}${L?" \u65E5":"d"}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${E(g)}">${c?$(g):"\u2014"}</span>
          <div class="inv-chips-row inv-chips-row--compact">${c?Xe(w,{pnl:!0}):""}</div>
          <span class="inv-kpi-foot">${c?yn(h):""}</span>
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
          <span class="inv-kpi-foot">${c?hn(h):""}</span>
        </div>
      </div>
    </section>
  </div>`}function ct(t,e){let n=a(t);if(n===null)return"\u2014";let o=String(e||"").toUpperCase(),s='<span class="text-slate-500">',r="</span>";return o==="USDC"?`${s}($)${r}\xA0${C(n,4)}`:o==="BTC"?`${s}\u20BF${r}\xA0${C(n,5)}`:o==="ETH"?`${s}\u2666${r}\xA0${C(n,5)}`:C(n,4)}function be(t){if(!t)return null;let e=String(t.kind||"").toLowerCase(),n=String(t.direction||"").toLowerCase()==="sell";if(e==="option"){let r=a(t.size);return r===null||r===0?null:n?-Math.abs(r):Math.abs(r)}let o=a(t.size_currency);if(o!==null&&o!==0)return n&&o>0?-Math.abs(o):o;let s=a(t.size);return s===null||s===0?null:n&&s>0?-Math.abs(s):s}function yo(t,e,n=null){let o=n??l.groups;if(ht(e,o,t,"short")>1){let d=It(t,"short");if(d!==null)return C(d,4)}let s=Q(e,t),r=be(s);if(r!==null)return C(r,4);let i=a(t.quantity);if(i===null)return"\u2014";let c=i>0?-Math.abs(i):i;return C(c,4)}function Y(t,e){return String(e==="long"?t?.long_instrument_name||"":t?.short_instrument_name||"")}function It(t,e){let n=a(t.quantity);return n===null?null:e==="short"?-Math.abs(n):Math.abs(n)}function ht(t,e,n,o){let s=Y(n,o);if(!s)return 0;let r=String(n?.account_name||""),i=new Set,c=0;for(let d of[t?.trade_groups||[],e?.open||[]])for(let p of d){if(!Ot(p))continue;let f=ft(p);i.has(f)||(i.add(f),Y(p,o)===s&&(r&&String(p?.account_name||"")!==r||c++))}return c}function ge(t,e,n){let o=Y(e,n);if(!o)return null;let s=t?.positions||[],r=String(e?.account_name||"");if(r){let i=s.find(c=>c.instrument_name===o&&String(c.account_name||"")===r);if(i)return i}return s.find(i=>i.instrument_name===o)||null}function Q(t,e){return ge(t,e,"short")}function ho(t,e,n=null){let o=n??l.groups,s=e.short_average_price,r=e.short_mark_price,i=e.short_floating_profit_loss,c=e.short_has_floating_profit_loss,d=e.short_floating_profit_loss_usd,p=e.short_has_floating_profit_loss_usd,f=s==null||s==="",m=r==null||r==="",v=i==null||i==="",y=d==null||d==="",h=ht(t,o,e,"short")>1;if((f||m||v||y||c===void 0||p===void 0)&&t?.positions?.length){let g=Q(t,e);g&&(f&&(s=g.average_price),m&&(r=g.mark_price),h||(v&&(i=g.floating_profit_loss),c===void 0&&(c=g.has_floating_profit_loss),y&&(d=g.floating_profit_loss_usd),p===void 0&&(p=g.has_floating_profit_loss_usd)))}return{...e,short_average_price:s,short_mark_price:r,short_floating_profit_loss:i,short_has_floating_profit_loss:c,short_floating_profit_loss_usd:d,short_has_floating_profit_loss_usd:p}}function cn(t){let e=t.expiration_timestamp_ms;if(e!=null&&e!==""){if(typeof e=="number"&&Number.isFinite(e))return Math.round(e);if(typeof e=="bigint")return Number(e);let n=String(e).trim();if(/^\d+$/.test(n)){let o=Number(n);return Number.isFinite(o)?o:null}}if(t.expiry){let n=luxon.DateTime.fromISO(String(t.expiry),{zone:"utc"});if(n.isValid)return n.toMillis()}return null}function $e(t){let e=a(t.dte_days)??a(t.dte);if(e!==null)return e;let n=cn(t);if(n===null)return null;let o=luxon.DateTime.fromMillis(n,{zone:"utc"});return o.isValid?o.diff(luxon.DateTime.utc(),"days").days:null}function tt(t){let e=String(t.option_type||"").toLowerCase();if(e==="call")return"Call";if(e==="put")return"Put";let n=String(t.short_instrument_name||"");return/-C$/i.test(n)||n.endsWith("-C")?"Call":"Put"}function un(t,e){for(let n of["BTC","ETH"]){let o=a(t?.underlying_index_usd?.[n]),s=a(e?.underlying_index_usd?.[n]),r=o>0?o:s>0?s:null;r!==null&&(l.lastUnderlyingIndexUsd[n]=r)}}function _o(t,e){let n={};for(let o of["BTC","ETH"]){let s=a(t?.underlying_index_usd?.[o]),r=a(e?.underlying_index_usd?.[o]),i=a(l.lastUnderlyingIndexUsd[o]),c=s>0?s:r>0?r:i>0?i:null;c!==null&&(n[o]=c)}return n}function xo(t,e,n){let o=String(n||"").toUpperCase(),s=_o(t,e);return a(s[o])}function q(t){let e=String(t.collateral_currency||"").toUpperCase();if(e==="BTC"||e==="ETH"||e==="USDC")return e;let n=String(t.short_instrument_name||"");return n.includes("_USDC-")?"USDC":n.startsWith("BTC-")?"BTC":n.startsWith("ETH-")?"ETH":String(t.currency||"").toUpperCase()||"BTC"}function dn(t){let e=q(t);return e==="BTC"||e==="ETH"?e:String(t.currency||"BTC").toUpperCase()}function bo(t,e){let n=q(e);if(n!=="BTC"&&n!=="ETH")return null;let o=n==="BTC"?"BTC-":"ETH-",s=t?.positions;if(!s?.length)return null;let r=String(e?.account_name||"");for(let i of s){if(r&&String(i.account_name||"")!==r)continue;let c=String(i.instrument_name||""),d=String(i.kind||"").toLowerCase();if(!c.startsWith(o)||d!=="option"&&d!=="future")continue;let p=a(i.index_price);if(p!==null&&p>0)return p}return null}function go(t,e,n){let o=dn(t),s=a(l.lastSpotUsd[o]);if(s!==null&&s>0)return s;let r=xo(e,n,o);if(r!==null&&r>0)return r;let i=Q(e,t),c=a(i?.index_price);if(c!==null&&c>0)return c;let d=bo(e,t);return d!==null&&d>0?d:null}function Nt(t,e,n){let o=q(t);if(o==="USDC")return 1;if(o==="BTC"||o==="ETH"){let s=go(t,e,n);return s!==null&&s>0?s:null}return null}function pn(t){return be(t)}function et(t,e,n,o=null){let s=o??l.groups,r=It(t,n);if(r!==null&&ht(e,s,t,n)>1)return r;let i=ge(e,t,n),c=be(i);return c!==null?c:r}function nt(t,e,n,o){if(n==="short"&&Ce(t,`short_${o}`)){let r=t[`short_${o}`];if(r!=null&&r!=="")return r}return ge(e,t,n)?.[o]??null}function $o(t,e,n,o=null){let s=a(nt(e,t,n,"average_price")),r=a(nt(e,t,n,"mark_price")),i=et(e,t,n,o);return s===null||r===null||i===null?null:(r-s)*i}function at(t,e,n,o){let s=$o(t,e,o,n);if(s===null)return null;let r=Nt(e,t,n);return r===null||r<=0?null:s*r}function So(t,e,n=null){let o=n??l.groups;if(ht(t,o,e,"short")>1){let d=Q(t,e);if(!d)return null;let p=a(d.average_price),f=a(d.mark_price),m=It(e,"short");return p===null||f===null||m===null?null:(f-p)*m}let s=Q(t,e);if(!s)return null;let r=a(s.average_price),i=a(s.mark_price),c=pn(s);return r===null||i===null||c===null?null:(i-r)*c}function Co(t,e){let n=So(e,t);if(n!==null)return n;if(t.short_has_floating_profit_loss){let o=a(t.short_floating_profit_loss);if(o!==null)return o}return null}function wo(t,e,n){let o=n??l.groups;if(ht(t,o,e,"short")>1){let p=Q(t,e);if(!p)return null;let f=a(p.average_price),m=a(p.mark_price),v=It(e,"short");if(f===null||m===null||v===null)return null;let y=Nt(e,t,n);return y===null||y<=0?null:(m-f)*v*y}let s=Q(t,e);if(!s)return null;let r=a(s.average_price),i=a(s.mark_price),c=pn(s);if(r===null||i===null||c===null)return null;let d=Nt(e,t,n);return d===null||d<=0?null:(i-r)*c*d}function Qe(t,e,n){let o=wo(e,t,n);if(o!==null)return o;if(t.short_has_floating_profit_loss_usd){let s=a(t.short_floating_profit_loss_usd);if(s!==null)return s}return null}function ko(t){let e=a(t);return e===null?"\u2014":`<span class="text-slate-500">($)</span>\xA0${new Intl.NumberFormat("en-US",{maximumFractionDigits:2,minimumFractionDigits:2}).format(e)}`}function tn(t){let e=a(t.unrealized_usdc_estimate);if(e!==null)return e;let n=a(t.entry_credit),o=a(t.current_debit);return n!==null&&o!==null?n-o:null}function Eo(t,e,n){let o=at(t,e,n,"short"),s=at(t,e,n,"long");return o===null&&s===null?null:(o||0)+(s||0)}function To(t,e,n){let o=at(t,e,n,"short"),s=at(t,e,n,"long");return o===null||s===null?null:o+s}function _t(t,e,n){return A(t)==="bull_put_spread"?To(e,t,n)??tn(t)??Eo(e,t,n)??Qe(t,e,n):Qe(t,e,n)??tn(t)}function st(t,e,n){return a(t.entry_credit)}function Mt(t,e){let n=String(e||"").toUpperCase();return n==="USDC"?t===null?"\u2014":ko(t):t===null?"\u2014":n==="BTC"?`<span class="text-slate-500">\u20BF</span>\xA0${C(t,8)}`:n==="ETH"?`<span class="text-slate-500">\u2666</span>\xA0${C(t,8)}`:C(t,8)}function Se(t,e,n){if(A(t)!=="bull_put_spread")return Co(t,e);let o=a(t.unrealized_coin_native);if(o!==null)return o;let s=_t(t,e,n),r=Nt(t,e,n);return s===null||r===null||r<=0?null:s/r}function C(t,e=4){let n=a(t);return n===null?"\u2014":(e>=8?z.num8:z.num4).format(n)}function b(t){return String(t??"").replace(/[&<>"']/g,e=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[e])}function Ce(t,e){return Object.prototype.hasOwnProperty.call(t||{},e)}function xt(t,e){let n=String(t||"").toUpperCase(),o=e?.portfolio||{},s=a(o?.equity_by_book?.[n]);if(s!==null)return s;let r=a(e?.accounts?.[n]?.equity);if(r===null)return null;if(n==="USDC")return r;let i=a(e?.underlying_index_usd?.[n])??a(l.lastSpotUsd?.[n]);return i===null||i<=0?null:r*i}function fn(t){let e={};for(let n of F)e[n]=xt(n,t);return e}function we(t,e,n){let o=a(t?.day_net_flow_usdc);return a(t?.day_pnl_usdc_ex_flow)??a(t?.day_pnl_usdc_ex_flow_ex_spot)??(e!==null&&n!==null?e-n-(o??0):null)}function mn(t,e,n,o){let s=String(t||"").toUpperCase(),r=e?.portfolio||{},i=a(r?.day_net_flow_usdc_by_book?.[s]);return a(r?.day_pnl_usdc_ex_flow_by_book?.[s])??a(r?.day_pnl_usdc_ex_flow_ex_spot_by_book?.[s])??(n!==null&&o!==null?n-o-(i??0):null)}function E(t){let e=a(t);return e===null||e===0?"":e>0?"pnl-pos":"pnl-neg"}function en(t){if(t==null)return"\u2014";let e;return typeof t=="number"?e=luxon.DateTime.fromMillis(t,{zone:"utc"}):e=luxon.DateTime.fromISO(String(t),{zone:"utc"}),e.isValid?e.toLocal().toFormat("yyyy-LL-dd HH:mm"):"\u2014"}function vn(t){if(t==null)return"\u2014";let e;return typeof t=="number"?e=luxon.DateTime.fromMillis(t,{zone:"utc"}):e=luxon.DateTime.fromISO(String(t),{zone:"utc"}),e.isValid?e.toLocal().toFormat("yyyy-LL-dd"):"\u2014"}function Ro(t){let e=Math.round(t??30);return u(`Total profit (rolling ${e}d)`,`\u5DF2\u5BE6\u73FE\u640D\u76CA\uFF08\u6EFE\u52D5 ${e} \u65E5\u8996\u7A97\uFF09`)}function Bo(t){let e=Math.round(t??30);return u(`Realized APR (rolling ${e}d)`,`\u5DF2\u5BE6\u73FE\u5E74\u5316\uFF08\u6EFE\u52D5 ${e} \u65E5\u8996\u7A97\uFF09`)}function yn(t){let e=Math.round(t??30);return u(`Closes in last ${e}d only`,`\u50C5\u8A08\u6700\u8FD1 ${e} \u65E5\u5167\u5E73\u5009`)}function hn(t){let e=Math.round(t??30);return u(`Last ${e}d closes \xF7 ledger total equity`,`\u8FD1 ${e} \u65E5\u5E73\u5009 \xF7 \u7576\u65E5\u7E3D\u6B0A\u76CA`)}function zt(){let t=a(l.status?.portfolio?.total_equity_usdc);return t!==null&&t>0?t:null}function Z(t){let e=a(t.closed_timestamp_ms);if(e!==null)return e;if(t.closed_timestamp){let n=luxon.DateTime.fromISO(String(t.closed_timestamp),{zone:"utc"});if(n.isValid)return n.toMillis()}return null}function Ft(t){let e=String(t.currency||"").toUpperCase()||"Option";if(A(t)==="bull_put_spread")return L?`${e} \u8CE3\u6B0A\u50F9\u5DEE`:`${e} put spread`;let o=tt(t);if(L){let s=o.toLowerCase()==="call"?"\u8CB7\u6B0A":"\u8CE3\u6B0A";return`${e} \u8CE3\u51FA${s}`}return`${e} short ${o.toLowerCase()}`}function Do(t,{places:e={BTC:5,ETH:4,USDC:2},pnl:n=!1}={}){let o={BTC:"\u20BF",ETH:"\u2666",USDC:"($)"};return`<span class="native-book-breakdown">${["BTC","ETH","USDC"].map(r=>{let i=a(t[r]),c=i===null?"\u2014":C(i,e[r]??4),d=n?` ${E(t[r])}`:"";return`<span class="native-book-item"><span class="native-book-symbol text-slate-500">${o[r]}</span> <span class="font-mono tabular-nums${d}">${c}</span></span>`}).join("")}</span>`}function nn(t){return Do(t,{pnl:!0})}function _n(t,e){let n={BTC:"\u20BF",ETH:"\u2666",USDC:"($)"},o={BTC:5,ETH:4,USDC:2},s=["BTC","ETH","USDC"].map(r=>{let i=a(t?.[r]),c=a(e?.[r]);if(i===null&&c===null)return null;if(r==="USDC"){let f=c??i;return f===null?null:`<div class="book-equity-dual-row">
          <span class="native-book-symbol text-slate-500">${n[r]}</span>
          <span class="font-mono tabular-nums">${$(f)}</span>
        </div>`}let d=i===null?"\u2014":C(i,o[r]),p=c===null?"\u2014":$(c);return`<div class="book-equity-dual-row">
        <span class="native-book-symbol text-slate-500">${n[r]}</span>
        <span class="font-mono tabular-nums">${d}</span>
        <span class="book-equity-dual-sep text-slate-600" aria-hidden="true">\xB7</span>
        <span class="font-mono tabular-nums text-slate-400">${p}</span>
      </div>`}).filter(Boolean);return s.length?`<div class="book-equity-dual-breakdown">${s.join("")}</div>`:'<span class="text-slate-500">\u2014</span>'}function Lo(t){return`<div class="open-credit-breakdown">${gt(new Set(H.map(n=>n.id))).map(n=>{let o=b(rt(n).short),s=a(t[n]),r=s===null?"\u2014":$(s);return`<div class="open-credit-row"><span class="open-credit-label text-slate-500">${o}</span><span class="open-credit-value font-mono tabular-nums text-slate-300">${r}</span></div>`}).join("")}</div>`}function xn(t=30){let e=`/api/realized_summary?days=${t}`,n=zt();return n!==null&&(e+=`&effective_capital_usdc=${encodeURIComponent(String(n))}`),e}function ke(t=30){let e=`/api/dashboard_bundle?days=${t}`,n=zt();return n!==null&&(e+=`&effective_capital_usdc=${encodeURIComponent(String(n))}`),e}function Ee(t){t?.groups&&(l.groups=t.groups),t?.status&&(l.status=t.status,l.statusErrorOnce=!1,l.dataFreshness.source="live",l.dataFreshness.live=!0,l.dataFreshness.statusMs=0),t?.realized_summary&&(l.report=t.realized_summary)}function bn(t,e){let n=null,o=s=>{if(!s||a(s.realized_pnl)===null||!Gt(s,l.status,e))return;let r=ot(s);r===null||r<=0||(n===null||r<n)&&(n=r)};for(let s of e?.closed||[])o(s);for(let s of t?.recent_closed_trades||[])o(s);return n}function on(t){if(!t||tt(t).toLowerCase()!=="call")return!1;let e=a(t.covered_underlying_quantity);return e!==null&&e>0||String(t.short_label||"").startsWith("covered_call-")||String(t.account_name||"")==="covered_call"?!0:String(t.account_env_file||"").includes(".env.covered_call")}function K(t){let e=String(t||"").trim().toLowerCase().replaceAll("-","_").replaceAll(" ","_");return e?{naked:"naked_short",naked_put:"naked_short",naked_call:"naked_short",short_put:"naked_short",short_call:"naked_short",shortput:"naked_short",shortcall:"naked_short",naked_short_put:"naked_short",naked_short_call:"naked_short",put_spread:"bull_put_spread",short_put_spread:"bull_put_spread",bullputspread:"bull_put_spread",bull_put:"bull_put_spread",coveredcall:"covered_call"}[e]||e:""}function A(t){let e=K(t?.strategy),n=String(t?.long_instrument_name||"").trim();return(e===""||e==="naked_short")&&n&&tt(t).toLowerCase()==="put"?"bull_put_spread":e==="naked_short"&&on(t)?"covered_call":e||(tt(t).toLowerCase()==="call"&&on(t)?"covered_call":"naked_short")}function rt(t){let e=K(t);if(G[e]){let o=G[e];return!x||!L?o:{...o,title:o.titleZh||o.title,short:o.shortZh||o.short,chipShort:o.chipShortZh||o.chipShort||o.shortZh||o.short,description:o.descriptionZh||o.description}}let n=e?e.replaceAll("_"," "):"\u2014";return{id:e||"",title:n,short:n,chipShort:n,accentClass:"border-slate-700",description:""}}function Ht(t){return rt(t).title}function Uo(t){let e=K(t);return e==="naked_short"?"chip-strategy-naked":e==="bull_put_spread"?"chip-strategy-spread":e==="covered_call"?"chip-strategy-covered":"chip-strategy-unknown"}function X(t,{compact:e=!1}={}){let n=rt(t),o=Uo(n.id||t),s=e&&n.chipShort||n.short;return`<span class="chip ${o}${e?" chip--compact":""}">${b(s)}</span>`}function ft(t){return[String(t?.account_name||""),String(t?.group_id||""),String(t?.short_instrument_name||"")].join("\0")}var Ao=["realized_pnl_collateral_native","short_entry_average_price","short_close_average_price","entry_index_usd","close_index_usd","realized_close_debit","realized_close_fee","entry_fee","entry_credit","collateral_currency","strategy","option_type","covered_underlying_quantity","realized_apr_on_equity","close_book_equity","quantity","realized_pnl","contract_size","short_strike"];function sn(t){return!(t==null||t===""||typeof t=="number"&&!Number.isFinite(t))}function Po(t,e){let n={...e,...t};for(let o of Ao)sn(t[o])?n[o]=t[o]:sn(e[o])&&(n[o]=e[o]);return n}function qt(t){let e=new Map;for(let n of t||[]){let o=ft(n),s=e.get(o);e.set(o,s?Po(s,n):n)}return[...e.values()]}function Ot(t){return String(t?.status||"open").toLowerCase()!=="closed"}function Vt(t){return String(t?.status||"").toLowerCase()==="closed"?!0:Z(t)!==null}var No=3e5;function Oo(t,e){let n=new Set;for(let o of bt(t,e)){let s=String(o?.short_instrument_name||"").trim();s&&n.add(s)}return n}function Io(t,e,n){if(!Vt(t)||String(t?.close_reason||"").toLowerCase()!=="reconciled_external")return!1;let o=ot(t),s=Z(t);if(o===null||s===null||s<=o||s-o>No)return!1;let r=String(t?.short_instrument_name||"").trim();return r?Oo(e,n).has(r):!1}function Gt(t,e,n){return Vt(t)&&!Io(t,e,n)}function bt(t,e){let n=[],o=new Set;for(let s of t?.trade_groups||[]){if(!Ot(s))continue;let r=ft(s);o.has(r)||(o.add(r),n.push(s))}for(let s of e?.open||[]){if(!Ot(s))continue;let r=ft(s);o.has(r)||(o.add(r),n.push(s))}return n.map(s=>ho(t,s,e))}function gn(t,e,n=20,o=null){let s=o??l.status,r=qt([...e?.closed||[],...t?.recent_closed_trades||[]]).filter(i=>Gt(i,s,e));return r.sort((i,c)=>(Z(c)||0)-(Z(i)||0)),r.slice(0,n)}function Te(t,e){return gn(t,e,500)}function gt(t){let e=H.map(s=>s.id),n=e.filter(s=>t.has(s)),o=[...t].filter(s=>!e.includes(s)).sort();return n.concat(o)}function Mo(t){let e=String(t||"").match(/-([0-9]+(?:\.[0-9]+)?)-[CP]$/i);return e?a(e[1]):null}function J(t,e){let n=a(e==="long"?t?.long_strike:t?.short_strike);return n!==null?n:Mo(Y(t,e))}function ut(t){let e=a(t);return e===null?"\u2014":$(e,0)}function Re(t){let e=J(t,"short"),n=J(t,"long");return e===null||n===null?null:e-n}function Wt(t,e,n){let o=a(nt(t,e,"short",n)),s=a(nt(t,e,"long",n));return o===null||s===null?null:o-s}function Be(t){let e=String(t?.long_instrument_name||"").trim();if(e)return u(`Long ${e}`,`\u8CB7\u817F ${e}`);let n=a(t?.covered_underlying_quantity);return n!==null&&n>0?u(`Covered ${C(n,4)} ${String(t.currency||"").toUpperCase()}`,`\u5099\u514C ${C(n,4)} ${String(t.currency||"").toUpperCase()}`):u("Single short leg","\u55AE\u908A\u8CE3\u51FA")}function mt(t){let e=String(t?.account_name||"").trim();return e?`Account ${e}`:""}function $t(t){let e=a(t?.holding_days);if(e!==null)return e;let n=Z(t),o=ot(t);return n===null||o===null||o<=0?null:Math.max(n-o,0)/864e5}function ot(t){let e=a(t?.entry_timestamp_ms);if(e!==null)return e;if(t?.entry_timestamp){let n=luxon.DateTime.fromISO(String(t.entry_timestamp),{zone:"utc"});if(n.isValid)return n.toMillis()}return null}function zo(t){let e=ot(t),n=cn(t);return e===null||n===null||n<=e?null:(n-e)/864e5}function De(t,e){let n=String(e||"USDC").toUpperCase(),o=a(t?.accounts?.[n]?.equity);return o===null||o<=0?null:o}function U(t){return q(t)}function St(t,e){let n=U(t);return n==="USDC"?null:a(e?.underlying_index_usd?.[n])??a(l.groups?.underlying_index_usd?.[n])??a(l.lastSpotUsd?.[n])??a(t?.close_index_usd)}function Le(t,e){let n=a(t?.realized_pnl_collateral_native);if(n!==null)return n;if(U(t)==="USDC")return a(t?.realized_pnl);let s=a(t?.quantity);if(s===null||s<=0)return null;let r=a(t?.entry_index_usd),i=a(t?.close_index_usd)??r,c=a(t?.entry_fee)??0,d=a(t?.realized_close_fee)??0,p=null,f=null,m=a(t?.short_entry_average_price),v=a(t?.short_close_average_price),y=a(t?.entry_credit),h=a(t?.realized_close_debit);if(m!==null&&m>0?(p=m*s,(r===null||r<=0)&&y!==null&&(r=(y+c)/(m*s))):y!==null&&r!==null&&r>0&&(p=(y+c)/r),v!==null&&v>0?(f=v*s,(i===null||i<=0)&&h!==null&&(i=Math.max(0,h-d)/(v*s))):h!==null&&i!==null&&i>0&&(f=Math.max(0,h-d)/i),p===null||f===null)return null;let g=0;if(c>0){if(r===null||r<=0)return null;g+=c/r}if(d>0){if(i===null||i<=0)return null;g+=d/i}return p-f-g}function Fo(t){let e=U(t);return e==="BTC"||e==="ETH"}function jt(t,e){if(U(t)==="USDC")return a(t?.realized_pnl);let o=Le(t,e),s=St(t,e);return o!==null&&s!==null&&s>0?o*s:null}function Ct(t,e){let n=U(t);if(n==="USDC")return a(t?.realized_pnl);let o=Le(t,e);if(o!==null)return o;let s=a(t?.realized_pnl);if(s===null)return null;let r=a(t?.close_index_usd)??a(e?.underlying_index_usd?.[n])??a(l.groups?.underlying_index_usd?.[n])??a(l.lastSpotUsd?.[n]);return r===null||r<=0?null:s/r}function Ho(t){let e=a(t?.contract_size);return e!==null&&e>0?e:1}function qo(t,e){let n=a(t?.quantity);if(n===null||n<=0)return null;let o=Ho(t),s=A(t),r=a(t?.estimated_im_collateral);if(s==="bull_put_spread"&&r!==null&&r>0)return r/n;if(U(t)==="USDC"){if(tt(t).toLowerCase()==="call"){let d=Go(t,e)??Jt(t,e)??St(t,e)??wn(t,e);if(d!==null&&d>0)return d}else{let d=J(t,"short");if(d!==null&&d>0)return d}return null}return o}function $n(t,e){let n=qo(t,e),o=a(t?.quantity);if(n===null||o===null||o<=0)return null;let s=A(t);if(s==="covered_call"){let i=a(t?.covered_underlying_quantity);return i!==null&&i>0?i:o}return U(t)==="USDC"||s==="bull_put_spread",n*o}function Yt(t,e){return $n(t,e)}function Sn(t,e){let n=Ct(t,e),o=$t(t),s=Yt(t,e);return n===null||s===null||s<=0||o===null||o<=0?null:n/s*(365/o)}function Vo(t,e){let n=U(t);if(!Fo(t)){let c=a(t?.realized_pnl);return c===null?"\u2014":$(c)}let o=Le(t,e);if(o===null){let c=a(t?.realized_pnl);return c===null?"\u2014":$(c)}let s=jt(t,e),i=`${C(o,n==="BTC"?5:4)} ${n}`;return L?`${$(s)}\uFF08${i}\uFF09`:`${$(s)} (${i})`}function Cn(t,e){let n=a(t);return n===null?`\u2014 ${e||""}`.trim():`${new Intl.NumberFormat("en-US",{maximumFractionDigits:8}).format(n)} ${e}`}function Pt(t,e,n){let o=$(t);if(e===null||!n||n==="USDC")return o;let s=Cn(e,n);return L?`${o}\uFF08${s}\uFF09`:`${o} (${s})`}function Zt(t,e,n){let o=$(t);if(e===null||!n||n==="USDC")return o;let s=b(Cn(e,n));return`<span class="open-position-value-stack"><span class="open-position-value-line">${o}</span><span class="open-position-value-sub">${s}</span></span>`}function Ue(t,e){let n=a(t),o=a(e);return n===null||o===null||o<=0?null:n/o}function Jt(t,e){let n=U(t);return n==="USDC"?null:a(t?.entry_index_usd)??a(e?.underlying_index_usd?.[n])??a(l.groups?.underlying_index_usd?.[n])??a(l.lastSpotUsd?.[n])}function wn(t,e){let n=U(t);return n==="USDC"?null:a(t?.close_index_usd)??a(e?.underlying_index_usd?.[n])??a(l.groups?.underlying_index_usd?.[n])??a(l.lastSpotUsd?.[n])??a(t?.entry_index_usd)}function Go(t,e){let n=dn(t);if(n!=="BTC"&&n!=="ETH")return null;let o=[a(t?.entry_index_usd),a(t?.close_index_usd),a(e?.underlying_index_usd?.[n]),a(l.groups?.underlying_index_usd?.[n]),a(l.lastSpotUsd?.[n]),J(t,"short")];for(let s of o)if(s!==null&&s>100)return s;return null}function Ae(t,e){let n=$t(t);if(n===null||n<=0)return null;let o=a(t?.realized_apr_on_equity)??a(t?.realized_annualized_return);return o!==null?o:Sn(t,e)}function Wo(t,e,n=null){let o=n??l.groups;if(A(t)==="bull_put_spread"){let i=et(t,e,"short",o),c=et(t,e,"long",o);if(i===null&&c===null){let p=a(t.quantity);return p===null?null:`${C(-Math.abs(p),4)} / ${C(Math.abs(p),4)}`}let d=[];return i!==null&&d.push(C(i,4)),c!==null&&d.push(C(c,4)),d.length?d.join(" / "):null}if(!Vt(t)){let i=yo(t,e,o);return i==="\u2014"?null:i}let r=a(t.quantity);return r===null?null:C(-Math.abs(r),4)}function jo(t,e){let n=a(t?.entry_credit);if(n===null)return null;let o=a(t?.entry_fee)??0,s=U(t),r=a(t?.short_entry_average_price),i=a(t?.quantity),c=Jt(t,e),d=n;if(o>0&&r!==null&&r>0&&i!==null&&i>0&&c!==null&&c>0){let p=r*i*c,f=Math.max(.01,Math.abs(p)*.001);Math.abs(p-n)<=f?d=n-o:Math.abs(p-(n+o))<=f&&(d=n)}return s==="USDC"?d:c===null||c<=0?null:d/c}function Kt(t,e){let n=zo(t),o=$n(t,e),s=jo(t,e);return s===null||s<=0||n===null||n<=0||o===null||o<=0?a(t?.entry_net_apr):s/o*(365/n)}function Xt(t){return a(t?.entry_fee)}function Pe(t,e){return Ue(Xt(t),Jt(t,e))}function Qt(t){let e=a(t?.current_close_fee);return e!==null&&e>0?e:a(t?.realized_close_fee)}function Ne(t,e){let n=a(t?.current_close_fee),o=n!==null&&n>0?St(t,e):wn(t,e);return Ue(Qt(t),o)}function te(t,e){return Ue(a(t?.entry_credit),Jt(t,e))}function Yo(t,e){let n=[],o=new Set,s=r=>{if(!r)return;let i=ft(r);o.has(i)||(o.add(i),n.push(r))};for(let r of t?.trade_groups||[])s(r);for(let r of e?.open||[])s(r);for(let r of e?.closed||[])s(r);return n}function kn(t,e){return qt(Yo(t,e)).filter(n=>Ot(n)).sort((n,o)=>(ot(o)||0)-(ot(n)||0))}function En(t,e,n){return gn(e,n,500,t)}function Oe(t,e,n){let o=t.length,s=Math.max(1,Math.ceil(o/n)),r=Math.min(Math.max(1,e),s),i=(r-1)*n;return{rows:t.slice(i,i+n),page:r,totalPages:s,total:o,start:o?i+1:0,end:Math.min(i+n,o)}}function Ie(t,e){let{page:n,totalPages:o,total:s,start:r,end:i}=e;if(s<=pt)return"";let c=n<=1,d=n>=o,p=u(`${r}\u2013${i} of ${s} \xB7 page ${n} of ${o}`,`${r}\u2013${i} / \u5171 ${s} \u7B46 \xB7 \u7B2C ${n} / ${o} \u9801`);return`<div class="activity-pagination" data-activity-section="${b(t)}">
      <button type="button" class="filter-chip activity-page-btn" data-activity-section="${b(t)}" data-direction="prev"${c?" disabled":""}>${u("Prev","\u4E0A\u4E00\u9801")}</button>
      <span class="activity-pagination-label">${b(p)}</span>
      <button type="button" class="filter-chip activity-page-btn" data-activity-section="${b(t)}" data-direction="next"${d?" disabled":""}>${u("Next","\u4E0B\u4E00\u9801")}</button>
    </div>`}function Zo(t){let e=String(t?.currency||"").toUpperCase()||"Option",n=String(t?.short_instrument_name||"");if(n){let o=n.split("-").slice(-2).join(" ");return`${e} ${o}`.trim()}try{return Ft(t)}catch{return`${e} trade`}}function _e(t){return t.filter(e=>e).map(e=>typeof e=="string"?`<span>${b(e)}</span>`:`<span>${b(e[0])} <strong>${b(String(e[1]))}</strong></span>`).join("")}function Tn(t,e,n){let o=A(t),s=U(t)||"\u2014",r=Kt(t,e),i=Xt(t),c=Qt(t),d=a(t.entry_credit),p=Pe(t,e),f=Ne(t,e),m=te(t,e),v=ot(t),y=Vt(t),h=jt(t,e),g=$t(t),w=y?Ae(t,e):null,T=Wo(t,e,n),R=Zo(t),D=d===null?"\u2014":Pt(d,m,s),_=r===null?"\u2014":k(r,1),S=i===null?null:Pt(i,p,s),N=[[u("Opened","\u958B\u5009"),en(v)],T!==null?[u("Amount","\u6578\u91CF"),T]:null,S?[u("Entry fee","\u9032\u5834\u624B\u7E8C\u8CBB"),S]:null].filter(Boolean),M=`<div class="activity-entry-metrics">
      <div class="activity-entry-metric">
        <span class="activity-entry-metric-label">${u("Credit","\u6536\u6B0A\u5229\u91D1")}</span>
        <span class="activity-entry-metric-value ${E(d)}">${b(D)}</span>
      </div>
      <div class="activity-entry-metric">
        <span class="activity-entry-metric-label">${u("Net APR","\u6DE8\u5E74\u5316\u5831\u916C\u7387")}</span>
        <span class="activity-entry-metric-value ${E(r)}">${b(_)}</span>
      </div>
    </div>
    <div class="activity-phase-meta activity-phase-meta-secondary">
      ${_e(N)}
    </div>`,I="";if(y){let j=[[u("Closed","\u5E73\u5009"),en(Z(t))],c!==null?[u("Close fee","\u5E73\u5009\u624B\u7E8C\u8CBB"),Pt(c,f,s)]:null,g!==null?[u("Held","\u6301\u6709"),`${C(g,1)}${L?" \u5929":"d"}`]:null].filter(Boolean),me=h!==null?`<span class="activity-closed-pnl-value ${E(h)}">${Vo(t,e)}</span>`:'<span class="activity-closed-pnl-value activity-closed-pnl-value-missing">\u2014</span>',Ut=w!==null?`<span class="activity-closed-pnl-value ${E(w)}">${k(w,1)}</span>`:'<span class="activity-closed-pnl-value activity-closed-pnl-value-missing">\u2014</span>';I=`${`<div class="activity-closed-metrics">
        <div class="activity-closed-pnl">
          <span class="activity-closed-pnl-label">${u("Realized PnL","\u5DF2\u5BE6\u73FE\u640D\u76CA")}</span>
          ${me}
        </div>
        <div class="activity-closed-pnl">
          <span class="activity-closed-pnl-label">${u("Realized APR","\u5BE6\u73FE\u5E74\u5316\u5831\u916C")}</span>
          ${Ut}
        </div>
      </div>`}<div class="activity-phase-meta activity-phase-meta-secondary">${_e(j)}</div>`}else{let j=[c!==null?[u("Est. close fee","\u9810\u4F30\u5E73\u5009\u8CBB"),Pt(c,f,s)]:null].filter(Boolean);I=`<div class="activity-phase-meta">
        <span class="activity-status-pill is-open">${u("Open","\u6301\u5009\u4E2D")}</span>
        ${j.length?_e(j):`<span>${u("Est. close fee","\u9810\u4F30\u5E73\u5009\u8CBB")} <strong>\u2014</strong></span>`}
      </div>`}let W=!x&&mt(t)?mt(t):"";return`
    <li class="activity-card">
      <div class="activity-card-head">
        ${X(o)}
        <span class="activity-card-title">${b(R)}</span>
        <span class="text-[11px] text-slate-500">${b(s)}</span>
        ${W?`<span class="text-[11px] text-slate-500">${b(W)}</span>`:""}
      </div>
      <div class="activity-card-instrument">${b(t.short_instrument_name||"")}</div>
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
    </li>`}function O(t,e){let n=document.getElementById(t);n&&(n.textContent=e)}function B(t){let e=document.getElementById("toast");e&&(e.textContent=t,e.classList.remove("hidden"),clearTimeout(B._t),B._t=setTimeout(()=>e.classList.add("hidden"),5e3))}function lt(t){return new Promise(e=>setTimeout(e,t))}async function Rn(t,e){let n=0,o=Math.max(1,Math.min(e||1,t.length));async function s(){for(;;){let r=n++;if(r>=t.length)break;await t[r]()}}await Promise.all(Array.from({length:o},()=>s()))}async function P(t,e={}){let n=We(t),o=Ke+1;for(let s=0;s<o;s++){let r;try{r=await fetch(n,e)}catch(c){if(s<o-1){await lt(he*(s+1));continue}throw c}if(r.ok)return r.json();let i=`${r.status} ${r.statusText}`;try{let c=await r.json();c?.detail&&(i=`${r.status} ${c.detail}`)}catch{}if(Je.has(r.status)&&s<o-1){await lt(he*(s+1));continue}throw new Error(i)}}function oe(){return{responsive:!0,maintainAspectRatio:!1,animation:!1,interaction:{mode:"nearest",intersect:!1},plugins:{legend:{labels:{color:"rgb(203 213 225)",boxWidth:12,padding:8}},tooltip:{backgroundColor:"rgba(15,23,42,0.95)",borderColor:"rgb(51,65,85)",borderWidth:1,titleColor:"rgb(226,232,240)",bodyColor:"rgb(226,232,240)"}},scales:{x:{type:"time",time:{tooltipFormat:"yyyy-LL-dd HH:mm"},grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)"}},y:{grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)"}}}}}function Et(t){let e=l.charts[t];if(!e)return;let n=e.canvas;e.destroy(),l.charts[t]=null,n&&(n.removeAttribute("width"),n.removeAttribute("height"),n.style.width="",n.style.height="")}function Tt(t){let e=document.getElementById(t);return e?e.getContext("2d"):null}function ee(){Object.values(l.charts).forEach(t=>{try{t?.resize?.()}catch{}})}function Rt(){requestAnimationFrame(()=>{ee(),window.setTimeout(ee,80),window.setTimeout(ee,320)})}var Bn=!1;function Pn(){Bn||typeof ResizeObserver>"u"||(Bn=!0,document.querySelectorAll(".chart-panel-canvas").forEach(t=>{t.querySelector("canvas")?.id&&new ResizeObserver(()=>ee()).observe(t)}))}function se(){let t=`/api/apr_series?window_days=${l.aprWindow}`,e=zt();return e!==null&&(t+=`&effective_capital_usdc=${encodeURIComponent(String(e))}`),t}function Nn(){let t=luxon.DateTime.now().toUTC().startOf("day");return{min:t.minus({days:Math.max(l.aprWindow,30)}).toMillis(),max:t.toMillis()}}function Ko(t){let e=document.getElementById(t);return e?e.closest(".chart-panel-canvas")||e.parentElement:null}function Bt(t,{empty:e,message:n=""}={}){let o=Ko(t);if(!o)return;let s=o.querySelector(".chart-empty-overlay");if(!e){s?.remove(),o.classList.remove("chart-panel-canvas--empty");return}o.classList.add("chart-panel-canvas--empty"),s||(s=document.createElement("div"),s.className="chart-empty-overlay",o.appendChild(s)),s.textContent=n}var Dn={realized:{en:"No closed positions yet \u2014 this chart fills in after the first close.",zh:"\u5C1A\u7121\u5E73\u5009\u7D00\u9304 \u2014 \u9996\u6B21\u5E73\u5009\u5F8C\u6B64\u5716\u8868\u624D\u6703\u958B\u59CB\u7D2F\u7A4D\u3002"},apr:{en:"Rolling APR needs closed trades and daily equity snapshots.",zh:"\u6EFE\u52D5\u5E74\u5316\u9700\u6709\u5E73\u5009\u7D00\u9304\u8207\u6BCF\u65E5\u6B0A\u76CA\u5FEB\u7167\u3002"}};function Xo(t){let e=Dn[t]||Dn.realized;return u(e.en,e.zh)}function Qo({yPercent:t=!1,chartType:e="line"}={}){let n=Nn(),o=oe(),s=t?-.1:-50,r=t?.1:50;return{...o,plugins:{...o.plugins,legend:{display:!1},tooltip:{enabled:!1}},scales:{x:{...o.scales.x,...n,display:!0,offset:e==="bar",time:{unit:"day",round:"day",tooltipFormat:"yyyy-LL-dd"}},y:{...o.scales.y,display:!0,min:s,max:r,ticks:{...o.scales.y.ticks,maxTicksLimit:6,...t?{callback:i=>k(i,1)}:{}}}}}}function kt(t,e,{yPercent:n=!1,chartType:o="line",messageKind:s="realized"}={}){let r=Tt(t);if(!r)return;Et(e),Bt(t,{empty:!0,message:Xo(s)});let i=Nn(),c=[{x:i.min,y:0},{x:i.max,y:0}];l.charts[e]=new Chart(r,{type:"line",data:{datasets:[{label:u("No realized history yet","\u5C1A\u7121\u5DF2\u5BE6\u73FE\u7D00\u9304"),data:c,borderWidth:1,pointRadius:0,borderColor:"rgba(148, 163, 184, 0.35)",backgroundColor:"transparent"}]},options:Qo({yPercent:n,chartType:o})})}function Me(){return l.bookFilter==="ALL"?F:[l.bookFilter]}function On(t,e,n){let o=Object.fromEntries(H.map(s=>[s.id,0]));for(let s of t||[]){let r=A(s);if(!G[r])continue;let i=st(s,e,n);i!==null&&(o[r]+=i)}return o}function In(t,e,n=null){let o=n??l.status;return qt([...e?.closed||[],...t?.recent_closed_trades||[]]).filter(s=>Gt(s,o,e)).filter(s=>a(s?.realized_pnl)!==null)}function Mn(t,e,n){let o={BTC:0,ETH:0,USDC:0};for(let s of In(t,e)){let r=U(s);if(r!=="BTC"&&r!=="ETH"&&r!=="USDC")continue;let i=Ct(s,n);i!==null&&(o[r]+=i)}return o}function zn(t,e,n,o){let s={BTC:0,ETH:0,USDC:0},r=o??30,i=Date.now()-r*24*3600*1e3;for(let c of In(t,e)){let d=Z(c);if(d===null||d<i)continue;let p=U(c);if(p!=="BTC"&&p!=="ETH"&&p!=="USDC")continue;let f=Ct(c,n);f!==null&&(s[p]+=f)}return s}function Fn(t){let e={},n=!1;for(let o of F){let s=De(t,o);e[o]=s,s!==null&&(n=!0)}if(!n){let{portfolio:o}=vt();for(let s of F){if(s==="USDC"){e[s]=a(o?.equity_by_book?.[s]);continue}let r=a(o?.equity_by_book?.[s]),i=a(t?.underlying_index_usd?.[s])??a(l.lastSpotUsd?.[s]);e[s]=r!==null&&i!==null&&i>0?r/i:null}}return e}function ts(){return{responsive:!0,maintainAspectRatio:!1,animation:!1,interaction:{mode:"index",intersect:!1},plugins:{legend:{labels:{color:"rgb(203 213 225)",boxWidth:12,padding:8}},tooltip:{backgroundColor:"rgba(15,23,42,0.95)",borderColor:"rgb(51,65,85)",borderWidth:1,titleColor:"rgb(226,232,240)",bodyColor:"rgb(226,232,240)"}},scales:{x:{grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)"}},y:{beginAtZero:!0,grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)",maxTicksLimit:8}}}}}function Dt(){let t=Tt("chart-risk-capital");if(!t)return;Et("riskCapital");let e=Me(),n=l.status?.portfolio,o=e.map(p=>{let f=xt(p,l.status);return f!==null?f:0}),s=a(n?.total_equity_usdc),r=o.reduce((p,f)=>p+f,0),i=u(`Total ${$(s)}`,`\u5408\u8A08 ${$(s)}`);s!==null&&r>0&&Math.abs(r-s)>1?i+=u(" \xB7 bars sum may differ from headline"," \xB7 \u5404\u5E33\u52A0\u7E3D\u53EF\u80FD\u8207\u7E3D\u89BD\u7565\u6709\u5DEE\u7570"):l.status||(i=u("Awaiting live snapshot","\u7B49\u5F85\u5373\u6642\u5FEB\u7167")),O("risk-capital-meta",i),O("risk-capital-hint",u("Per-book equity in USDC equivalent from the live snapshot (or last saved snapshot).","\u5404\u5E33\u672C\u6B0A\u76CA\u4EE5 USDC \u7D04\u7576\u986F\u793A\uFF0C\u4F86\u81EA\u5373\u6642\u6216\u6700\u8FD1\u5FEB\u7167\u3002"));let c=e.map(p=>it[p]||"#94a3b8"),d=ts();Bt("chart-risk-capital",{empty:!1}),l.charts.riskCapital=new Chart(t,{type:"bar",data:{labels:e,datasets:[{label:u("Book equity (USDC eq.)","\u5E33\u672C\u6B0A\u76CA\uFF08USDC \u7D04\u7576\uFF09"),data:o,backgroundColor:c.map(p=>p+"cc"),borderColor:c,borderWidth:1}]},options:{...d,plugins:{...d.plugins,tooltip:{...d.plugins.tooltip,callbacks:{afterBody(p){if(!p?.length)return"";let f=p[0].dataIndex;if(f===void 0)return"";let m=o[f]??0,v=s>0?m/s:null;return[`${u("Share of total: ","\u4F54\u7E3D\u6B0A\u76CA\uFF1A")}${k(v,2)}`]}}}}}})}var ne=864e5;function dt(t){let e=luxon.DateTime.fromISO(String(t||"").trim(),{zone:"utc"});return e.isValid?e.toMillis():NaN}function Ln(t){let e=t.filter(n=>Number.isFinite(n.x)&&n.y!==null&&Number.isFinite(n.y)).sort((n,o)=>n.x-o.x);if(e.length===0)return[];if(e.length===1){let n=e[0];return[{x:n.x-ne,y:0},{x:n.x,y:n.y},{x:n.x+ne,y:n.y}]}return e}function wt(t){return t.filter(e=>Number.isFinite(e.x)&&e.y!==null&&Number.isFinite(e.y)).sort((e,n)=>e.x-n.x)}function Hn(t){let e=wt(t);if(e.length===0)return[];if(e.length===1){let n=e[0];return[n,{x:n.x+ne,y:n.y}]}return e}function qn(t){let e=(t||[]).map(i=>i.x).filter(Number.isFinite);if(!e.length)return{};let n=Math.min(...e),o=Math.max(...e),s=o-n,r=ne;return e.length===1||s<r*.25?{min:n-r,max:o+r}:{}}function re(){let t=Tt("chart-cum-pnl");if(!t)return;Et("cumPnl");let e=l.cumulativePnl,n=e?.realized_count?`${e.realized_count} closed groups`:u("no closed groups","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44");if(O("cum-pnl-meta",n),!e){kt("chart-cum-pnl","cumPnl");return}let o=[],s=Me();for(let r of s){let i=e.cumulative_by_book?.[r]||[];if(i.length){let c=Ln(i.map(d=>({x:dt(d.date),y:a(d.pnl_usdc)})));c.length&&o.push({label:`${r} cum. PnL`,data:c,borderColor:it[r],backgroundColor:it[r]+"22",stepped:!0,pointRadius:0,borderWidth:2})}}if(l.bookFilter==="ALL"&&e.cumulative_total?.length){let r=Ln(e.cumulative_total.map(i=>({x:dt(i.date),y:a(i.pnl_usdc)})));r.length&&o.push({label:"Total cum. PnL",data:r,borderColor:it.TOTAL,backgroundColor:it.TOTAL+"22",stepped:!0,pointRadius:0,borderWidth:2,borderDash:[4,4]})}if(!o.length){kt("chart-cum-pnl","cumPnl");return}Bt("chart-cum-pnl",{empty:!1}),l.charts.cumPnl=new Chart(t,{type:"line",data:{datasets:o},options:oe()})}function es(t){return t.filter(e=>Math.abs(e.y)>1e-12)}var ns="rgba(52, 211, 153, 0.67)",os="#34d399",ss="rgba(251, 113, 133, 0.67)",rs="#fb7185";function Un(t){return t.map(e=>{let n=a(e.y)??0;return n>0?ns:n<0?ss:"rgba(148, 163, 184, 0.4)"})}function An(t){return t.map(e=>{let n=a(e.y)??0;return n>0?os:n<0?rs:"#94a3b8"})}function ie(){let t=Tt("chart-daily-pnl");if(!t)return;Et("dailyPnl");let e=30,n=l.cumulativePnl;if(!n){O("daily-pnl-meta",u("no closed groups","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44")),kt("chart-daily-pnl","dailyPnl",{chartType:"bar"});return}let o=Me(),s=(n.daily_total||[]).filter(m=>Number.isFinite(dt(m.date))),r=n?.daily_total?.length?`${n.daily_total.length} ${u("active days","\u500B\u6709\u6548\u4EA4\u6613\u65E5")}`:u("no closed groups","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44");l.bookFilter==="ALL"&&s.length>=e&&(r+=" \xB7 30d SMA"),O("daily-pnl-meta",r);let i=m=>({x:dt(m.date),y:a(m.pnl_usdc)}),c=[];if(l.bookFilter==="ALL"){let m=wt((n.daily_total||[]).map(i));m.length&&c.push({type:"bar",label:u("Daily total","\u6BCF\u65E5\u5408\u8A08"),data:m,order:1,backgroundColor:Un(m),borderColor:An(m),borderWidth:1})}else for(let m of o){let v=n.daily_by_book?.[m]||[],y=wt(v.map(i));y=es(y),y.length&&c.push({type:"bar",label:`${m} ${u("daily","\u6BCF\u65E5")}`,data:y,order:1,backgroundColor:Un(y),borderColor:An(y),borderWidth:1})}if(l.bookFilter==="ALL"&&s.length>=e){let m=[];for(let y=e-1;y<s.length;y++){let h=0;for(let g=y-e+1;g<=y;g++)h+=a(s[g].pnl_usdc)||0;m.push({x:dt(s[y].date),y:h/e})}let v=Hn(wt(m));v.length&&c.push({type:"line",label:`30d SMA (${e}-day realized avg.)`,data:v,order:2,borderColor:"#f472b6",backgroundColor:"#f472b633",tension:.15,pointRadius:0,borderWidth:2})}if(!c.length){kt("chart-daily-pnl","dailyPnl",{chartType:"bar"});return}Bt("chart-daily-pnl",{empty:!1});let d=c.flatMap(m=>m.data||[]),p=qn(d),f=oe();l.charts.dailyPnl=new Chart(t,{type:"bar",data:{datasets:c},options:{...f,scales:{x:{...f.scales.x,...p,offset:!0,time:{unit:"day",tooltipFormat:"yyyy-LL-dd"}},y:{...f.scales.y,ticks:{...f.scales.y.ticks,maxTicksLimit:10}}}}})}function ae(){let t=Tt("chart-apr");if(!t)return;Et("apr");let e=l.aprSeries?.rows||[],n=Hn(wt(e.map(r=>({x:dt(r.date),y:a(r.apr)}))));if(!n.length){kt("chart-apr","apr",{yPercent:!0,messageKind:"apr"});return}Bt("chart-apr",{empty:!1});let o=qn(n),s=oe();l.charts.apr=new Chart(t,{type:"line",data:{datasets:[{label:`Rolling APR (${l.aprWindow}d)`,data:n,borderColor:"#facc15",backgroundColor:"rgba(250,204,21,0.15)",tension:.25,pointRadius:0,borderWidth:2,fill:!0}]},options:{...s,scales:{x:{...s.scales.x,...o,time:{unit:"day",tooltipFormat:"yyyy-LL-dd"}},y:{...s.scales.y,ticks:{...s.scales.y.ticks,callback:r=>k(r,1)}}}}})}function as(t){if(!x||!t)return;let e=String(t.investor_display_name||t.investor_id||"").trim(),n=document.querySelector(".app-header h1");n&&e&&(n.textContent=`${e} \xB7 ${L?"\u6295\u8CC7\u7D44\u5408\u7E3D\u89BD":"Investor summary"}`);let o=document.querySelector(".app-header h1 + p");if(!o)return;o.dataset.investorBaseCopy||(o.dataset.investorBaseCopy=o.textContent||"");let s=o.dataset.investorBaseCopy,r=String(t.investor_id||"").trim();o.textContent=r&&r!==e?`${u("Investor id","\u6295\u8CC7\u4EBA ID")}: ${r} \xB7 ${s}`:s}function ls(t){return x?t==="mainnet"?"border-sky-500/50 bg-sky-500/10 text-sky-200":t==="test"?"border-amber-500/50 bg-amber-500/10 text-amber-200":"border-slate-500/50 bg-slate-500/10 text-slate-200":t==="mainnet"?"border-rose-500/50 bg-rose-500/10 text-rose-200":"border-emerald-500/50 bg-emerald-500/10 text-emerald-200"}function le(t){if(!t)return;as(t);let e=(t.env||"").toLowerCase(),n=document.getElementById("env-badge");n&&(n.textContent=x?e==="mainnet"?u("Network: Mainnet","\u7DB2\u8DEF\uFF1A\u4E3B\u7DB2"):e==="multi"?u("Network: Multi-account","\u7DB2\u8DEF\uFF1A\u591A\u5E33\u6236"):e==="test"?u("Network: Test","\u7DB2\u8DEF\uFF1A\u6E2C\u8A66"):`${u("Network:","\u7DB2\u8DEF\uFF1A")} ${e||"\u2014"}`:`env: ${e||"?"}`,n.className="text-xs px-2 py-0.5 rounded-full border "+ls(e));let o=document.getElementById("strategy-badge");if(o){let i=K(t.option_strategy||""),c=t.accounts?.length||0;o.textContent=t.multi_account?u(`strategy: multi (${c} accounts)`,`\u7B56\u7565\uFF1A\u591A\u5E33\u6236\uFF08${c}\uFF09`):x?`${u("Strategy:","\u7B56\u7565\uFF1A")} ${i?Ht(i):"\u2014"}`:`strategy: ${i?Ht(i):"?"}`,o.className="text-xs px-2 py-0.5 rounded-full border border-sky-500/50 bg-sky-500/10 text-sky-200"}let s=document.getElementById("creds-badge");s&&(s.textContent=t.has_private_creds?"creds: ok":"creds: missing",s.className="text-xs px-2 py-0.5 rounded-full border "+(t.has_private_creds?"border-emerald-500/50 bg-emerald-500/10 text-emerald-200":"border-rose-500/50 bg-rose-500/10 text-rose-200"));let r=document.getElementById("scheduler-badge");if(r)if(t.scheduler_running){let i=t.snapshot_interval_sec||300,c=Math.round(i/60);r.textContent=u(`scheduler: on (every ${c} min)`,`\u5FEB\u7167\u6392\u7A0B\uFF1A\u6BCF ${c} \u5206\u9418`),r.className="text-xs px-2 py-0.5 rounded-full border border-emerald-500/50 bg-emerald-500/10 text-emerald-200"}else r.textContent=u("scheduler: off","\u5FEB\u7167\u6392\u7A0B\uFF1A\u95DC\u9589"),r.className="text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-700/30 text-slate-300";xe()}function jn(t){let e=document.getElementById("regime-badge");if(!e)return;let n=t?.portfolio?.regime||"?",o=String(n).toLowerCase(),s={normal:"\u6B63\u5E38",elevated:"\u504F\u9AD8",crisis:"\u8B66\u6212"},r={normal:"Normal",elevated:"Elevated",crisis:"Crisis"};e.textContent=x?`${u("Risk posture:","\u98A8\u63A7\u72C0\u614B\uFF1A")} ${L?s[o]||n:r[o]||n}`:`regime: ${n}`;let i=n==="normal"?"border-emerald-500/50 bg-emerald-500/10 text-emerald-200":n==="elevated"?"border-amber-500/50 bg-amber-500/10 text-amber-200":n==="crisis"?"border-rose-500/50 bg-rose-500/10 text-rose-200":"border-slate-600 bg-slate-700/30 text-slate-300";e.className=`text-xs px-2 py-0.5 rounded-full border ${i}`}function cs(t,e){let n=e?.portfolio||{},s=(e?.accounts||{})[t]||{},r=Ce(n?.equity_by_book,t),i=a(s.equity),c=xt(t,e),d=r?a(n?.day_start_equity_by_book?.[t]):null,p=a(n?.day_drawdown_pct_by_book?.[t]),f=mn(t,e,c,d),m=n?.margin_ratios_by_currency?.[t]||{},v=a(m.im_ratio),y=a(m.mm_ratio),h=a(n?.delta_totals_by_currency?.[t]),g=n?.regime_by_currency?.[t],w=n?.cooling_down_by_book?.[t],T=n?.hard_derisk_by_book?.[t],R=n?.halt_entries_by_book?.[t],D=n?.halt_entry_reasons_by_book?.[t]||[],_=t==="BTC"?"book-card-btc":t==="ETH"?"book-card-eth":"book-card-usdc",S=[];if(r||S.push('<span class="chip chip-muted">not traded</span>'),g&&r){let j=g==="normal"?"chip-ok":g==="elevated"?"chip-warn":"chip-bad";S.push(`<span class="chip ${j}">${g}</span>`)}w&&S.push('<span class="chip chip-warn">cooling</span>'),T&&S.push('<span class="chip chip-bad">hard derisk</span>'),R&&S.push('<span class="chip chip-warn">halt entries</span>'),S.length===0&&S.push('<span class="chip chip-ok">healthy</span>');let N=v!==null?Math.min(1,Math.max(0,v)):0,M=v===null?"bar-ok":v>=.45?"bar-bad":v>=.35?"bar-warn":"bar-ok",I=y!==null?Math.min(1,Math.max(0,y)):0,W=y===null?"bar-ok":y>=.33?"bar-bad":y>=.22?"bar-warn":"bar-ok";return`
    <div class="rounded-2xl border ${_} bg-slate-900/60 p-4 shadow">
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-sm font-semibold tracking-wide text-slate-200">${t} BOOK</h3>
        <div class="flex flex-wrap gap-1">${S.join("")}</div>
      </div>
      <div class="text-2xl font-mono">${$(c)}</div>
      <div class="text-xs text-slate-500 mb-3">
        ${i!==null?C(i,8)+" "+t:""}
        ${d!==null?"\xB7 day-start "+$(d):""}
      </div>
      <div class="kv"><span class="k">Day P&amp;L</span><span class="v ${E(f)}">${$(f)}</span></div>
      <div class="kv"><span class="k">Day drawdown</span><span class="v ${E(p===null?null:-p)}">${k(p)}</span></div>
      <div class="kv"><span class="k">Delta total</span><span class="v">${C(h,4)}</span></div>
      <div class="mt-3 space-y-2">
        <div>
          <div class="flex justify-between text-xs text-slate-400">
            <span>IM ratio</span><span class="font-mono">${k(v,2)}</span>
          </div>
          <div class="mini-bar"><span class="${M}" style="width:${(N*100).toFixed(1)}%"></span></div>
        </div>
        <div>
          <div class="flex justify-between text-xs text-slate-400">
            <span>MM ratio</span><span class="font-mono">${k(y,2)}</span>
          </div>
          <div class="mini-bar"><span class="${W}" style="width:${(I*100).toFixed(1)}%"></span></div>
        </div>
      </div>
      ${D.length?`<p class="mt-3 text-xs text-rose-300">${D.map(b).join("<br>")}</p>`:""}
    </div>
  `}function Yn(t){let e=document.getElementById("book-cards");if(!e)return;if(!t){e.innerHTML=`
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm md:col-span-3">
        Need DERIBIT_CLIENT_ID/SECRET in <code>.env</code> to load live status.
        Read-only views (closed trades, cumulative PnL) still work below.
      </div>`;return}let n=Object.keys(t?.portfolio?.equity_by_book||{}).map(r=>String(r).toUpperCase()).filter(r=>F.includes(r)),s=(n.length?n:F).map(r=>cs(r,t)).join("");e.innerHTML=s}function Zn(t,e){let n=document.getElementById("account-cards");if(!n)return;let o=t?.accounts||e?.dashboard_accounts||[],s=new Map((e?.account_statuses||[]).map(i=>[String(i.name||""),i])),r=o.length?o:e?.account_statuses||[];if(!r.length){n.innerHTML=`
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm md:col-span-3">
        No dashboard account metadata yet.
      </div>`;return}n.innerHTML=r.map(i=>{let c=String(i.name||""),d=s.get(c)||i,p=d.portfolio||{},f=a(p.total_equity_usdc),m=a(p.day_start_equity_usdc),v=we(p,f,m),y=p.regime||"\u2014",h=a(d.trade_group_count),g=i.has_private_creds,w=d.option_strategy||i.option_strategy||"",T=d.env||i.env||"",R=i.state_file||d.state_file||"",D=[w?X(w):"",g===void 0?"":`<span class="chip ${g?"chip-ok":"chip-bad"}">creds ${g?"ok":"missing"}</span>`].filter(Boolean);return`
        <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 shadow">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h3 class="text-sm font-semibold tracking-wide text-slate-100">${b(c||"account")}</h3>
              <p class="text-xs text-slate-500 mt-1 break-all">${b(T)} \xB7 ${b(R)}</p>
            </div>
            <div class="flex flex-wrap justify-end gap-1 flex-shrink-0">${D.join("")}</div>
          </div>
          <div class="stat-grid mt-4">
            <div class="stat-tile">
              <div class="label">Equity</div>
              <div class="value">${$(f)}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Day P&amp;L</div>
              <div class="value ${E(v)}">${$(v)}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Open groups</div>
              <div class="value">${h??"\u2014"}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Regime</div>
              <div class="value">${b(y)}</div>
            </div>
          </div>
        </div>
      `}).join("")}function Jn(t,e){let n=document.getElementById("aggregate-card");if(!n)return;let{portfolio:o,source:s}=vt(),r=e?.summary;if(!o&&!r){x&&!l.investorReady?n.innerHTML=rn():n.innerHTML=`<p class="text-sm text-slate-400">${u("No status / report data yet.","\u5C1A\u7121\u5373\u6642\u5E33\u6236\u6216\u7E3E\u6548\u6458\u8981\u8CC7\u6599\u3002")}</p>`;return}let i=a(o?.total_equity_usdc),c=a(o?.day_start_equity_usdc),d=we(o,i,c),p=a(o?.day_drawdown_pct),f=bt(t,l.groups),m=f.reduce((ao,lo)=>ao+(st(lo,t,l.groups)||0),0),v=On(f,t,l.groups),y=a(r?.realized_pnl_usdc),h=a(r?.lifetime_realized_apr),g=a(r?.realized_win_rate),w=a(r?.avg_holding_days),T=a(r?.realized_closed_group_count),R=a(r?.window_days_used),D=a(r?.window_realized_pnl_usdc),_=a(r?.window_realized_apr),S=bn(e,l.groups),N=Mn(e,l.groups,t),M=R??30,I=zn(e,l.groups,t,M),W=Fn(t),j=fn(t),me=S!==null?`${u("since","\u81EA")} ${vn(S)}`:u("no realized history yet","\u5C1A\u7121\u5DF2\u5BE6\u73FE\u7D00\u9304"),Ut=s==="snapshot"&&x?`<p class="text-xs text-amber-200/80 mt-3">${u("Equity from last snapshot; live sync continues in background.","\u6B0A\u76CA\u4F86\u81EA\u6700\u8FD1\u5FEB\u7167\uFF1B\u5373\u6642\u540C\u6B65\u65BC\u80CC\u666F\u9032\u884C\u4E2D\u3002")}</p>`:s==="live"&&x?`<p class="text-xs text-emerald-200/70 mt-3">${u("Live Deribit sync","\u5DF2\u540C\u6B65 Deribit \u5373\u6642\u8CC7\u6599")}</p>`:"",ve={totalEquity:i,dayStart:c,dayPnl:d,dayDrawdown:p,openCredit:m,creditByStrategy:v,summary:r,winRate:g,avgHolding:w,sinceLine:me,lifetimePnl:y,lifetimeNativeByBook:N,closedCount:T,windowLabelDays:M,windowPnl:D,windowNativeByBook:I,lifetimeApr:h,windowApr:_,equityNativeByBook:W,equityUsdByBook:j},Ge=an(ve);x?n.innerHTML=`
      <div class="investor-view-desktop">${Ge}</div>
      <div class="investor-view-mobile">${ln(ve)}</div>
      ${Ut}`:n.innerHTML=`${Ge}${Ut}`,xe()}function ze(t){return{id:t,openCount:0,closedCount:0,wins:0,openEntryCredit:0,unrealizedUsd:0,realizedPnl:0,annualizedSum:0,annualizedCount:0,annualizedWeightedSum:0,annualizedWeight:0,aprPnlUsdSum:0,aprCapitalDays:0,holdingSum:0,holdingCount:0,books:new Set}}function Vn(t,e,n){let o=n||"";return e.add(o),t.has(o)||t.set(o,ze(o)),t.get(o)}function us(t,e,n){if(n===null||n<=0)return null;let o=Yt(t,e);if(o===null||o<=0)return null;let s=U(t);if(s==="USDC")return o*n;let r=a(e?.underlying_index_usd?.[s])??a(l.lastSpotUsd?.[s]);return r===null||r<=0?null:o*r*n}function ds(t){return t.aprCapitalDays>0?t.aprPnlUsdSum/t.aprCapitalDays*365:null}function ps(t,e,n){let o=new Set(H.map(d=>d.id)),s=new Map;for(let d of o)s.set(d,ze(d));let r=bt(t,n);for(let d of r){let p=A(d);if(!G[p])continue;let f=Vn(s,o,p);f.openCount+=1;let m=st(d,t,n);m!==null&&(f.openEntryCredit+=m);let v=_t(d,t,n);v!==null&&(f.unrealizedUsd+=v);let y=q(d);y&&f.books.add(y)}let i=Te(e,n);for(let d of i){let p=A(d);if(!G[p])continue;let f=Vn(s,o,p);f.closedCount+=1;let m=jt(d,t);m!==null&&(f.realizedPnl+=m,m>0&&(f.wins+=1));let v=$t(d);v!==null&&(f.holdingSum+=v,f.holdingCount+=1);let y=Yt(d,t);if(m!==null&&y!==null&&y>0&&v!==null&&v>0){let w=U(d),T=y;if(w==="BTC"||w==="ETH"){let R=St(d,t);R===null||R<=0?T=null:T=y*R}T!==null&&(f.aprPnlUsdSum+=m,f.aprCapitalDays+=T*v)}let h=Ae(d,t);if(h!==null){f.annualizedSum+=h,f.annualizedCount+=1;let w=us(d,t,v);w!==null&&(f.annualizedWeightedSum+=h*w,f.annualizedWeight+=w)}let g=String(d.collateral_currency||d.currency||"").toUpperCase();g&&f.books.add(g)}return gt(o).map(d=>s.get(d)||ze(d))}function fs(t){let e=rt(t.id),n=t.closedCount>0?t.wins/t.closedCount:null,o=ds(t),s=t.holdingCount>0?t.holdingSum/t.holdingCount:null,r=Array.from(t.books).sort().join(" / ")||"\u2014";return`
    <div class="rounded-2xl border ${e.accentClass} bg-slate-900/60 p-4 shadow">
      <div class="flex items-start justify-between gap-3 mb-2">
        <div>
          <h3 class="text-sm font-semibold tracking-wide text-slate-100">${b(e.title)}</h3>
          <p class="text-xs text-slate-500 mt-1">${b(e.description)}</p>
        </div>
        ${X(t.id)}
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
          <div class="value ${E(t.unrealizedUsd)}">${$(t.unrealizedUsd)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Realized P&amp;L","\u5DF2\u5BE6\u73FE\u640D\u76CA")}</div>
          <div class="value ${E(t.realizedPnl)}">${$(t.realizedPnl)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Win rate","\u52DD\u7387")}</div>
          <div class="value">${k(n,1)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Avg holding","\u5E73\u5747\u6301\u6709")}</div>
          <div class="value">${s===null?"\u2014":C(s,2)+(L?" \u5929":"d")}</div>
        </div>
      </div>
      <div class="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <span>${t.closedCount} ${u("closed \xB7 books","\u7B46\u5DF2\u5E73 \xB7 \u5E33\u672C")} ${b(r)}</span>
        <span>${u("weighted annualized","\u52A0\u6B0A\u5E74\u5316")} ${k(o,1)}</span>
      </div>
    </div>
  `}function Kn(t){let e=K(t);return e==="covered_call"?"open-position-call":e==="bull_put_spread"?"open-position-spread":"open-position-put"}function Xn(t){let e=a(t);return e===null||Math.abs(e)<.005?"open-position-flat":e>0?"open-position-profit":"open-position-loss"}function Qn(t){let e=a(t);return e===null||Math.abs(e)<.005?u("Flat","\u6301\u5E73"):e>0?u("In profit","\u6D6E\u76C8"):u("Underwater","\u6D6E\u8667")}function to(t){let e=a(t),n=e===null?0:Math.max(0,Math.min(100,e*100));return`<span class="credit-capture-bar"><span class="${e===null?"bar-muted":e>=.5?"bar-ok":e>=.15?"bar-warn":"bar-bad"}" style="width:${n}%"></span></span>`}function V(t,e,n="",{secondary:o=!1}={}){return`
    <div class="open-position-metric${o?" open-position-kpi-secondary":""} ${n}">
      <span class="open-position-label">${t}</span>
      <span class="open-position-value">${e}</span>
    </div>`}function Gn(t,e,n,o){let s=o==="short",r=tt(t),i=s?L?`\u8CE3\u51FA${r==="Call"?"\u8CB7\u6B0A":"\u8CE3\u6B0A"}`:`Short ${r}`:u("Long protection","\u4FDD\u8B77\u8CB7\u817F"),c=Y(t,o),d=et(t,e,o),p=J(t,o),f=nt(t,e,o,"average_price"),m=nt(t,e,o,"mark_price"),v=at(e,t,n,o),y=q(t)||t.collateral_currency||"";return`
    <div class="open-position-leg ${s?"leg-short":"leg-long"}">
      <div class="open-position-leg-head">
        <span class="chip ${s?"chip-warn":"chip-ok"}">${i}</span>
        <span class="open-position-leg-amount">${d===null?"\u2014":C(d,4)}</span>
      </div>
      <div class="open-position-leg-instrument">${b(c||"\u2014")}</div>
      <div class="open-position-leg-metrics">
        ${V(u("Strike","\u5C65\u7D04\u50F9"),ut(p))}
        ${V(u("Entry","\u9032\u5834\u50F9"),ct(f,y))}
        ${V(u("Mark","\u6A19\u8A18\u50F9"),ct(m,y))}
        ${V(u("Leg PNL","\u55AE\u817F\u640D\u76CA"),v===null?"\u2014":$(v),E(v))}
      </div>
    </div>`}function ms(t,e,n){let o=A(t),s=q(t)||t.collateral_currency||"";if(o==="bull_put_spread"){let r=Re(t),i=Wt(t,e,"average_price"),c=Wt(t,e,"mark_price");return`
      <span>${u("Width","\u50F9\u5DEE\u5BEC\u5EA6")} ${ut(r)}</span>
      <span>${u("Entry gap","\u9032\u5834\u50F9\u5DEE")} ${ct(i,s)}</span>
      <span>${u("Mark gap","\u5E02\u50F9\u50F9\u5DEE")} ${ct(c,s)}</span>`}return`
    <span>${u("Strike","\u5C65\u7D04\u50F9")} ${ut(J(t,"short"))}</span>
    <span>${b(Be(t))}</span>`}function vs(t,e,n){let o=A(t),s=o==="bull_put_spread",r=$e(t),i=_t(t,e,n),c=Se(t,e,n),d=q(t)||t.collateral_currency||"",p=a(t.profit_capture),f=st(t,e,n),m=te(t,e),v=Y(t,"long"),y=et(t,e,"short"),h=et(t,e,"long"),g=I=>I===null?"":` \xB7 ${C(I,4)}`,w=Kn(o),T=Xn(i),R=Qn(i),D=f===null?"\u2014":$(f),_=m===null?"":`<span class="inv-pos-metric-sub font-mono">${Mt(m,d)}</span>`,S=Kt(t,e),N=S===null?"\u2014":k(S,1),M="";if(s){let I=Re(t),W=Wt(t,e,"average_price");M=`
      <span class="inv-pos-tag">${u("Width","\u50F9\u5DEE")} ${ut(I)}</span>
      <span class="inv-pos-tag">${u("Entry gap","\u9032\u5834")} ${ct(W,d)}</span>`}else M=`
      <span class="inv-pos-tag">${u("Strike","\u5C65\u7D04")} ${ut(J(t,"short"))}</span>
      <span class="inv-pos-tag">${b(Be(t))}</span>`;return`
    <article class="inv-position ${w} ${T}">
      <header class="inv-position-head">
        <div class="inv-position-main">
          <div class="inv-position-titleline">
            ${X(o,{compact:!0})}
            <h3 class="inv-position-name">${b(Ft(t))}</h3>
          </div>
          <p class="inv-position-contract font-mono">${b(t.short_instrument_name||"\u2014")}<span class="inv-position-size tabular-nums">${g(y)}</span></p>
          ${s&&v?`<p class="inv-position-contract font-mono inv-position-contract--long">${u("Long","\u8CB7\u817F")} ${b(v)}<span class="inv-position-size tabular-nums">${g(h)}</span></p>`:""}
          <div class="inv-position-tags">
            <span class="inv-pos-tag">${b(d)}</span>
            <span class="inv-pos-tag inv-pos-tag--status">${b(R)}</span>
            ${M}
          </div>
        </div>
        <div class="inv-position-pnl">
          <span class="inv-position-pnl-label">${u("Unrealized","\u672A\u5BE6\u73FE")}</span>
          <span class="inv-position-pnl-value font-mono tabular-nums ${E(i)}">${i===null?"\u2014":$(i)}</span>
          <span class="inv-position-pnl-native font-mono tabular-nums ${E(c)}">${Mt(c,d)}</span>
        </div>
      </header>
      <div class="inv-position-strip" role="list">
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("DTE","\u5230\u671F")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${r!==null?`${C(r,1)}${L?"\u5929":"d"}`:"\u2014"}</span>
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("Credit kept","\u6B0A\u5229\u91D1")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${k(p,1)}</span>
          ${to(p)}
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("Entry","\u9032\u5834")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${D}</span>
          ${_}
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("Entry APR","\u9032\u5834\u5E74\u5316")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums ${S!==null&&S>=.15?"pnl-pos":""}">${N}</span>
        </div>
      </div>
    </article>`}function ys(t,e,n){let o=A(t),s=o==="bull_put_spread",r=$e(t),i=_t(t,e,n),c=Se(t,e,n),d=q(t)||t.collateral_currency||"",p=a(t.profit_capture),f=st(t,e,n),m=te(t,e),v=Xt(t),y=Pe(t,e),h=Qt(t),g=Ne(t,e),w=Y(t,"long"),T=!x&&mt(t)?mt(t):"",R=Kn(o),D=Xn(i),_=x?u(`${d} book`,`${d} \u5E33\u672C`):`${d} book`;return`
    <article class="open-position-card ${R} ${D}">
      <div class="open-position-glow"></div>
      <div class="open-position-header">
        <div class="open-position-main">
          <div class="open-position-title-row">
            ${X(o)}
            <h3>${b(Ft(t))}</h3>
            <span class="open-book-pill">${b(_)}</span>
            <span class="open-status-pill">${Qn(i)}</span>
          </div>
          <div class="open-position-instruments">
            <span>${b(t.short_instrument_name||"\u2014")}</span>
            ${s&&w?`<span>${u("Long","\u8CB7\u5165\u4FDD\u8B77")} ${b(w)}</span>`:""}
          </div>
          <div class="open-position-detail-row">
            ${ms(t,e,n)}
            ${T?`<span>${b(T)}</span>`:""}
          </div>
        </div>
        <div class="open-position-pnl-panel">
          <span class="open-position-label"${s?` title="${u("Sum of leg mark MTM when both legs load; otherwise engine entry\u2212debit (bid/ask close est.).","\u5169\u817F\u7686\u8F09\u5165\u6642\u70BA\u6A19\u8A18\u640D\u76CA\u52A0\u7E3D\uFF1B\u5426\u5247\u70BA\u5F15\u64CE\u9032\u5834\u6536\u6582\u8207\u73FE\u4F30\u5E73\u5009\u5DEE\u984D\u3002")}"`:""}>${u("Unrealized PNL","\u672A\u5BE6\u73FE\u640D\u76CA")}</span>
          <strong class="${E(i)}">${i===null?"\u2014":$(i)}</strong>
          <span class="open-position-native ${E(c)}">${Mt(c,d)}</span>
        </div>
      </div>
      <div class="open-position-kpis open-position-kpis-extended">
        ${V(u("DTE","\u8DDD\u5230\u671F\u5929\u6578"),r!==null?`${C(r,2)}${L?" \u5929":"d"}`:"\u2014")}
        ${V(u("Credit kept","\u5DF2\u6536\u6B0A\u5229\u91D1\u6BD4\u4F8B"),`${k(p,1)}${to(p)}`)}
        ${V(u("Entry credit","\u9032\u5834\u6536\u6582"),f===null?"\u2014":Zt(f,m,d))}
        ${(()=>{let S=Kt(t,e),N=S!==null&&S>=.15?"pnl-pos":"";return V(u("Entry net APR","\u9032\u5834\u6DE8\u5E74\u5316"),S===null?"\u2014":k(S,1),N)})()}
        ${V(u("Entry fee","\u9032\u5834\u624B\u7E8C\u8CBB"),v===null?"\u2014":Zt(v,y,d))}
        ${V(u("Est. close fee","\u9810\u4F30\u5E73\u5009\u8CBB"),h===null?"\u2014":Zt(h,g,d))}
      </div>
      <div class="open-position-legs ${s?"has-two-legs":"has-one-leg"}">
        ${Gn(t,e,n,"short")}
        ${s?Gn(t,e,n,"long"):""}
      </div>
    </article>`}function hs(t,e,n){let o=ys(t,e,n);return x?`<div class="investor-view-desktop">${o}</div><div class="investor-view-mobile">${vs(t,e,n)}</div>`:o}function _s(t,e,n,o){let s=K(t)||t,r=rt(s),i=e.map(c=>hs(c,n,o)).join("");return`
    <div class="rounded-2xl border ${r.accentClass} bg-slate-900/60 shadow overflow-hidden">
      <div class="flex flex-wrap items-baseline justify-between gap-3 px-4 py-3 border-b border-slate-800 bg-slate-950/40">
        <div class="flex flex-wrap items-center gap-2 min-w-0">
          <h3 class="text-sm font-semibold text-slate-200">${b(r.title)}</h3>
          ${X(s)}
        </div>
        <span class="text-xs text-slate-500">${e.length} ${u("open","\u7B46\u6301\u5009")}</span>
      </div>
      <div class="p-4">
        <div class="open-position-list">
          ${i}
        </div>
      </div>
    </div>`}function ce(t,e,n){let o=document.getElementById("strategy-cards"),s=document.getElementById("strategy-open-groups");if(!o&&!s)return;let r=ps(t,e,n),i=bt(t,n),c=i.length,d=Te(e,n).length,p=r.filter(v=>v.openCount||v.closedCount).length;if(O("strategy-meta",x?u(`${c} open \xB7 ${d} closed \xB7 ${p||0} active strategy groups`,`${c} \u7B46\u6301\u5009 \xB7 ${d} \u7B46\u5DF2\u5E73 \xB7 ${p||0} \u985E\u7B56\u7565`):`${c} open \xB7 ${d} closed \xB7 ${p||0} active strategy groups`),o&&(o.innerHTML=r.map(fs).join("")),!s)return;if(!i.length){s.innerHTML=`
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-sm text-slate-400">
        ${u("No open strategy positions.","\u76EE\u524D\u6C92\u6709\u958B\u5009\u4E2D\u7684\u7B56\u7565\u90E8\u4F4D\u3002")}
      </div>`;return}let f=new Map,m=new Set(H.map(v=>v.id));for(let v of i){let y=A(v);G[y]&&(f.has(y)||f.set(y,[]),f.get(y).push(v))}s.innerHTML=gt(m).filter(v=>f.has(v)).map(v=>_s(v,f.get(v),t,n)).join("")}function Wn(t,e,n,o,s){if(!t)return;if(!e.length){t.innerHTML=`<li class="activity-empty">${b(s)}</li>`;return}let r=[];for(let i of e)try{r.push(Tn(i,n,o))}catch(c){console.warn("activity card skipped",i?.group_id,c)}t.innerHTML=r.length?r.join(""):`<li class="activity-empty">${b(s)}</li>`}function Lt(t,e,n){let o=document.getElementById("activity-open-list"),s=document.getElementById("activity-closed-list");if(!o&&!s)return;let r=kn(t,n),i=En(t,e,n),c=Oe(r,l.activityOpenPage,pt),d=Oe(i,l.activityClosedPage,pt);l.activityOpenPage=c.page,l.activityClosedPage=d.page,O("activity-meta",u(`${r.length} open \xB7 ${i.length} closed`,`${r.length} \u6301\u5009\u4E2D \xB7 ${i.length} \u5DF2\u5E73\u5009`)),Wn(o,c.rows,t,n,u("No open positions","\u5C1A\u7121\u6301\u5009")),Wn(s,d.rows,t,n,u("No closed trades","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D00\u9304"));let p=document.getElementById("activity-open-pagination"),f=document.getElementById("activity-closed-pagination");p&&(p.innerHTML=Ie("open",c),p.hidden=!p.innerHTML),f&&(f.innerHTML=Ie("closed",d),f.hidden=!f.innerHTML)}function xs(t){let e=Array.isArray(t?.strategy_stresses)?t.strategy_stresses.filter(Boolean):[];return e.length?e:[t]}function bs(t,e){let n=t.equity_usdc_by_book||{},o=t.strategy_analysis||{},s=K(t.option_strategy||o.label||"naked_short"),r=Object.values(n).reduce((m,v)=>m+(a(v)||0),0),i=(t.accounts||[]).map(m=>m?.name).filter(Boolean).join(", "),c=Array.isArray(o.actions)?o.actions:[],d=F.map(m=>`
        <div class="rounded-xl bg-slate-800/40 px-3 py-2">
          <div class="text-[11px] text-slate-400 uppercase tracking-wide">${m} book</div>
          <div class="font-mono text-sm">${$(n[m])}</div>
        </div>`).join(""),p=(t.scenarios||[]).map(m=>{let v=a(m.loss_usdc_total),y=a(m.loss_usdc_pct_of_total_equity),h=m.loss_by_book_usdc||{};return`
        <tr>
          <td class="px-3 py-2 font-mono">${k(a(m.shock),0)}</td>
          <td class="px-3 py-2 font-mono">${k(a(m.slippage),0)}</td>
          <td class="px-3 py-2 text-right font-mono ${E(v)}">${$(v)}</td>
          <td class="px-3 py-2 text-right font-mono">${k(y,2)}</td>
          <td class="px-3 py-2 text-right font-mono ${E(a(h.BTC))}">${$(h.BTC)}</td>
          <td class="px-3 py-2 text-right font-mono ${E(a(h.ETH))}">${$(h.ETH)}</td>
          <td class="px-3 py-2 text-right font-mono ${E(a(h.USDC))}">${$(h.USDC)}</td>
        </tr>`}).join(""),f=c.length?`<ul class="mt-2 list-disc list-inside text-xs text-slate-500 space-y-1">
        ${c.map(m=>`<li>${b(m)}</li>`).join("")}
      </ul>`:"";return`
    <div class="${e>1?"rounded-2xl border border-slate-800 bg-slate-900/40 p-4":""}">
      <div class="rounded-xl bg-slate-800/40 px-3 py-3 mb-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div class="text-[11px] text-slate-400 uppercase tracking-wide">Strategy black swan read</div>
            <div class="mt-1 flex items-center gap-2 text-sm text-slate-200">
              <span>${b(Ht(s))}</span>
              ${X(s)}
            </div>
          </div>
          <div class="text-[11px] text-slate-500">
            ${b(i||`${t.scenarios?.length||0} scenarios \xB7 ${t.positions?.length||0} legs`)}
          </div>
        </div>
        <p class="mt-2 text-xs text-slate-400">${b(o.summary||"")}</p>
        <p class="mt-1 text-xs text-slate-500">${b(o.focus||"")}</p>
        ${f}
      </div>
      <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
        ${d}
        <div class="rounded-xl bg-slate-800/40 px-3 py-2">
          <div class="text-[11px] text-slate-400 uppercase tracking-wide">Total equity (USDC)</div>
          <div class="font-mono text-sm">${$(r)}</div>
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
  `}function eo(t){if(x)return;let e=document.getElementById("stress-card");if(!e)return;if(!t){e.innerHTML='<p class="text-sm text-slate-400">Set DERIBIT_CLIENT_ID and DERIBIT_CLIENT_SECRET to load live stress data.</p>',O("stress-meta","\u2014");return}let n=xs(t),o=n.reduce((r,i)=>r+(i.scenarios?.length||0),0),s=n.reduce((r,i)=>r+(i.positions?.length||0),0);O("stress-meta",`${n.length} strategy view${n.length===1?"":"s"} \xB7 ${o} scenarios \xB7 ${s} legs`),e.innerHTML=`
    <div class="space-y-4">
      ${n.map(r=>bs(r,n.length)).join("")}
    </div>
    <p class="text-xs text-slate-500 mt-3">
      Per-book loss is capped at that book's equity (liquidation-style floor). Spot shock is a negative index move.
      For bull put spread, long option legs are netted when present; for covered call, BTC/ETH spot cover drawdown is included.
    </p>
  `}var Fe=null;function oo(){Fe?.()}function He(){let t=document.getElementById("header-spot-btc"),e=document.getElementById("header-spot-eth"),n=l.lastSpotUsd.BTC,o=l.lastSpotUsd.ETH;t&&(t.textContent=n!==null&&n>0?`BTC ${z.usd2.format(n)}`:"BTC \u2014"),e&&(e.textContent=o!==null&&o>0?`ETH ${z.usd2.format(o)}`:"ETH \u2014")}function de(){let t=[["risk-capital",Dt],["cum-pnl",re],["daily-pnl",ie],["apr",ae]];for(let[e,n]of t)try{n()}catch(o){console.error(`${e} chart render failed`,o)}Rt()}var $s={spot:{en:"Fetching BTC / ETH market prices\u2026",zh:"\u6B63\u5728\u53D6\u5F97 BTC / ETH \u5373\u6642\u5831\u50F9\u2026"},snapshot:{en:"Loading last equity snapshot\u2026",zh:"\u6B63\u5728\u8B80\u53D6\u6700\u8FD1\u6B0A\u76CA\u5FEB\u7167\u2026"},health:{en:"Checking account connection\u2026",zh:"\u6B63\u5728\u78BA\u8A8D\u5E33\u6236\u9023\u7DDA\u2026"},groups:{en:"Loading open positions and spreads\u2026",zh:"\u6B63\u5728\u8B80\u53D6\u6301\u5009\u8207\u50F9\u5DEE\u90E8\u4F4D\u2026"},cumulative:{en:"Loading realized P&L history\u2026",zh:"\u6B63\u5728\u8F09\u5165\u5DF2\u5BE6\u73FE\u640D\u76CA\u6B77\u53F2\u2026"},apr:{en:"Calculating rolling performance (APR)\u2026",zh:"\u6B63\u5728\u8A08\u7B97\u6EFE\u52D5\u5E74\u5316\u5831\u916C\u2026"},status:{en:"Syncing live equity and margin\u2026",zh:"\u6B63\u5728\u540C\u6B65\u5373\u6642\u6B0A\u76CA\u8207\u4FDD\u8B49\u91D1\u2026"},summary:{en:"Loading performance summary from local records\u2026",zh:"\u6B63\u5728\u5F9E\u672C\u5730\u7D00\u9304\u8F09\u5165\u7E3E\u6548\u6458\u8981\u2026"},render:{en:"Preparing your dashboard\u2026",zh:"\u6B63\u5728\u6574\u7406\u5100\u8868\u677F\u986F\u793A\u2026"},done:{en:"Done",zh:"\u5B8C\u6210"}};function Ss(t){let e=$s[t];return e?u(e.en,e.zh):""}function Cs(t,{includeCharts:e=!0}={}){let n=3+(t?2:0)+1;return e&&(n+=2),n}function qe(t,e){let n=Math.min(100,Math.max(0,Math.round(t*100))),o=document.getElementById("investor-load-bar-fill");o&&(o.style.width=`${n}%`);let s=document.querySelector("[data-investor-load-pct]");s&&(s.textContent=`${n}%`);let r=document.querySelector("[data-investor-load-step]");r&&e&&(r.textContent=Ss(e))}function so(){if(!x)return;let t=(e,n,o)=>{let s=document.querySelector(`[data-investor-load-${e}]`);s&&(s.textContent=u(n,o))};t("eyebrow","Please wait","\u8ACB\u7A0D\u5019"),t("title","Loading your portfolio","\u6B63\u5728\u8F09\u5165\u60A8\u7684\u6295\u8CC7\u7D44\u5408"),t("hint","Showing snapshot first; live positions and P&L sync in the background.","\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\uFF1B\u6301\u5009\u8207\u640D\u76CA\u65BC\u80CC\u666F\u540C\u6B65\u4E2D\u3002")}function ro({blocking:t=!0}={}){if(!x)return;l.investorLoadDone=0,l.investorLoadTotal=Cs(!1),document.body.classList.toggle("investor-blocking-load",t);let e=document.getElementById("investor-load-overlay");e&&(e.classList.remove("hidden"),e.classList.toggle("investor-load-overlay--refresh",!t),e.setAttribute("aria-busy","true"));let n=document.getElementById("refresh-now");n&&(n.disabled=!0),qe(0,"spot")}function ue(t){if(!x)return;l.investorLoadDone=Math.min(l.investorLoadTotal||1,l.investorLoadDone+1);let e=l.investorLoadTotal>0?l.investorLoadDone/l.investorLoadTotal:0;qe(e,t)}function ws(t){if(!x)return;if(!t){ro({blocking:!l.investorReady});return}qe(1,"done"),l.investorReady=!0,document.body.classList.remove("investor-blocking-load"),document.body.classList.add("investor-ready");let e=document.getElementById("investor-load-overlay");e&&(e.classList.add("hidden"),e.classList.remove("investor-load-overlay--refresh"),e.setAttribute("aria-busy","false"));let n=document.getElementById("refresh-now");n&&(n.disabled=!1),Rt()}async function ks({renderDependentViews:t=!0,updateDom:e=!0}={}){try{let n=await P("/api/spot");l.lastSpotUsd.BTC=a(n.BTC),l.lastSpotUsd.ETH=a(n.ETH),e&&(He(),t&&(ce(l.status,l.report,l.groups),Lt(l.status,l.report,l.groups)))}catch{}}function Es(){return!!document.getElementById("charts-section")?.open}async function no(){try{let t=await P("/api/portfolio/snapshot");l.portfolioSnapshot=t,t?.source==="ledger"&&(l.dataFreshness.source="snapshot",l.dataFreshness.snapshotMs=a(t.freshness_ms),l.dataFreshness.live=!1)}catch{}}async function Ts(){let t=ye,e=!1,n=lt(t).then(()=>{throw e=!0,new Error("status timeout")});try{let o=await Promise.race([P("/api/status"),n]);return l.status=o,l.statusErrorOnce=!1,l.dataFreshness.source="live",l.dataFreshness.live=!0,l.dataFreshness.statusMs=0,o}catch(o){return e&&l.portfolioSnapshot?.portfolio?(l.statusErrorOnce||(B(u("Live sync is slow; showing last snapshot.","\u5373\u6642\u540C\u6B65\u8F03\u6162\uFF0C\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\u3002")),l.statusErrorOnce=!0),P("/api/status").then(s=>{l.status=s,l.dataFreshness.source="live",l.dataFreshness.live=!0,oo()}).catch(()=>{}),null):(l.status=null,l.statusErrorOnce||(B(`status: ${o.message}`),l.statusErrorOnce=!0),null)}}async function Rs({backgroundOnTimeout:t=!1}={}){let e=ye,n=!1,o=P(ke(30)),s=x?Promise.race([o,lt(e).then(()=>{throw n=!0,new Error("dashboard bundle timeout")})]):o;try{return Ee(await s),!0}catch(r){return x&&n&&l.portfolioSnapshot?.portfolio?(l.statusErrorOnce||(B(u("Live sync is slow; showing last snapshot.","\u5373\u6642\u540C\u6B65\u8F03\u6162\uFF0C\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\u3002")),l.statusErrorOnce=!0),t&&P(ke(30)).then(i=>{Ee(i),oo()}).catch(()=>{}),!1):((!x||!n)&&B(`dashboard bundle: ${r.message}`),!1)}}async function Ve({force:t=!1,investorFetchWrap:e=null}={}){if(!t&&l.chartsDataLoaded){de();return}if(!l.chartsLoadInFlight){l.chartsLoadInFlight=!0;try{let n=()=>P("/api/cumulative_pnl_series").then(s=>{l.cumulativePnl=s}).catch(s=>B(`cumulative pnl: ${s.message}`)),o=()=>P(se()).then(s=>{l.aprSeries=s}).catch(s=>B(`apr series: ${s.message}`));e?await Promise.all([e("cumulative",n),e("apr",o)]):await Promise.all([n(),o()]),l.chartsDataLoaded=!0,de()}finally{l.chartsLoadInFlight=!1}}}function Bs(){return l.lastRefreshStartedMs?Math.max(0,At-(Date.now()-l.lastRefreshStartedMs)):0}async function pe({force:t=!1,silentIfLimited:e=!1,renderDashboard:n}={}){if(Fe=n??null,l.refreshInFlight){e||B(u("refresh already running","\u5DF2\u6709\u66F4\u65B0\u6B63\u5728\u9032\u884C"));return}let o=Bs();if(!t&&o>0){e||B(u(`refresh rate limited; wait ${Math.ceil(o/1e3)}s`,`\u8ACB\u7A0D\u5019 ${Math.ceil(o/1e3)} \u79D2\u5F8C\u518D\u8A66`));return}l.refreshInFlight=!0,l.lastRefreshStartedMs=Date.now();let s=x&&!l.investorReady;s?ro({blocking:!0}):x&&yt(!0,{indeterminate:!0});try{let i=function(){r||(r=!0,requestAnimationFrame(()=>{r=!1;try{n?.()}catch(_){console.error("renderDashboard failed",_),B(`render failed: ${_.message}`)}}))},c=function(_,S){return s?S().finally(()=>ue(_)):S()},v=function(){return P("/api/groups").then(_=>{l.groups=_,i()}).catch(_=>{B(`groups: ${_.message}`)})},y=function(){return P(xn(30)).then(_=>{l.report=_,i()}).catch(_=>B(`realized summary: ${_.message}`))},h=function(){return P("/api/status").then(_=>{l.status=_,l.statusErrorOnce=!1,i()}).catch(_=>{l.status=null,l.statusErrorOnce||(B(`status: ${_.message}`),l.statusErrorOnce=!0)})},g=function(){return P("/api/stress?shocks=0.1,0.2,0.3,0.4,0.5").then(_=>{l.stress=_,i()}).catch(_=>B(`stress: ${_.message}`))},r=!1;try{let _=c("spot",()=>ks({renderDependentViews:!x,updateDom:!0})),S=c("health",()=>P("/api/health").then(N=>{l.health=N}));await Promise.all([_,S]),le(l.health)}catch(_){B(`health failed: ${_.message}`)}let d=!!l.health?.has_private_creds,p=!1;if(x&&s){try{await Promise.race([c("snapshot",no),lt(Ze)])}catch{}p=!0,ws(!0),yt(!0,{indeterminate:!0}),i()}let f=s?(_,S)=>c(_,S):null,m=(_,S)=>f?f(_,S):S();async function w(){await m("groups",v),x?(await m("status",()=>Ts().then(()=>i())),await m("summary",y)):(await h(),await y())}async function T(){if(!d){await m("groups",v),l.status=null,l.report=null,l.stress=null;return}if(Ye&&await Rs({backgroundOnTimeout:x})){s&&(ue("groups"),ue("status"),ue("summary")),i();return}await w()}let R=[()=>T()];d&&!x&&R.push(()=>g()),x&&!p&&R.push(()=>m("snapshot",no));let D=!x||Es();if(D&&R.push(()=>Ve({force:!x,investorFetchWrap:f})),await Rn(R,je),x&&(l.stress=null),D||Dt(),!x||l.investorReady)try{n?.()}catch(_){console.error("renderDashboard failed",_),B(`render failed: ${_.message}`)}yt(!1),O("last-refresh",`${u("last refresh:","\u4E0A\u6B21\u66F4\u65B0\uFF1A")} ${luxon.DateTime.now().toFormat("HH:mm:ss")}`)}finally{l.refreshInFlight=!1,yt(!1),Fe=null}}function fe(){un(l.status,l.groups),jn(l.status),le(l.health),He(),Zn(l.health,l.status),Yn(l.status),Jn(l.status,l.report),ce(l.status,l.report,l.groups),de(),Lt(l.status,l.report,l.groups),eo(l.stress)}function Ls(t){l.bookFilter=t;let e=document.querySelector("#book-filter");e&&e.querySelectorAll("button[data-book]").forEach(n=>{n.classList.toggle("filter-active",n.dataset.book===t)}),Dt(),re(),ie()}function Us(){let t=document.getElementById("auto-refresh");if(!t)return;function e(){l.autoRefreshHandle&&(clearInterval(l.autoRefreshHandle),l.autoRefreshHandle=null),t.checked&&(l.autoRefreshHandle=setInterval(()=>pe({silentIfLimited:!0,renderDashboard:fe}),At))}t.addEventListener("change",e),e()}function As(){document.getElementById("refresh-now")?.addEventListener("click",()=>pe({renderDashboard:fe})),document.getElementById("book-filter")?.addEventListener("click",t=>{let e=t.target.closest("button[data-book]");e&&Ls(e.dataset.book)}),document.getElementById("activity-section")?.addEventListener("click",t=>{let e=t.target.closest("button.activity-page-btn");if(!e||e.disabled)return;let n=e.dataset.activitySection,o=e.dataset.direction==="next"?1:-1;n==="open"?l.activityOpenPage+=o:n==="closed"&&(l.activityClosedPage+=o),Lt(l.status,l.report,l.groups)}),document.getElementById("apr-window")?.addEventListener("change",async t=>{l.aprWindow=parseInt(t.target.value,10)||30;try{l.aprSeries=await P(se())}catch(e){B(`apr series: ${e.message}`)}ae()})}function Ps(){document.querySelectorAll("details.collapsible-section").forEach(t=>{t.addEventListener("toggle",()=>{t.open&&(Rt(),x&&t.id==="charts-section"&&Ve({renderDashboard:fe}))})})}function io(){let t=()=>{so(),Pn(),As(),Ps(),Us(),pe({force:!0,renderDashboard:fe})};document.readyState==="loading"?document.addEventListener("DOMContentLoaded",t):t()}io();})();
