(()=>{var Co="ops",_=Co==="investor",wo=(()=>{if(!_)return"en";let t=String(typeof window<"u"&&window.__INVESTOR_LOCALE__||"en").trim().toLowerCase();return t==="zh-hant"||t==="zh_tw"||t==="zh-tw"||t==="zh-hk"||t==="zh"?"zh":"en"})(),B=_&&wo==="zh";function u(t,e){return _&&B?e:t}function ko(){try{return document.querySelector('meta[name="dashboard-api-base"]')?.getAttribute("content")?.trim()||""}catch{return""}}function tn(t){if(/^https?:\/\//i.test(t))return t;let n=((typeof window<"u"&&window.__API_BASE__?String(window.__API_BASE__).trim():"")||ko()).replace(/\/$/,""),o=t.startsWith("/")?t:`/${t}`;return n?`${n}${o}`:o}var F={usd0:new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:0}),usd2:new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:2}),num4:new Intl.NumberFormat("en-US",{maximumFractionDigits:4}),num8:new Intl.NumberFormat("en-US",{maximumFractionDigits:8}),pct2:new Intl.NumberFormat("en-US",{style:"percent",maximumFractionDigits:2,minimumFractionDigits:2}),pct1:new Intl.NumberFormat("en-US",{style:"percent",maximumFractionDigits:1,minimumFractionDigits:1})},lt={BTC:"#fb923c",ETH:"#818cf8",USDC:"#38bdf8",TOTAL:"#a3e635"},z=["BTC","ETH","USDC"],Mt=18e4,en=_?6:3,nn=!0,ge=45e3,on=3e3,rn=new Set([502,503,504]),sn=2,be=450,vt=10,H=[{id:"covered_call",title:"Covered Call",titleZh:"\u5099\u514C\u8CB7\u6B0A",short:"Covered Call",shortZh:"\u5099\u514C",chipShort:"CC",chipShortZh:"\u5099\u514C",accentClass:"strategy-card-call",description:"Short call backed by existing BTC/ETH spot collateral.",descriptionZh:"\u5728\u6301\u6709\u73FE\u8CA8\u64D4\u4FDD\u4E0B\u8CE3\u51FA\u8CB7\u6B0A\uFF0C\u4EE5\u6B0A\u5229\u91D1\u589E\u5F37\u6536\u76CA\u3002"},{id:"naked_short",title:"Naked Short",titleZh:"\u55AE\u8CE3\u9078\u64C7\u6B0A\uFF08\u88F8\u8CE3\uFF09",short:"Naked Short",shortZh:"\u88F8\u8CE3",chipShort:"Naked",chipShortZh:"\u88F8\u8CE3",accentClass:"strategy-card-put",description:"Single-leg short option (put / call / both) with uncapped tail risk on the chosen side.",descriptionZh:"\u55AE\u908A\u8CE3\u51FA\u8CB7\uFF0F\u8CE3\u6B0A\uFF1B\u5728\u5C0D\u61C9\u65B9\u5411\u5177\u5C3E\u90E8\u98A8\u96AA\uFF0C\u9700\u56B4\u683C\u98A8\u63A7\u3002"},{id:"bull_put_spread",title:"Bull Put Spread",titleZh:"\u725B\u52E2\u8CE3\u6B0A\u50F9\u5DEE",short:"Put Spread",shortZh:"\u8CE3\u6B0A\u50F9\u5DEE",chipShort:"Spread",chipShortZh:"\u50F9\u5DEE",accentClass:"strategy-card-spread",description:"Short put paired with a lower-strike long put protection leg.",descriptionZh:"\u8CE3\u51FA\u8F03\u9AD8\u5C65\u7D04\u50F9\u8CE3\u6B0A\uFF0C\u4E26\u8CB7\u5165\u8F03\u4F4E\u5C65\u7D04\u50F9\u8CE3\u6B0A\u4F5C\u4FDD\u8B77\u3002"}],V=Object.fromEntries(H.map(t=>[t.id,t]));var a={health:null,status:null,report:null,stress:null,groups:null,cumulativePnl:null,aprSeries:null,portfolioSnapshot:null,dataFreshness:{source:null,snapshotMs:null,statusMs:null,live:!1},chartsDataLoaded:!1,chartsLoadInFlight:!1,stressDataLoaded:!1,stressLoadInFlight:!1,bookFilter:"ALL",aprWindow:30,charts:{},autoRefreshHandle:null,refreshInFlight:!1,investorReady:!1,investorLoadTotal:0,investorLoadDone:0,lastRefreshStartedMs:0,statusErrorOnce:!1,lastUnderlyingIndexUsd:{},lastSpotUsd:{BTC:null,ETH:null},activityOpenPage:1,activityClosedPage:1};function W(t){return String(t).padStart(2,"0")}function an(t=new Date){return`${W(t.getUTCHours())}:${W(t.getUTCMinutes())}:${W(t.getUTCSeconds())}`}function ht(t){if(t==null||t==="")return null;let e=Date.parse(String(t).trim());return Number.isFinite(e)?e:null}function ln(t){return t==null||t===void 0?null:typeof t=="number"?Number.isFinite(t)?t:null:ht(t)}function cn(t){return(t-Date.now())/864e5}function un(t){let e=ln(t);if(e===null)return"\u2014";let n=new Date(e);return Number.isNaN(n.getTime())?"\u2014":`${n.getFullYear()}-${W(n.getMonth()+1)}-${W(n.getDate())} ${W(n.getHours())}:${W(n.getMinutes())}`}function dn(t){let e=ln(t);if(e===null)return"\u2014";let n=new Date(e);return Number.isNaN(n.getTime())?"\u2014":`${n.getFullYear()}-${W(n.getMonth()+1)}-${W(n.getDate())}`}function l(t){if(t==null||t==="")return null;let e=typeof t=="number"?t:Number(t);return Number.isFinite(e)?e:null}function $(t,e=2){let n=l(t);return n===null?"\u2014":e===0?F.usd0.format(n):F.usd2.format(n)}function w(t,e=2){let n=l(t);return n===null?"\u2014":e===1?F.pct1.format(n):F.pct2.format(n)}function xt(){if(a.status?.portfolio)return{portfolio:a.status.portfolio,source:"live",freshnessMs:a.dataFreshness.statusMs??0};let t=a.portfolioSnapshot?.portfolio;return t&&Object.keys(t).length>0?{portfolio:t,source:"snapshot",freshnessMs:l(a.portfolioSnapshot?.freshness_ms)}:{portfolio:null,source:null,freshnessMs:null}}function To(t){let e=l(t);return e===null||e<0?null:Math.max(1,Math.round(e/6e4))}function Eo(){let t=xt();if(t.source==="live"){let e=l(a.dataFreshness.statusMs);if(e!==null&&e<3e4)return`<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-emerald-500/40 bg-emerald-500/10 text-emerald-200">${u("Live","\u5373\u6642")}</span>`}if(t.source==="snapshot"){let e=To(t.freshnessMs);return`<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-200">${e!==null?u(`Snapshot \xB7 ~${e}m ago`,`\u5FEB\u7167 \xB7 \u7D04 ${e} \u5206\u9418\u524D`):u("Snapshot","\u5FEB\u7167")}</span>`}return`<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-800/60 text-slate-400">${u("Loading\u2026","\u8F09\u5165\u4E2D\u2026")}</span>`}function Se(){if(!_)return;let t=document.getElementById("data-freshness-slot");t&&(t.innerHTML=Eo())}function gt(t,{indeterminate:e=!1}={}){let n=document.getElementById("investor-progress-bar");n&&(n.classList.toggle("hidden",!t),n.classList.toggle("investor-progress-bar--indeterminate",t&&e))}function xn(){let e=`<div class="overview-metrics-grid">${'<div class="skeleton-block h-16 rounded-lg"></div>'.repeat(8)}</div>`;return _?`<div class="investor-view-desktop">${e}</div><div class="investor-view-mobile"><div class="inv-dashboard">
      <div class="inv-panel skeleton-block" style="height:5.5rem"></div>
      <div class="inv-panel skeleton-block" style="height:4rem"></div>
      <div class="inv-panel skeleton-block" style="height:7rem"></div>
    </div></div>`:e}function gn(t){let{totalEquity:e,dayStart:n,dayPnl:o,dayDrawdown:r,openCredit:s,creditByStrategy:i,summary:c,winRate:d,avgHolding:p,sinceLine:f,lifetimePnl:m,lifetimeNativeByBook:v,closedCount:h,windowLabelDays:x,windowPnl:b,windowNativeByBook:C,lifetimeApr:T,windowApr:E,equityNativeByBook:L,equityUsdByBook:P}=t;return`
    <div class="overview-metrics-grid">
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Total equity","\u7E3D\u6B0A\u76CA")}</div>
        <div class="text-2xl font-mono">${$(e)}</div>
        <div class="text-[11px] text-slate-500">${u("USDC equivalent (all books)","USDC \u7D04\u7576\uFF08\u5168\u5E33\u672C\u5408\u8A08\uFF09")}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${Bn(L,P)}</div>
          <div class="overview-metric-line">${u("day-start","\u65E5\u521D")} ${$(n)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Day P&L","\u672C\u65E5\u640D\u76CA")}</div>
        <div class="text-2xl font-mono ${k(o)}">${$(o)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${u("drawdown","\u56DE\u64A4")} ${w(r)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Open credit","\u672A\u5BE6\u73FE\u6B0A\u5229\u91D1\uFF08\u9032\u5834\u6536\u6582\uFF09")}</div>
        <div class="text-2xl font-mono">${$(s)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${Wo(i)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Win rate \xB7 avg holding","\u52DD\u7387 \xB7 \u5E73\u5747\u6301\u6709")}</div>
        <div class="text-2xl font-mono">${c?`${w(d,1)} \xB7 ${S(p,2)}${B?" \u5929":"d"}`:"\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${c?f:u("Loading performance\u2026","\u7E3E\u6548\u6458\u8981\u8F09\u5165\u4E2D\u2026")}</div>
        </div>
      </div>

      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Total profit (lifetime)","\u7D2F\u8A08\u5DF2\u5BE6\u73FE\u640D\u76CA")}</div>
        <div class="text-2xl font-mono ${k(m)}">${c?$(m):"\u2014"}</div>
        <div class="overview-metric-meta">
          ${c?`<div class="overview-metric-line">${hn(v)}</div>`:""}
          <div class="overview-metric-line">${c?`${h??0} ${u("closed groups","\u7B46\u5DF2\u5E73\u5009\u90E8\u4F4D")}`:""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${qo(x)}</div>
        <div class="text-2xl font-mono ${k(b)}">${c?$(b):"\u2014"}</div>
        <div class="overview-metric-meta">
          ${c?`<div class="overview-metric-line">${hn(C)}</div>`:""}
          <div class="overview-metric-line">${c?Rn(x):""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Realized APR (lifetime)","\u5DF2\u5BE6\u73FE\u5E74\u5316\uFF08\u5B58\u7E8C\u671F\uFF09")}</div>
        <div class="text-2xl font-mono">${c?w(T):"\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${c?u("annualized on actual span","\u4F9D\u5BE6\u969B\u5340\u9593\u5E74\u5316"):""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${Go(x)}</div>
        <div class="text-2xl font-mono">${c?w(E):"\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line overview-metric-line--hint">${c?Dn(x):""}</div>
        </div>
      </div>
    </div>`}function pn(t,{pnl:e=!1,places:n={BTC:5,ETH:4,USDC:2}}={}){let o={BTC:"\u20BF",ETH:"\u25C6",USDC:"$"};return["BTC","ETH","USDC"].map(r=>{let s=l(t[r]),i=s===null?"\u2014":S(s,n[r]??4);return`<span class="inv-chip ${e?k(t[r]):""}"><span class="inv-chip-sym">${o[r]}</span><span class="inv-chip-val font-mono tabular-nums">${i}</span></span>`}).join("")}function Ro(t){return wt(new Set(H.map(e=>e.id))).map(e=>{let n=g(it(e).short),o=l(t[e]),r=o===null?"\u2014":$(o);return`<div class="inv-mini-row"><span class="inv-mini-label">${n}</span><span class="inv-mini-value font-mono tabular-nums">${r}</span></div>`}).join("")}function bn(t){let{totalEquity:e,dayStart:n,dayPnl:o,dayDrawdown:r,openCredit:s,creditByStrategy:i,summary:c,winRate:d,avgHolding:p,sinceLine:f,lifetimePnl:m,lifetimeNativeByBook:v,closedCount:h,windowLabelDays:x,windowPnl:b,windowNativeByBook:C,lifetimeApr:T,windowApr:E,equityNativeByBook:L,equityUsdByBook:P}=t,y=c!=null?`${w(d,1)} \xB7 ${S(p,2)}${B?" \u5929":"d"}`:"\u2014",R=c?f:u("Loading performance\u2026","\u7E3E\u6548\u6458\u8981\u8F09\u5165\u4E2D\u2026");return`<div class="inv-dashboard">
    <section class="inv-panel inv-panel--hero" aria-label="${u("Account snapshot","\u5E33\u6236\u5FEB\u7167")}">
      <div class="inv-split">
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Total equity","\u7E3D\u6B0A\u76CA")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${$(e)}</span>
          <span class="inv-kpi-foot">${u("USDC equivalent","USDC \u7D04\u7576")} \xB7 ${u("day-start","\u65E5\u521D")} ${$(n)}</span>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Day P&L","\u672C\u65E5\u640D\u76CA")}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${k(o)}">${$(o)}</span>
          <span class="inv-kpi-foot">${u("drawdown","\u56DE\u64A4")} ${w(r)}</span>
        </div>
      </div>
      <div class="inv-equity-dual">${Bn(L,P)}</div>
    </section>

    <section class="inv-panel" aria-label="${u("Open risk","\u672A\u5E73\u5009\u98A8\u96AA")}">
      <div class="inv-split">
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Open credit","\u672A\u5BE6\u73FE\u6B0A\u5229\u91D1")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${$(s)}</span>
          <div class="inv-mini-list">${Ro(i)}</div>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Win rate \xB7 hold","\u52DD\u7387 \xB7 \u6301\u6709")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${y}</span>
          <span class="inv-kpi-foot">${R}</span>
        </div>
      </div>
    </section>

    <section class="inv-panel" aria-label="${u("Realized performance","\u5DF2\u5BE6\u73FE\u7E3E\u6548")}">
      <h3 class="inv-panel-title">${u("Realized P&L","\u5DF2\u5BE6\u73FE\u640D\u76CA")}</h3>
      <div class="inv-compare">
        <div class="inv-compare-col">
          <span class="inv-compare-tag">${u("Lifetime","\u5B58\u7E8C")}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${k(m)}">${c?$(m):"\u2014"}</span>
          <div class="inv-chips-row inv-chips-row--compact">${c?pn(v,{pnl:!0}):""}</div>
          <span class="inv-kpi-foot">${c?`${h??0} ${u("closed","\u7B46\u5E73\u5009")}`:""}</span>
        </div>
        <div class="inv-compare-col">
          <span class="inv-compare-tag">${u("Last","\u8FD1")} ${x}${B?" \u65E5":"d"}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${k(b)}">${c?$(b):"\u2014"}</span>
          <div class="inv-chips-row inv-chips-row--compact">${c?pn(C,{pnl:!0}):""}</div>
          <span class="inv-kpi-foot">${c?Rn(x):""}</span>
        </div>
      </div>
      <div class="inv-split inv-split--apr">
        <div class="inv-kpi inv-kpi--compact">
          <span class="inv-kpi-label">${u("APR lifetime","\u5E74\u5316\xB7\u5B58\u7E8C")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${c?w(T):"\u2014"}</span>
        </div>
        <div class="inv-kpi inv-kpi--compact">
          <span class="inv-kpi-label">${u("APR","\u5E74\u5316")} ${x}${B?" \u65E5":"d"}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${c?w(E):"\u2014"}</span>
          <span class="inv-kpi-foot">${c?Dn(x):""}</span>
        </div>
      </div>
    </section>
  </div>`}function dt(t,e){let n=l(t);if(n===null)return"\u2014";let o=String(e||"").toUpperCase(),r='<span class="text-slate-500">',s="</span>";return o==="USDC"?`${r}($)${s}\xA0${S(n,4)}`:o==="BTC"?`${r}\u20BF${s}\xA0${S(n,5)}`:o==="ETH"?`${r}\u2666${s}\xA0${S(n,5)}`:S(n,4)}function Ce(t){if(!t)return null;let e=String(t.kind||"").toLowerCase(),n=String(t.direction||"").toLowerCase()==="sell";if(e==="option"){let s=l(t.size);return s===null||s===0?null:n?-Math.abs(s):Math.abs(s)}let o=l(t.size_currency);if(o!==null&&o!==0)return n&&o>0?-Math.abs(o):o;let r=l(t.size);return r===null||r===0?null:n&&r>0?-Math.abs(r):r}function Do(t,e,n=null){let o=n??a.groups;if(bt(e,o,t,"short")>1){let d=qt(t,"short");if(d!==null)return S(d,4)}let r=tt(e,t),s=Ce(r);if(s!==null)return S(s,4);let i=l(t.quantity);if(i===null)return"\u2014";let c=i>0?-Math.abs(i):i;return S(c,4)}function Z(t,e){return String(e==="long"?t?.long_instrument_name||"":t?.short_instrument_name||"")}function qt(t,e){let n=l(t.quantity);return n===null?null:e==="short"?-Math.abs(n):Math.abs(n)}function bt(t,e,n,o){let r=Z(n,o);if(!r)return 0;let s=String(n?.account_name||""),i=new Set,c=0;for(let d of[t?.trade_groups||[],e?.open||[]])for(let p of d){if(!Ht(p))continue;let f=yt(p);i.has(f)||(i.add(f),Z(p,o)===r&&(s&&String(p?.account_name||"")!==s||c++))}return c}function we(t,e,n){let o=Z(e,n);if(!o)return null;let r=t?.positions||[],s=String(e?.account_name||"");if(s){let i=r.find(c=>c.instrument_name===o&&String(c.account_name||"")===s);if(i)return i}return r.find(i=>i.instrument_name===o)||null}function tt(t,e){return we(t,e,"short")}function Bo(t,e,n=null){let o=n??a.groups,r=e.short_average_price,s=e.short_mark_price,i=e.short_floating_profit_loss,c=e.short_has_floating_profit_loss,d=e.short_floating_profit_loss_usd,p=e.short_has_floating_profit_loss_usd,f=r==null||r==="",m=s==null||s==="",v=i==null||i==="",h=d==null||d==="",x=bt(t,o,e,"short")>1;if((f||m||v||h||c===void 0||p===void 0)&&t?.positions?.length){let b=tt(t,e);b&&(f&&(r=b.average_price),m&&(s=b.mark_price),x||(v&&(i=b.floating_profit_loss),c===void 0&&(c=b.has_floating_profit_loss),h&&(d=b.floating_profit_loss_usd),p===void 0&&(p=b.has_floating_profit_loss_usd)))}return{...e,short_average_price:r,short_mark_price:s,short_floating_profit_loss:i,short_has_floating_profit_loss:c,short_floating_profit_loss_usd:d,short_has_floating_profit_loss_usd:p}}function $n(t){let e=t.expiration_timestamp_ms;if(e!=null&&e!==""){if(typeof e=="number"&&Number.isFinite(e))return Math.round(e);if(typeof e=="bigint")return Number(e);let n=String(e).trim();if(/^\d+$/.test(n)){let o=Number(n);return Number.isFinite(o)?o:null}}if(t.expiry){let n=ht(String(t.expiry));if(n!==null)return n}return null}function ke(t){let e=l(t.dte_days)??l(t.dte);if(e!==null)return e;let n=$n(t);return n===null?null:cn(n)}function et(t){let e=String(t.option_type||"").toLowerCase();if(e==="call")return"Call";if(e==="put")return"Put";let n=String(t.short_instrument_name||"");return/-C$/i.test(n)||n.endsWith("-C")?"Call":"Put"}function Sn(t,e){for(let n of["BTC","ETH"]){let o=l(t?.underlying_index_usd?.[n]),r=l(e?.underlying_index_usd?.[n]),s=o>0?o:r>0?r:null;s!==null&&(a.lastUnderlyingIndexUsd[n]=s)}}function Uo(t,e){let n={};for(let o of["BTC","ETH"]){let r=l(t?.underlying_index_usd?.[o]),s=l(e?.underlying_index_usd?.[o]),i=l(a.lastUnderlyingIndexUsd[o]),c=r>0?r:s>0?s:i>0?i:null;c!==null&&(n[o]=c)}return n}function Lo(t,e,n){let o=String(n||"").toUpperCase(),r=Uo(t,e);return l(r[o])}function q(t){let e=String(t.collateral_currency||"").toUpperCase();if(e==="BTC"||e==="ETH"||e==="USDC")return e;let n=String(t.short_instrument_name||"");return n.includes("_USDC-")?"USDC":n.startsWith("BTC-")?"BTC":n.startsWith("ETH-")?"ETH":String(t.currency||"").toUpperCase()||"BTC"}function Cn(t){let e=q(t);return e==="BTC"||e==="ETH"?e:String(t.currency||"BTC").toUpperCase()}function Ao(t,e){let n=q(e);if(n!=="BTC"&&n!=="ETH")return null;let o=n==="BTC"?"BTC-":"ETH-",r=t?.positions;if(!r?.length)return null;let s=String(e?.account_name||"");for(let i of r){if(s&&String(i.account_name||"")!==s)continue;let c=String(i.instrument_name||""),d=String(i.kind||"").toLowerCase();if(!c.startsWith(o)||d!=="option"&&d!=="future")continue;let p=l(i.index_price);if(p!==null&&p>0)return p}return null}function No(t,e,n){let o=Cn(t),r=l(a.lastSpotUsd[o]);if(r!==null&&r>0)return r;let s=Lo(e,n,o);if(s!==null&&s>0)return s;let i=tt(e,t),c=l(i?.index_price);if(c!==null&&c>0)return c;let d=Ao(e,t);return d!==null&&d>0?d:null}function zt(t,e,n){let o=q(t);if(o==="USDC")return 1;if(o==="BTC"||o==="ETH"){let r=No(t,e,n);return r!==null&&r>0?r:null}return null}function wn(t){return Ce(t)}function nt(t,e,n,o=null){let r=o??a.groups,s=qt(t,n);if(s!==null&&bt(e,r,t,n)>1)return s;let i=we(e,t,n),c=Ce(i);return c!==null?c:s}function ot(t,e,n,o){if(n==="short"&&Ee(t,`short_${o}`)){let s=t[`short_${o}`];if(s!=null&&s!=="")return s}return we(e,t,n)?.[o]??null}function Po(t,e,n,o=null){let r=l(ot(e,t,n,"average_price")),s=l(ot(e,t,n,"mark_price")),i=nt(e,t,n,o);return r===null||s===null||i===null?null:(s-r)*i}function ct(t,e,n,o){let r=Po(t,e,o,n);if(r===null)return null;let s=zt(e,t,n);return s===null||s<=0?null:r*s}function Io(t,e,n=null){let o=n??a.groups;if(bt(t,o,e,"short")>1){let d=tt(t,e);if(!d)return null;let p=l(d.average_price),f=l(d.mark_price),m=qt(e,"short");return p===null||f===null||m===null?null:(f-p)*m}let r=tt(t,e);if(!r)return null;let s=l(r.average_price),i=l(r.mark_price),c=wn(r);return s===null||i===null||c===null?null:(i-s)*c}function Oo(t,e){let n=Io(e,t);if(n!==null)return n;if(t.short_has_floating_profit_loss){let o=l(t.short_floating_profit_loss);if(o!==null)return o}return null}function Mo(t,e,n){let o=n??a.groups;if(bt(t,o,e,"short")>1){let p=tt(t,e);if(!p)return null;let f=l(p.average_price),m=l(p.mark_price),v=qt(e,"short");if(f===null||m===null||v===null)return null;let h=zt(e,t,n);return h===null||h<=0?null:(m-f)*v*h}let r=tt(t,e);if(!r)return null;let s=l(r.average_price),i=l(r.mark_price),c=wn(r);if(s===null||i===null||c===null)return null;let d=zt(e,t,n);return d===null||d<=0?null:(i-s)*c*d}function fn(t,e,n){let o=Mo(e,t,n);if(o!==null)return o;if(t.short_has_floating_profit_loss_usd){let r=l(t.short_floating_profit_loss_usd);if(r!==null)return r}return null}function Fo(t){let e=l(t);return e===null?"\u2014":`<span class="text-slate-500">($)</span>\xA0${new Intl.NumberFormat("en-US",{maximumFractionDigits:2,minimumFractionDigits:2}).format(e)}`}function mn(t){let e=l(t.unrealized_usdc_estimate);if(e!==null)return e;let n=l(t.entry_credit),o=l(t.current_debit);return n!==null&&o!==null?n-o:null}function zo(t,e,n){let o=ct(t,e,n,"short"),r=ct(t,e,n,"long");return o===null&&r===null?null:(o||0)+(r||0)}function Ho(t,e,n){let o=ct(t,e,n,"short"),r=ct(t,e,n,"long");return o===null||r===null?null:o+r}function $t(t,e,n){return A(t)==="bull_put_spread"?Ho(e,t,n)??mn(t)??zo(e,t,n)??fn(t,e,n):fn(t,e,n)??mn(t)}function st(t,e,n){return l(t.entry_credit)}function Gt(t,e){let n=String(e||"").toUpperCase();return n==="USDC"?t===null?"\u2014":Fo(t):t===null?"\u2014":n==="BTC"?`<span class="text-slate-500">\u20BF</span>\xA0${S(t,8)}`:n==="ETH"?`<span class="text-slate-500">\u2666</span>\xA0${S(t,8)}`:S(t,8)}function Te(t,e,n){if(A(t)!=="bull_put_spread")return Oo(t,e);let o=l(t.unrealized_coin_native);if(o!==null)return o;let r=$t(t,e,n),s=zt(t,e,n);return r===null||s===null||s<=0?null:r/s}function S(t,e=4){let n=l(t);return n===null?"\u2014":(e>=8?F.num8:F.num4).format(n)}function g(t){return String(t??"").replace(/[&<>"']/g,e=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[e])}function Ee(t,e){return Object.prototype.hasOwnProperty.call(t||{},e)}function St(t,e){let n=String(t||"").toUpperCase(),o=e?.portfolio||{},r=l(o?.equity_by_book?.[n]);if(r!==null)return r;let s=l(e?.accounts?.[n]?.equity);if(s===null)return null;if(n==="USDC")return s;let i=l(e?.underlying_index_usd?.[n])??l(a.lastSpotUsd?.[n]);return i===null||i<=0?null:s*i}function kn(t){let e={};for(let n of z)e[n]=St(n,t);return e}function Re(t,e,n){let o=l(t?.day_net_flow_usdc);return l(t?.day_pnl_usdc_ex_flow)??l(t?.day_pnl_usdc_ex_flow_ex_spot)??(e!==null&&n!==null?e-n-(o??0):null)}function Tn(t,e,n,o){let r=String(t||"").toUpperCase(),s=e?.portfolio||{},i=l(s?.day_net_flow_usdc_by_book?.[r]);return l(s?.day_pnl_usdc_ex_flow_by_book?.[r])??l(s?.day_pnl_usdc_ex_flow_ex_spot_by_book?.[r])??(n!==null&&o!==null?n-o-(i??0):null)}function k(t){let e=l(t);return e===null||e===0?"":e>0?"pnl-pos":"pnl-neg"}function vn(t){return un(t)}function En(t){return dn(t)}function qo(t){let e=Math.round(t??30);return u(`Total profit (rolling ${e}d)`,`\u5DF2\u5BE6\u73FE\u640D\u76CA\uFF08\u6EFE\u52D5 ${e} \u65E5\u8996\u7A97\uFF09`)}function Go(t){let e=Math.round(t??30);return u(`Realized APR (rolling ${e}d)`,`\u5DF2\u5BE6\u73FE\u5E74\u5316\uFF08\u6EFE\u52D5 ${e} \u65E5\u8996\u7A97\uFF09`)}function Rn(t){let e=Math.round(t??30);return u(`Closes in last ${e}d only`,`\u50C5\u8A08\u6700\u8FD1 ${e} \u65E5\u5167\u5E73\u5009`)}function Dn(t){let e=Math.round(t??30);return u(`Last ${e}d closes \xF7 ledger total equity`,`\u8FD1 ${e} \u65E5\u5E73\u5009 \xF7 \u7576\u65E5\u7E3D\u6B0A\u76CA`)}function Vt(){let t=l(a.status?.portfolio?.total_equity_usdc);return t!==null&&t>0?t:null}function J(t){let e=l(t.closed_timestamp_ms);if(e!==null)return e;if(t.closed_timestamp){let n=ht(String(t.closed_timestamp));if(n!==null)return n}return null}function Wt(t){let e=String(t.currency||"").toUpperCase()||"Option";if(A(t)==="bull_put_spread")return B?`${e} \u8CE3\u6B0A\u50F9\u5DEE`:`${e} put spread`;let o=et(t);if(B){let r=o.toLowerCase()==="call"?"\u8CB7\u6B0A":"\u8CE3\u6B0A";return`${e} \u8CE3\u51FA${r}`}return`${e} short ${o.toLowerCase()}`}function Vo(t,{places:e={BTC:5,ETH:4,USDC:2},pnl:n=!1}={}){let o={BTC:"\u20BF",ETH:"\u2666",USDC:"($)"};return`<span class="native-book-breakdown">${["BTC","ETH","USDC"].map(s=>{let i=l(t[s]),c=i===null?"\u2014":S(i,e[s]??4),d=n?` ${k(t[s])}`:"";return`<span class="native-book-item"><span class="native-book-symbol text-slate-500">${o[s]}</span> <span class="font-mono tabular-nums${d}">${c}</span></span>`}).join("")}</span>`}function hn(t){return Vo(t,{pnl:!0})}function Bn(t,e){let n={BTC:"\u20BF",ETH:"\u2666",USDC:"($)"},o={BTC:5,ETH:4,USDC:2},r=["BTC","ETH","USDC"].map(s=>{let i=l(t?.[s]),c=l(e?.[s]);if(i===null&&c===null)return null;if(s==="USDC"){let f=c??i;return f===null?null:`<div class="book-equity-dual-row">
          <span class="native-book-symbol text-slate-500">${n[s]}</span>
          <span class="font-mono tabular-nums">${$(f)}</span>
        </div>`}let d=i===null?"\u2014":S(i,o[s]),p=c===null?"\u2014":$(c);return`<div class="book-equity-dual-row">
        <span class="native-book-symbol text-slate-500">${n[s]}</span>
        <span class="font-mono tabular-nums">${d}</span>
        <span class="book-equity-dual-sep text-slate-600" aria-hidden="true">\xB7</span>
        <span class="font-mono tabular-nums text-slate-400">${p}</span>
      </div>`}).filter(Boolean);return r.length?`<div class="book-equity-dual-breakdown">${r.join("")}</div>`:'<span class="text-slate-500">\u2014</span>'}function Wo(t){return`<div class="open-credit-breakdown">${wt(new Set(H.map(n=>n.id))).map(n=>{let o=g(it(n).short),r=l(t[n]),s=r===null?"\u2014":$(r);return`<div class="open-credit-row"><span class="open-credit-label text-slate-500">${o}</span><span class="open-credit-value font-mono tabular-nums text-slate-300">${s}</span></div>`}).join("")}</div>`}function Un(t=30){let e=`/api/realized_summary?days=${t}`,n=Vt();return n!==null&&(e+=`&effective_capital_usdc=${encodeURIComponent(String(n))}`),e}function De(t=30,{sections:e=null}={}){let n=`/api/dashboard_bundle?days=${t}`;e&&(n+=`&sections=${encodeURIComponent(e)}`);let o=Vt();return o!==null&&(n+=`&effective_capital_usdc=${encodeURIComponent(String(o))}`),n}function Be(t){t?.groups&&(a.groups=t.groups),t?.status&&(a.status=t.status,a.statusErrorOnce=!1,a.dataFreshness.source="live",a.dataFreshness.live=!0,a.dataFreshness.statusMs=0),t?.realized_summary&&(a.report=t.realized_summary)}function Ln(t,e){let n=null,o=r=>{if(!r||l(r.realized_pnl)===null||!Jt(r,a.status,e))return;let s=rt(r);s===null||s<=0||(n===null||s<n)&&(n=s)};for(let r of e?.closed||[])o(r);for(let r of t?.recent_closed_trades||[])o(r);return n}function yn(t){if(!t||et(t).toLowerCase()!=="call")return!1;let e=l(t.covered_underlying_quantity);return e!==null&&e>0||String(t.short_label||"").startsWith("covered_call-")||String(t.account_name||"")==="covered_call"?!0:String(t.account_env_file||"").includes(".env.covered_call")}function X(t){let e=String(t||"").trim().toLowerCase().replaceAll("-","_").replaceAll(" ","_");return e?{naked:"naked_short",naked_put:"naked_short",naked_call:"naked_short",short_put:"naked_short",short_call:"naked_short",shortput:"naked_short",shortcall:"naked_short",naked_short_put:"naked_short",naked_short_call:"naked_short",put_spread:"bull_put_spread",short_put_spread:"bull_put_spread",bullputspread:"bull_put_spread",bull_put:"bull_put_spread",coveredcall:"covered_call"}[e]||e:""}function A(t){let e=X(t?.strategy),n=String(t?.long_instrument_name||"").trim();return(e===""||e==="naked_short")&&n&&et(t).toLowerCase()==="put"?"bull_put_spread":e==="naked_short"&&yn(t)?"covered_call":e||(et(t).toLowerCase()==="call"&&yn(t)?"covered_call":"naked_short")}function it(t){let e=X(t);if(V[e]){let o=V[e];return!_||!B?o:{...o,title:o.titleZh||o.title,short:o.shortZh||o.short,chipShort:o.chipShortZh||o.chipShort||o.shortZh||o.short,description:o.descriptionZh||o.description}}let n=e?e.replaceAll("_"," "):"\u2014";return{id:e||"",title:n,short:n,chipShort:n,accentClass:"border-slate-700",description:""}}function Yt(t){return it(t).title}function Yo(t){let e=X(t);return e==="naked_short"?"chip-strategy-naked":e==="bull_put_spread"?"chip-strategy-spread":e==="covered_call"?"chip-strategy-covered":"chip-strategy-unknown"}function Q(t,{compact:e=!1}={}){let n=it(t),o=Yo(n.id||t),r=e&&n.chipShort||n.short;return`<span class="chip ${o}${e?" chip--compact":""}">${g(r)}</span>`}function yt(t){return[String(t?.account_name||""),String(t?.group_id||""),String(t?.short_instrument_name||"")].join("\0")}var jo=["realized_pnl_collateral_native","short_entry_average_price","short_close_average_price","entry_index_usd","close_index_usd","realized_close_debit","realized_close_fee","entry_fee","entry_credit","collateral_currency","strategy","option_type","covered_underlying_quantity","realized_apr_on_equity","close_book_equity","quantity","realized_pnl","contract_size","short_strike"];function _n(t){return!(t==null||t===""||typeof t=="number"&&!Number.isFinite(t))}function Zo(t,e){let n={...e,...t};for(let o of jo)_n(t[o])?n[o]=t[o]:_n(e[o])&&(n[o]=e[o]);return n}function jt(t){let e=new Map;for(let n of t||[]){let o=yt(n),r=e.get(o);e.set(o,r?Zo(r,n):n)}return[...e.values()]}function Ht(t){return String(t?.status||"open").toLowerCase()!=="closed"}function Zt(t){return String(t?.status||"").toLowerCase()==="closed"?!0:J(t)!==null}var Jo=3e5;function Ko(t,e){let n=new Set;for(let o of Ct(t,e)){let r=String(o?.short_instrument_name||"").trim();r&&n.add(r)}return n}function Xo(t,e,n){if(!Zt(t)||String(t?.close_reason||"").toLowerCase()!=="reconciled_external")return!1;let o=rt(t),r=J(t);if(o===null||r===null||r<=o||r-o>Jo)return!1;let s=String(t?.short_instrument_name||"").trim();return s?Ko(e,n).has(s):!1}function Jt(t,e,n){return Zt(t)&&!Xo(t,e,n)}function Ct(t,e){let n=[],o=new Set;for(let r of t?.trade_groups||[]){if(!Ht(r))continue;let s=yt(r);o.has(s)||(o.add(s),n.push(r))}for(let r of e?.open||[]){if(!Ht(r))continue;let s=yt(r);o.has(s)||(o.add(s),n.push(r))}return n.map(r=>Bo(t,r,e))}function An(t,e,n=20,o=null){let r=o??a.status,s=jt([...e?.closed||[],...t?.recent_closed_trades||[]]).filter(i=>Jt(i,r,e));return s.sort((i,c)=>(J(c)||0)-(J(i)||0)),s.slice(0,n)}function Ue(t,e){return An(t,e,500)}function wt(t){let e=H.map(r=>r.id),n=e.filter(r=>t.has(r)),o=[...t].filter(r=>!e.includes(r)).sort();return n.concat(o)}function Qo(t){let e=String(t||"").match(/-([0-9]+(?:\.[0-9]+)?)-[CP]$/i);return e?l(e[1]):null}function K(t,e){let n=l(e==="long"?t?.long_strike:t?.short_strike);return n!==null?n:Qo(Z(t,e))}function pt(t){let e=l(t);return e===null?"\u2014":$(e,0)}function Le(t){let e=K(t,"short"),n=K(t,"long");return e===null||n===null?null:e-n}function Kt(t,e,n){let o=l(ot(t,e,"short",n)),r=l(ot(t,e,"long",n));return o===null||r===null?null:o-r}function Ae(t){let e=String(t?.long_instrument_name||"").trim();if(e)return u(`Long ${e}`,`\u8CB7\u817F ${e}`);let n=l(t?.covered_underlying_quantity);return n!==null&&n>0?u(`Covered ${S(n,4)} ${String(t.currency||"").toUpperCase()}`,`\u5099\u514C ${S(n,4)} ${String(t.currency||"").toUpperCase()}`):u("Single short leg","\u55AE\u908A\u8CE3\u51FA")}function _t(t){let e=String(t?.account_name||"").trim();return e?`Account ${e}`:""}function kt(t){let e=l(t?.holding_days);if(e!==null)return e;let n=J(t),o=rt(t);return n===null||o===null||o<=0?null:Math.max(n-o,0)/864e5}function rt(t){let e=l(t?.entry_timestamp_ms);if(e!==null)return e;if(t?.entry_timestamp){let n=ht(String(t.entry_timestamp));if(n!==null)return n}return null}function tr(t){let e=rt(t),n=$n(t);return e===null||n===null||n<=e?null:(n-e)/864e5}function Ne(t,e){let n=String(e||"USDC").toUpperCase(),o=l(t?.accounts?.[n]?.equity);return o===null||o<=0?null:o}function U(t){return q(t)}function Tt(t,e){let n=U(t);return n==="USDC"?null:l(e?.underlying_index_usd?.[n])??l(a.groups?.underlying_index_usd?.[n])??l(a.lastSpotUsd?.[n])??l(t?.close_index_usd)}function Pe(t,e){let n=l(t?.realized_pnl_collateral_native);if(n!==null)return n;if(U(t)==="USDC")return l(t?.realized_pnl);let r=l(t?.quantity);if(r===null||r<=0)return null;let s=l(t?.entry_index_usd),i=l(t?.close_index_usd)??s,c=l(t?.entry_fee)??0,d=l(t?.realized_close_fee)??0,p=null,f=null,m=l(t?.short_entry_average_price),v=l(t?.short_close_average_price),h=l(t?.entry_credit),x=l(t?.realized_close_debit);if(m!==null&&m>0?(p=m*r,(s===null||s<=0)&&h!==null&&(s=(h+c)/(m*r))):h!==null&&s!==null&&s>0&&(p=(h+c)/s),v!==null&&v>0?(f=v*r,(i===null||i<=0)&&x!==null&&(i=Math.max(0,x-d)/(v*r))):x!==null&&i!==null&&i>0&&(f=Math.max(0,x-d)/i),p===null||f===null)return null;let b=0;if(c>0){if(s===null||s<=0)return null;b+=c/s}if(d>0){if(i===null||i<=0)return null;b+=d/i}return p-f-b}function er(t){let e=U(t);return e==="BTC"||e==="ETH"}function Xt(t,e){if(U(t)==="USDC")return l(t?.realized_pnl);let o=Pe(t,e),r=Tt(t,e);return o!==null&&r!==null&&r>0?o*r:null}function Et(t,e){let n=U(t);if(n==="USDC")return l(t?.realized_pnl);let o=Pe(t,e);if(o!==null)return o;let r=l(t?.realized_pnl);if(r===null)return null;let s=l(t?.close_index_usd)??l(e?.underlying_index_usd?.[n])??l(a.groups?.underlying_index_usd?.[n])??l(a.lastSpotUsd?.[n]);return s===null||s<=0?null:r/s}function nr(t){let e=l(t?.contract_size);return e!==null&&e>0?e:1}function or(t,e){let n=l(t?.quantity);if(n===null||n<=0)return null;let o=nr(t),r=A(t),s=l(t?.estimated_im_collateral);if(r==="bull_put_spread"&&s!==null&&s>0)return s/n;if(U(t)==="USDC"){if(et(t).toLowerCase()==="call"){let d=sr(t,e)??ee(t,e)??Tt(t,e)??On(t,e);if(d!==null&&d>0)return d}else{let d=K(t,"short");if(d!==null&&d>0)return d}return null}return o}function Nn(t,e){let n=or(t,e),o=l(t?.quantity);if(n===null||o===null||o<=0)return null;let r=A(t);if(r==="covered_call"){let i=l(t?.covered_underlying_quantity);return i!==null&&i>0?i:o}return U(t)==="USDC"||r==="bull_put_spread",n*o}function Qt(t,e){return Nn(t,e)}function Pn(t,e){let n=Et(t,e),o=kt(t),r=Qt(t,e);return n===null||r===null||r<=0||o===null||o<=0?null:n/r*(365/o)}function rr(t,e){let n=U(t);if(!er(t)){let c=l(t?.realized_pnl);return c===null?"\u2014":$(c)}let o=Pe(t,e);if(o===null){let c=l(t?.realized_pnl);return c===null?"\u2014":$(c)}let r=Xt(t,e),i=`${S(o,n==="BTC"?5:4)} ${n}`;return B?`${$(r)}\uFF08${i}\uFF09`:`${$(r)} (${i})`}function In(t,e){let n=l(t);return n===null?`\u2014 ${e||""}`.trim():`${new Intl.NumberFormat("en-US",{maximumFractionDigits:8}).format(n)} ${e}`}function Ft(t,e,n){let o=$(t);if(e===null||!n||n==="USDC")return o;let r=In(e,n);return B?`${o}\uFF08${r}\uFF09`:`${o} (${r})`}function te(t,e,n){let o=$(t);if(e===null||!n||n==="USDC")return o;let r=g(In(e,n));return`<span class="open-position-value-stack"><span class="open-position-value-line">${o}</span><span class="open-position-value-sub">${r}</span></span>`}function Ie(t,e){let n=l(t),o=l(e);return n===null||o===null||o<=0?null:n/o}function ee(t,e){let n=U(t);return n==="USDC"?null:l(t?.entry_index_usd)??l(e?.underlying_index_usd?.[n])??l(a.groups?.underlying_index_usd?.[n])??l(a.lastSpotUsd?.[n])}function On(t,e){let n=U(t);return n==="USDC"?null:l(t?.close_index_usd)??l(e?.underlying_index_usd?.[n])??l(a.groups?.underlying_index_usd?.[n])??l(a.lastSpotUsd?.[n])??l(t?.entry_index_usd)}function sr(t,e){let n=Cn(t);if(n!=="BTC"&&n!=="ETH")return null;let o=[l(t?.entry_index_usd),l(t?.close_index_usd),l(e?.underlying_index_usd?.[n]),l(a.groups?.underlying_index_usd?.[n]),l(a.lastSpotUsd?.[n]),K(t,"short")];for(let r of o)if(r!==null&&r>100)return r;return null}function Oe(t,e){let n=kt(t);if(n===null||n<=0)return null;let o=l(t?.realized_apr_on_equity)??l(t?.realized_annualized_return);return o!==null?o:Pn(t,e)}function ir(t,e,n=null){let o=n??a.groups;if(A(t)==="bull_put_spread"){let i=nt(t,e,"short",o),c=nt(t,e,"long",o);if(i===null&&c===null){let p=l(t.quantity);return p===null?null:`${S(-Math.abs(p),4)} / ${S(Math.abs(p),4)}`}let d=[];return i!==null&&d.push(S(i,4)),c!==null&&d.push(S(c,4)),d.length?d.join(" / "):null}if(!Zt(t)){let i=Do(t,e,o);return i==="\u2014"?null:i}let s=l(t.quantity);return s===null?null:S(-Math.abs(s),4)}function ar(t,e){let n=l(t?.entry_credit);if(n===null)return null;let o=l(t?.entry_fee)??0,r=U(t),s=l(t?.short_entry_average_price),i=l(t?.quantity),c=ee(t,e),d=n;if(o>0&&s!==null&&s>0&&i!==null&&i>0&&c!==null&&c>0){let p=s*i*c,f=Math.max(.01,Math.abs(p)*.001);Math.abs(p-n)<=f?d=n-o:Math.abs(p-(n+o))<=f&&(d=n)}return r==="USDC"?d:c===null||c<=0?null:d/c}function ne(t,e){let n=tr(t),o=Nn(t,e),r=ar(t,e);return r===null||r<=0||n===null||n<=0||o===null||o<=0?l(t?.entry_net_apr):r/o*(365/n)}function oe(t){return l(t?.entry_fee)}function Me(t,e){return Ie(oe(t),ee(t,e))}function re(t){let e=l(t?.current_close_fee);return e!==null&&e>0?e:l(t?.realized_close_fee)}function Fe(t,e){let n=l(t?.current_close_fee),o=n!==null&&n>0?Tt(t,e):On(t,e);return Ie(re(t),o)}function se(t,e){return Ie(l(t?.entry_credit),ee(t,e))}function lr(t,e){let n=[],o=new Set,r=s=>{if(!s)return;let i=yt(s);o.has(i)||(o.add(i),n.push(s))};for(let s of t?.trade_groups||[])r(s);for(let s of e?.open||[])r(s);for(let s of e?.closed||[])r(s);return n}function Mn(t,e){return jt(lr(t,e)).filter(n=>Ht(n)).sort((n,o)=>(rt(o)||0)-(rt(n)||0))}function Fn(t,e,n){return An(e,n,500,t)}function ze(t,e,n){let o=t.length,r=Math.max(1,Math.ceil(o/n)),s=Math.min(Math.max(1,e),r),i=(s-1)*n;return{rows:t.slice(i,i+n),page:s,totalPages:r,total:o,start:o?i+1:0,end:Math.min(i+n,o)}}function He(t,e){let{page:n,totalPages:o,total:r,start:s,end:i}=e;if(r<=vt)return"";let c=n<=1,d=n>=o,p=u(`${s}\u2013${i} of ${r} \xB7 page ${n} of ${o}`,`${s}\u2013${i} / \u5171 ${r} \u7B46 \xB7 \u7B2C ${n} / ${o} \u9801`);return`<div class="activity-pagination" data-activity-section="${g(t)}">
      <button type="button" class="filter-chip activity-page-btn" data-activity-section="${g(t)}" data-direction="prev"${c?" disabled":""}>${u("Prev","\u4E0A\u4E00\u9801")}</button>
      <span class="activity-pagination-label">${g(p)}</span>
      <button type="button" class="filter-chip activity-page-btn" data-activity-section="${g(t)}" data-direction="next"${d?" disabled":""}>${u("Next","\u4E0B\u4E00\u9801")}</button>
    </div>`}function cr(t){let e=String(t?.currency||"").toUpperCase()||"Option",n=String(t?.short_instrument_name||"");if(n){let o=n.split("-").slice(-2).join(" ");return`${e} ${o}`.trim()}try{return Wt(t)}catch{return`${e} trade`}}function $e(t){return t.filter(e=>e).map(e=>typeof e=="string"?`<span>${g(e)}</span>`:`<span>${g(e[0])} <strong>${g(String(e[1]))}</strong></span>`).join("")}function zn(t,e,n){let o=A(t),r=U(t)||"\u2014",s=ne(t,e),i=oe(t),c=re(t),d=l(t.entry_credit),p=Me(t,e),f=Fe(t,e),m=se(t,e),v=rt(t),h=Zt(t),x=Xt(t,e),b=kt(t),C=h?Oe(t,e):null,T=ir(t,e,n),E=cr(t),L=d===null?"\u2014":Ft(d,m,r),P=s===null?"\u2014":w(s,1),y=i===null?null:Ft(i,p,r),R=[[u("Opened","\u958B\u5009"),vn(v)],T!==null?[u("Amount","\u6578\u91CF"),T]:null,y?[u("Entry fee","\u9032\u5834\u624B\u7E8C\u8CBB"),y]:null].filter(Boolean),O=`<div class="activity-entry-metrics">
      <div class="activity-entry-metric">
        <span class="activity-entry-metric-label">${u("Credit","\u6536\u6B0A\u5229\u91D1")}</span>
        <span class="activity-entry-metric-value ${k(d)}">${g(L)}</span>
      </div>
      <div class="activity-entry-metric">
        <span class="activity-entry-metric-label">${u("Net APR","\u6DE8\u5E74\u5316\u5831\u916C\u7387")}</span>
        <span class="activity-entry-metric-value ${k(s)}">${g(P)}</span>
      </div>
    </div>
    <div class="activity-phase-meta activity-phase-meta-secondary">
      ${$e(R)}
    </div>`,I="";if(h){let j=[[u("Closed","\u5E73\u5009"),vn(J(t))],c!==null?[u("Close fee","\u5E73\u5009\u624B\u7E8C\u8CBB"),Ft(c,f,r)]:null,b!==null?[u("Held","\u6301\u6709"),`${S(b,1)}${B?" \u5929":"d"}`]:null].filter(Boolean),_e=x!==null?`<span class="activity-closed-pnl-value ${k(x)}">${rr(t,e)}</span>`:'<span class="activity-closed-pnl-value activity-closed-pnl-value-missing">\u2014</span>',Ot=C!==null?`<span class="activity-closed-pnl-value ${k(C)}">${w(C,1)}</span>`:'<span class="activity-closed-pnl-value activity-closed-pnl-value-missing">\u2014</span>';I=`${`<div class="activity-closed-metrics">
        <div class="activity-closed-pnl">
          <span class="activity-closed-pnl-label">${u("Realized PnL","\u5DF2\u5BE6\u73FE\u640D\u76CA")}</span>
          ${_e}
        </div>
        <div class="activity-closed-pnl">
          <span class="activity-closed-pnl-label">${u("Realized APR","\u5BE6\u73FE\u5E74\u5316\u5831\u916C")}</span>
          ${Ot}
        </div>
      </div>`}<div class="activity-phase-meta activity-phase-meta-secondary">${$e(j)}</div>`}else{let j=[c!==null?[u("Est. close fee","\u9810\u4F30\u5E73\u5009\u8CBB"),Ft(c,f,r)]:null].filter(Boolean);I=`<div class="activity-phase-meta">
        <span class="activity-status-pill is-open">${u("Open","\u6301\u5009\u4E2D")}</span>
        ${j.length?$e(j):`<span>${u("Est. close fee","\u9810\u4F30\u5E73\u5009\u8CBB")} <strong>\u2014</strong></span>`}
      </div>`}let Y=!_&&_t(t)?_t(t):"";return`
    <li class="activity-card">
      <div class="activity-card-head">
        ${Q(o)}
        <span class="activity-card-title">${g(E)}</span>
        <span class="text-[11px] text-slate-500">${g(r)}</span>
        ${Y?`<span class="text-[11px] text-slate-500">${g(Y)}</span>`:""}
      </div>
      <div class="activity-card-instrument">${g(t.short_instrument_name||"")}</div>
      <div class="activity-lifecycle">
        <div class="activity-phase activity-phase-entry">
          <div class="activity-phase-label">${u("Entry","\u9032\u5834")}</div>
          ${O}
        </div>
        <div class="activity-phase-divider" aria-hidden="true"></div>
        <div class="activity-phase activity-phase-exit">
          <div class="activity-phase-label">${u("Exit","\u51FA\u5834")}</div>
          ${I}
        </div>
      </div>
    </li>`}function M(t,e){let n=document.getElementById(t);n&&(n.textContent=e)}function D(t){let e=document.getElementById("toast");e&&(e.textContent=t,e.classList.remove("hidden"),clearTimeout(D._t),D._t=setTimeout(()=>e.classList.add("hidden"),5e3))}function ut(t){return new Promise(e=>setTimeout(e,t))}async function Hn(t,e){let n=0,o=Math.max(1,Math.min(e||1,t.length));async function r(){for(;;){let s=n++;if(s>=t.length)break;await t[s]()}}await Promise.all(Array.from({length:o},()=>r()))}async function N(t,e={}){let n=tn(t),o=sn+1;for(let r=0;r<o;r++){let s;try{s=await fetch(n,e)}catch(c){if(r<o-1){await ut(be*(r+1));continue}throw c}if(s.ok)return s.json();let i=`${s.status} ${s.statusText}`;try{let c=await s.json();c?.detail&&(i=`${s.status} ${c.detail}`)}catch{}if(rn.has(s.status)&&r<o-1){await ut(be*(r+1));continue}throw new Error(i)}}function le(){return{responsive:!0,maintainAspectRatio:!1,animation:!1,interaction:{mode:"nearest",intersect:!1},plugins:{legend:{labels:{color:"rgb(203 213 225)",boxWidth:12,padding:8}},tooltip:{backgroundColor:"rgba(15,23,42,0.95)",borderColor:"rgb(51,65,85)",borderWidth:1,titleColor:"rgb(226,232,240)",bodyColor:"rgb(226,232,240)"}},scales:{x:{type:"time",time:{tooltipFormat:"yyyy-LL-dd HH:mm"},grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)"}},y:{grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)"}}}}}function Bt(t){let e=a.charts[t];if(!e)return;let n=e.canvas;e.destroy(),a.charts[t]=null,n&&(n.removeAttribute("width"),n.removeAttribute("height"),n.style.width="",n.style.height="")}function Ut(t){let e=document.getElementById(t);return e?e.getContext("2d"):null}function ie(){Object.values(a.charts).forEach(t=>{try{t?.resize?.()}catch{}})}function Lt(){requestAnimationFrame(()=>{ie(),window.setTimeout(ie,80),window.setTimeout(ie,320)})}var qn=!1;function jn(){qn||typeof ResizeObserver>"u"||(qn=!0,document.querySelectorAll(".chart-panel-canvas").forEach(t=>{t.querySelector("canvas")?.id&&new ResizeObserver(()=>ie()).observe(t)}))}function ce(){let t=`/api/apr_series?window_days=${a.aprWindow}`,e=Vt();return e!==null&&(t+=`&effective_capital_usdc=${encodeURIComponent(String(e))}`),t}function Zn(){let t=luxon.DateTime.now().toUTC().startOf("day");return{min:t.minus({days:Math.max(a.aprWindow,30)}).toMillis(),max:t.toMillis()}}function dr(t){let e=document.getElementById(t);return e?e.closest(".chart-panel-canvas")||e.parentElement:null}function At(t,{empty:e,message:n=""}={}){let o=dr(t);if(!o)return;let r=o.querySelector(".chart-empty-overlay");if(!e){r?.remove(),o.classList.remove("chart-panel-canvas--empty");return}o.classList.add("chart-panel-canvas--empty"),r||(r=document.createElement("div"),r.className="chart-empty-overlay",o.appendChild(r)),r.textContent=n}var Gn={realized:{en:"No closed positions yet \u2014 this chart fills in after the first close.",zh:"\u5C1A\u7121\u5E73\u5009\u7D00\u9304 \u2014 \u9996\u6B21\u5E73\u5009\u5F8C\u6B64\u5716\u8868\u624D\u6703\u958B\u59CB\u7D2F\u7A4D\u3002"},apr:{en:"Rolling APR needs closed trades and daily equity snapshots.",zh:"\u6EFE\u52D5\u5E74\u5316\u9700\u6709\u5E73\u5009\u7D00\u9304\u8207\u6BCF\u65E5\u6B0A\u76CA\u5FEB\u7167\u3002"}};function pr(t){let e=Gn[t]||Gn.realized;return u(e.en,e.zh)}function fr({yPercent:t=!1,chartType:e="line"}={}){let n=Zn(),o=le(),r=t?-.1:-50,s=t?.1:50;return{...o,plugins:{...o.plugins,legend:{display:!1},tooltip:{enabled:!1}},scales:{x:{...o.scales.x,...n,display:!0,offset:e==="bar",time:{unit:"day",round:"day",tooltipFormat:"yyyy-LL-dd"}},y:{...o.scales.y,display:!0,min:r,max:s,ticks:{...o.scales.y.ticks,maxTicksLimit:6,...t?{callback:i=>w(i,1)}:{}}}}}}function Dt(t,e,{yPercent:n=!1,chartType:o="line",messageKind:r="realized"}={}){let s=Ut(t);if(!s)return;Bt(e),At(t,{empty:!0,message:pr(r)});let i=Zn(),c=[{x:i.min,y:0},{x:i.max,y:0}];a.charts[e]=new Chart(s,{type:"line",data:{datasets:[{label:u("No realized history yet","\u5C1A\u7121\u5DF2\u5BE6\u73FE\u7D00\u9304"),data:c,borderWidth:1,pointRadius:0,borderColor:"rgba(148, 163, 184, 0.35)",backgroundColor:"transparent"}]},options:fr({yPercent:n,chartType:o})})}function qe(){return a.bookFilter==="ALL"?z:[a.bookFilter]}function Jn(t,e,n){let o=Object.fromEntries(H.map(r=>[r.id,0]));for(let r of t||[]){let s=A(r);if(!V[s])continue;let i=st(r,e,n);i!==null&&(o[s]+=i)}return o}function Kn(t,e,n=null){let o=n??a.status;return jt([...e?.closed||[],...t?.recent_closed_trades||[]]).filter(r=>Jt(r,o,e)).filter(r=>l(r?.realized_pnl)!==null)}function Xn(t,e,n){let o={BTC:0,ETH:0,USDC:0};for(let r of Kn(t,e)){let s=U(r);if(s!=="BTC"&&s!=="ETH"&&s!=="USDC")continue;let i=Et(r,n);i!==null&&(o[s]+=i)}return o}function Qn(t,e,n,o){let r={BTC:0,ETH:0,USDC:0},s=o??30,i=Date.now()-s*24*3600*1e3;for(let c of Kn(t,e)){let d=J(c);if(d===null||d<i)continue;let p=U(c);if(p!=="BTC"&&p!=="ETH"&&p!=="USDC")continue;let f=Et(c,n);f!==null&&(r[p]+=f)}return r}function to(t){let e={},n=!1;for(let o of z){let r=Ne(t,o);e[o]=r,r!==null&&(n=!0)}if(!n){let{portfolio:o}=xt();for(let r of z){if(r==="USDC"){e[r]=l(o?.equity_by_book?.[r]);continue}let s=l(o?.equity_by_book?.[r]),i=l(t?.underlying_index_usd?.[r])??l(a.lastSpotUsd?.[r]);e[r]=s!==null&&i!==null&&i>0?s/i:null}}return e}function mr(){return{responsive:!0,maintainAspectRatio:!1,animation:!1,interaction:{mode:"index",intersect:!1},plugins:{legend:{labels:{color:"rgb(203 213 225)",boxWidth:12,padding:8}},tooltip:{backgroundColor:"rgba(15,23,42,0.95)",borderColor:"rgb(51,65,85)",borderWidth:1,titleColor:"rgb(226,232,240)",bodyColor:"rgb(226,232,240)"}},scales:{x:{grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)"}},y:{beginAtZero:!0,grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)",maxTicksLimit:8}}}}}function ue(){let t=Ut("chart-risk-capital");if(!t)return;Bt("riskCapital");let e=qe(),n=a.status?.portfolio,o=e.map(p=>{let f=St(p,a.status);return f!==null?f:0}),r=l(n?.total_equity_usdc),s=o.reduce((p,f)=>p+f,0),i=u(`Total ${$(r)}`,`\u5408\u8A08 ${$(r)}`);r!==null&&s>0&&Math.abs(s-r)>1?i+=u(" \xB7 bars sum may differ from headline"," \xB7 \u5404\u5E33\u52A0\u7E3D\u53EF\u80FD\u8207\u7E3D\u89BD\u7565\u6709\u5DEE\u7570"):a.status||(i=u("Awaiting live snapshot","\u7B49\u5F85\u5373\u6642\u5FEB\u7167")),M("risk-capital-meta",i),M("risk-capital-hint",u("Per-book equity in USDC equivalent from the live snapshot (or last saved snapshot).","\u5404\u5E33\u672C\u6B0A\u76CA\u4EE5 USDC \u7D04\u7576\u986F\u793A\uFF0C\u4F86\u81EA\u5373\u6642\u6216\u6700\u8FD1\u5FEB\u7167\u3002"));let c=e.map(p=>lt[p]||"#94a3b8"),d=mr();At("chart-risk-capital",{empty:!1}),a.charts.riskCapital=new Chart(t,{type:"bar",data:{labels:e,datasets:[{label:u("Book equity (USDC eq.)","\u5E33\u672C\u6B0A\u76CA\uFF08USDC \u7D04\u7576\uFF09"),data:o,backgroundColor:c.map(p=>p+"cc"),borderColor:c,borderWidth:1}]},options:{...d,plugins:{...d.plugins,tooltip:{...d.plugins.tooltip,callbacks:{afterBody(p){if(!p?.length)return"";let f=p[0].dataIndex;if(f===void 0)return"";let m=o[f]??0,v=r>0?m/r:null;return[`${u("Share of total: ","\u4F54\u7E3D\u6B0A\u76CA\uFF1A")}${w(v,2)}`]}}}}}})}var ae=864e5;function ft(t){let e=luxon.DateTime.fromISO(String(t||"").trim(),{zone:"utc"});return e.isValid?e.toMillis():NaN}function Vn(t){let e=t.filter(n=>Number.isFinite(n.x)&&n.y!==null&&Number.isFinite(n.y)).sort((n,o)=>n.x-o.x);if(e.length===0)return[];if(e.length===1){let n=e[0];return[{x:n.x-ae,y:0},{x:n.x,y:n.y},{x:n.x+ae,y:n.y}]}return e}function Rt(t){return t.filter(e=>Number.isFinite(e.x)&&e.y!==null&&Number.isFinite(e.y)).sort((e,n)=>e.x-n.x)}function eo(t){let e=Rt(t);if(e.length===0)return[];if(e.length===1){let n=e[0];return[n,{x:n.x+ae,y:n.y}]}return e}function no(t){let e=(t||[]).map(i=>i.x).filter(Number.isFinite);if(!e.length)return{};let n=Math.min(...e),o=Math.max(...e),r=o-n,s=ae;return e.length===1||r<s*.25?{min:n-s,max:o+s}:{}}function de(){let t=Ut("chart-cum-pnl");if(!t)return;Bt("cumPnl");let e=a.cumulativePnl,n=e?.realized_count?`${e.realized_count} closed groups`:u("no closed groups","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44");if(M("cum-pnl-meta",n),!e){Dt("chart-cum-pnl","cumPnl");return}let o=[],r=qe();for(let s of r){let i=e.cumulative_by_book?.[s]||[];if(i.length){let c=Vn(i.map(d=>({x:ft(d.date),y:l(d.pnl_usdc)})));c.length&&o.push({label:`${s} cum. PnL`,data:c,borderColor:lt[s],backgroundColor:lt[s]+"22",stepped:!0,pointRadius:0,borderWidth:2})}}if(a.bookFilter==="ALL"&&e.cumulative_total?.length){let s=Vn(e.cumulative_total.map(i=>({x:ft(i.date),y:l(i.pnl_usdc)})));s.length&&o.push({label:"Total cum. PnL",data:s,borderColor:lt.TOTAL,backgroundColor:lt.TOTAL+"22",stepped:!0,pointRadius:0,borderWidth:2,borderDash:[4,4]})}if(!o.length){Dt("chart-cum-pnl","cumPnl");return}At("chart-cum-pnl",{empty:!1}),a.charts.cumPnl=new Chart(t,{type:"line",data:{datasets:o},options:le()})}function vr(t){return t.filter(e=>Math.abs(e.y)>1e-12)}var hr="rgba(52, 211, 153, 0.67)",yr="#34d399",_r="rgba(251, 113, 133, 0.67)",xr="#fb7185";function Wn(t){return t.map(e=>{let n=l(e.y)??0;return n>0?hr:n<0?_r:"rgba(148, 163, 184, 0.4)"})}function Yn(t){return t.map(e=>{let n=l(e.y)??0;return n>0?yr:n<0?xr:"#94a3b8"})}function pe(){let t=Ut("chart-daily-pnl");if(!t)return;Bt("dailyPnl");let e=30,n=a.cumulativePnl;if(!n){M("daily-pnl-meta",u("no closed groups","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44")),Dt("chart-daily-pnl","dailyPnl",{chartType:"bar"});return}let o=qe(),r=(n.daily_total||[]).filter(m=>Number.isFinite(ft(m.date))),s=n?.daily_total?.length?`${n.daily_total.length} ${u("active days","\u500B\u6709\u6548\u4EA4\u6613\u65E5")}`:u("no closed groups","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44");a.bookFilter==="ALL"&&r.length>=e&&(s+=" \xB7 30d SMA"),M("daily-pnl-meta",s);let i=m=>({x:ft(m.date),y:l(m.pnl_usdc)}),c=[];if(a.bookFilter==="ALL"){let m=Rt((n.daily_total||[]).map(i));m.length&&c.push({type:"bar",label:u("Daily total","\u6BCF\u65E5\u5408\u8A08"),data:m,order:1,backgroundColor:Wn(m),borderColor:Yn(m),borderWidth:1})}else for(let m of o){let v=n.daily_by_book?.[m]||[],h=Rt(v.map(i));h=vr(h),h.length&&c.push({type:"bar",label:`${m} ${u("daily","\u6BCF\u65E5")}`,data:h,order:1,backgroundColor:Wn(h),borderColor:Yn(h),borderWidth:1})}if(a.bookFilter==="ALL"&&r.length>=e){let m=[];for(let h=e-1;h<r.length;h++){let x=0;for(let b=h-e+1;b<=h;b++)x+=l(r[b].pnl_usdc)||0;m.push({x:ft(r[h].date),y:x/e})}let v=eo(Rt(m));v.length&&c.push({type:"line",label:`30d SMA (${e}-day realized avg.)`,data:v,order:2,borderColor:"#f472b6",backgroundColor:"#f472b633",tension:.15,pointRadius:0,borderWidth:2})}if(!c.length){Dt("chart-daily-pnl","dailyPnl",{chartType:"bar"});return}At("chart-daily-pnl",{empty:!1});let d=c.flatMap(m=>m.data||[]),p=no(d),f=le();a.charts.dailyPnl=new Chart(t,{type:"bar",data:{datasets:c},options:{...f,scales:{x:{...f.scales.x,...p,offset:!0,time:{unit:"day",tooltipFormat:"yyyy-LL-dd"}},y:{...f.scales.y,ticks:{...f.scales.y.ticks,maxTicksLimit:10}}}}})}function fe(){let t=Ut("chart-apr");if(!t)return;Bt("apr");let e=a.aprSeries?.rows||[],n=eo(Rt(e.map(s=>({x:ft(s.date),y:l(s.apr)}))));if(!n.length){Dt("chart-apr","apr",{yPercent:!0,messageKind:"apr"});return}At("chart-apr",{empty:!1});let o=no(n),r=le();a.charts.apr=new Chart(t,{type:"line",data:{datasets:[{label:`Rolling APR (${a.aprWindow}d)`,data:n,borderColor:"#facc15",backgroundColor:"rgba(250,204,21,0.15)",tension:.25,pointRadius:0,borderWidth:2,fill:!0}]},options:{...r,scales:{x:{...r.scales.x,...o,time:{unit:"day",tooltipFormat:"yyyy-LL-dd"}},y:{...r.scales.y,ticks:{...r.scales.y.ticks,callback:s=>w(s,1)}}}}})}function br(t){if(!_||!t)return;let e=String(t.investor_display_name||t.investor_id||"").trim(),n=document.querySelector(".app-header h1");n&&e&&(n.textContent=`${e} \xB7 ${B?"\u6295\u8CC7\u7D44\u5408\u7E3D\u89BD":"Investor summary"}`);let o=document.querySelector(".app-header h1 + p");if(!o)return;o.dataset.investorBaseCopy||(o.dataset.investorBaseCopy=o.textContent||"");let r=o.dataset.investorBaseCopy,s=String(t.investor_id||"").trim();o.textContent=s&&s!==e?`${u("Investor id","\u6295\u8CC7\u4EBA ID")}: ${s} \xB7 ${r}`:r}function $r(t){return _?t==="mainnet"?"border-sky-500/50 bg-sky-500/10 text-sky-200":t==="test"?"border-amber-500/50 bg-amber-500/10 text-amber-200":"border-slate-500/50 bg-slate-500/10 text-slate-200":t==="mainnet"?"border-rose-500/50 bg-rose-500/10 text-rose-200":"border-emerald-500/50 bg-emerald-500/10 text-emerald-200"}function io(t){if(!t)return;br(t);let e=(t.env||"").toLowerCase(),n=document.getElementById("env-badge");n&&(n.textContent=_?e==="mainnet"?u("Network: Mainnet","\u7DB2\u8DEF\uFF1A\u4E3B\u7DB2"):e==="multi"?u("Network: Multi-account","\u7DB2\u8DEF\uFF1A\u591A\u5E33\u6236"):e==="test"?u("Network: Test","\u7DB2\u8DEF\uFF1A\u6E2C\u8A66"):`${u("Network:","\u7DB2\u8DEF\uFF1A")} ${e||"\u2014"}`:`env: ${e||"?"}`,n.className="text-xs px-2 py-0.5 rounded-full border "+$r(e));let o=document.getElementById("strategy-badge");if(o){let i=X(t.option_strategy||""),c=t.accounts?.length||0;o.textContent=t.multi_account?u(`strategy: multi (${c} accounts)`,`\u7B56\u7565\uFF1A\u591A\u5E33\u6236\uFF08${c}\uFF09`):_?`${u("Strategy:","\u7B56\u7565\uFF1A")} ${i?Yt(i):"\u2014"}`:`strategy: ${i?Yt(i):"?"}`,o.className="text-xs px-2 py-0.5 rounded-full border border-sky-500/50 bg-sky-500/10 text-sky-200"}let r=document.getElementById("creds-badge");r&&(r.textContent=t.has_private_creds?"creds: ok":"creds: missing",r.className="text-xs px-2 py-0.5 rounded-full border "+(t.has_private_creds?"border-emerald-500/50 bg-emerald-500/10 text-emerald-200":"border-rose-500/50 bg-rose-500/10 text-rose-200"));let s=document.getElementById("scheduler-badge");if(s)if(t.scheduler_running){let i=t.snapshot_interval_sec||300,c=Math.round(i/60);s.textContent=u(`scheduler: on (every ${c} min)`,`\u5FEB\u7167\u6392\u7A0B\uFF1A\u6BCF ${c} \u5206\u9418`),s.className="text-xs px-2 py-0.5 rounded-full border border-emerald-500/50 bg-emerald-500/10 text-emerald-200"}else s.textContent=u("scheduler: off","\u5FEB\u7167\u6392\u7A0B\uFF1A\u95DC\u9589"),s.className="text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-700/30 text-slate-300";Se()}function ao(t){let e=document.getElementById("regime-badge");if(!e)return;let n=t?.portfolio?.regime||"?",o=String(n).toLowerCase(),r={normal:"\u6B63\u5E38",elevated:"\u504F\u9AD8",crisis:"\u8B66\u6212"},s={normal:"Normal",elevated:"Elevated",crisis:"Crisis"};e.textContent=_?`${u("Risk posture:","\u98A8\u63A7\u72C0\u614B\uFF1A")} ${B?r[o]||n:s[o]||n}`:`regime: ${n}`;let i=n==="normal"?"border-emerald-500/50 bg-emerald-500/10 text-emerald-200":n==="elevated"?"border-amber-500/50 bg-amber-500/10 text-amber-200":n==="crisis"?"border-rose-500/50 bg-rose-500/10 text-rose-200":"border-slate-600 bg-slate-700/30 text-slate-300";e.className=`text-xs px-2 py-0.5 rounded-full border ${i}`}function Sr(t,e){let n=e?.portfolio||{},r=(e?.accounts||{})[t]||{},s=Ee(n?.equity_by_book,t),i=l(r.equity),c=St(t,e),d=s?l(n?.day_start_equity_by_book?.[t]):null,p=l(n?.day_drawdown_pct_by_book?.[t]),f=Tn(t,e,c,d),m=n?.margin_ratios_by_currency?.[t]||{},v=l(m.im_ratio),h=l(m.mm_ratio),x=l(n?.delta_totals_by_currency?.[t]),b=n?.regime_by_currency?.[t],C=n?.cooling_down_by_book?.[t],T=n?.hard_derisk_by_book?.[t],E=n?.halt_entries_by_book?.[t],L=n?.halt_entry_reasons_by_book?.[t]||[],P=t==="BTC"?"book-card-btc":t==="ETH"?"book-card-eth":"book-card-usdc",y=[];if(s||y.push('<span class="chip chip-muted">not traded</span>'),b&&s){let j=b==="normal"?"chip-ok":b==="elevated"?"chip-warn":"chip-bad";y.push(`<span class="chip ${j}">${b}</span>`)}C&&y.push('<span class="chip chip-warn">cooling</span>'),T&&y.push('<span class="chip chip-bad">hard derisk</span>'),E&&y.push('<span class="chip chip-warn">halt entries</span>'),y.length===0&&y.push('<span class="chip chip-ok">healthy</span>');let R=v!==null?Math.min(1,Math.max(0,v)):0,O=v===null?"bar-ok":v>=.45?"bar-bad":v>=.35?"bar-warn":"bar-ok",I=h!==null?Math.min(1,Math.max(0,h)):0,Y=h===null?"bar-ok":h>=.33?"bar-bad":h>=.22?"bar-warn":"bar-ok";return`
    <div class="rounded-2xl border ${P} bg-slate-900/60 p-4 shadow">
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-sm font-semibold tracking-wide text-slate-200">${t} BOOK</h3>
        <div class="flex flex-wrap gap-1">${y.join("")}</div>
      </div>
      <div class="text-2xl font-mono">${$(c)}</div>
      <div class="text-xs text-slate-500 mb-3">
        ${i!==null?S(i,8)+" "+t:""}
        ${d!==null?"\xB7 day-start "+$(d):""}
      </div>
      <div class="kv"><span class="k">Day P&amp;L</span><span class="v ${k(f)}">${$(f)}</span></div>
      <div class="kv"><span class="k">Day drawdown</span><span class="v ${k(p===null?null:-p)}">${w(p)}</span></div>
      <div class="kv"><span class="k">Delta total</span><span class="v">${S(x,4)}</span></div>
      <div class="mt-3 space-y-2">
        <div>
          <div class="flex justify-between text-xs text-slate-400">
            <span>IM ratio</span><span class="font-mono">${w(v,2)}</span>
          </div>
          <div class="mini-bar"><span class="${O}" style="width:${(R*100).toFixed(1)}%"></span></div>
        </div>
        <div>
          <div class="flex justify-between text-xs text-slate-400">
            <span>MM ratio</span><span class="font-mono">${w(h,2)}</span>
          </div>
          <div class="mini-bar"><span class="${Y}" style="width:${(I*100).toFixed(1)}%"></span></div>
        </div>
      </div>
      ${L.length?`<p class="mt-3 text-xs text-rose-300">${L.map(g).join("<br>")}</p>`:""}
    </div>
  `}function lo(t){let e=document.getElementById("book-cards");if(!e)return;if(!t){e.innerHTML=`
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm md:col-span-3">
        Need DERIBIT_CLIENT_ID/SECRET in <code>.env</code> to load live status.
        Read-only views (closed trades, cumulative PnL) still work below.
      </div>`;return}let n=Object.keys(t?.portfolio?.equity_by_book||{}).map(s=>String(s).toUpperCase()).filter(s=>z.includes(s)),r=(n.length?n:z).map(s=>Sr(s,t)).join("");e.innerHTML=r}function co(t,e){let n=document.getElementById("account-cards");if(!n)return;let o=t?.accounts||e?.dashboard_accounts||[],r=new Map((e?.account_statuses||[]).map(i=>[String(i.name||""),i])),s=o.length?o:e?.account_statuses||[];if(!s.length){n.innerHTML=`
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm md:col-span-3">
        No dashboard account metadata yet.
      </div>`;return}n.innerHTML=s.map(i=>{let c=String(i.name||""),d=r.get(c)||i,p=d.portfolio||{},f=l(p.total_equity_usdc),m=l(p.day_start_equity_usdc),v=Re(p,f,m),h=p.regime||"\u2014",x=l(d.trade_group_count),b=i.has_private_creds,C=d.option_strategy||i.option_strategy||"",T=d.env||i.env||"",E=i.state_file||d.state_file||"",L=[C?Q(C):"",b===void 0?"":`<span class="chip ${b?"chip-ok":"chip-bad"}">creds ${b?"ok":"missing"}</span>`].filter(Boolean);return`
        <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 shadow">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h3 class="text-sm font-semibold tracking-wide text-slate-100">${g(c||"account")}</h3>
              <p class="text-xs text-slate-500 mt-1 break-all">${g(T)} \xB7 ${g(E)}</p>
            </div>
            <div class="flex flex-wrap justify-end gap-1 flex-shrink-0">${L.join("")}</div>
          </div>
          <div class="stat-grid mt-4">
            <div class="stat-tile">
              <div class="label">Equity</div>
              <div class="value">${$(f)}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Day P&amp;L</div>
              <div class="value ${k(v)}">${$(v)}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Open groups</div>
              <div class="value">${x??"\u2014"}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Regime</div>
              <div class="value">${g(h)}</div>
            </div>
          </div>
        </div>
      `}).join("")}function uo(t,e){let n=document.getElementById("aggregate-card");if(!n)return;let{portfolio:o,source:r}=xt(),s=e?.summary;if(!o&&!s){_&&!a.investorReady?n.innerHTML=xn():n.innerHTML=`<p class="text-sm text-slate-400">${u("No status / report data yet.","\u5C1A\u7121\u5373\u6642\u5E33\u6236\u6216\u7E3E\u6548\u6458\u8981\u8CC7\u6599\u3002")}</p>`;return}let i=l(o?.total_equity_usdc),c=l(o?.day_start_equity_usdc),d=Re(o,i,c),p=l(o?.day_drawdown_pct),f=Ct(t,a.groups),m=f.reduce(($o,So)=>$o+(st(So,t,a.groups)||0),0),v=Jn(f,t,a.groups),h=l(s?.realized_pnl_usdc),x=l(s?.lifetime_realized_apr),b=l(s?.realized_win_rate),C=l(s?.avg_holding_days),T=l(s?.realized_closed_group_count),E=l(s?.window_days_used),L=l(s?.window_realized_pnl_usdc),P=l(s?.window_realized_apr),y=Ln(e,a.groups),R=Xn(e,a.groups,t),O=E??30,I=Qn(e,a.groups,t,O),Y=to(t),j=kn(t),_e=y!==null?`${u("since","\u81EA")} ${En(y)}`:u("no realized history yet","\u5C1A\u7121\u5DF2\u5BE6\u73FE\u7D00\u9304"),Ot=r==="snapshot"&&_?`<p class="text-xs text-amber-200/80 mt-3">${u("Equity from last snapshot; live sync continues in background.","\u6B0A\u76CA\u4F86\u81EA\u6700\u8FD1\u5FEB\u7167\uFF1B\u5373\u6642\u540C\u6B65\u65BC\u80CC\u666F\u9032\u884C\u4E2D\u3002")}</p>`:r==="live"&&_?`<p class="text-xs text-emerald-200/70 mt-3">${u("Live Deribit sync","\u5DF2\u540C\u6B65 Deribit \u5373\u6642\u8CC7\u6599")}</p>`:"",xe={totalEquity:i,dayStart:c,dayPnl:d,dayDrawdown:p,openCredit:m,creditByStrategy:v,summary:s,winRate:b,avgHolding:C,sinceLine:_e,lifetimePnl:h,lifetimeNativeByBook:R,closedCount:T,windowLabelDays:O,windowPnl:L,windowNativeByBook:I,lifetimeApr:x,windowApr:P,equityNativeByBook:Y,equityUsdByBook:j},Qe=gn(xe);_?n.innerHTML=`
      <div class="investor-view-desktop">${Qe}</div>
      <div class="investor-view-mobile">${bn(xe)}</div>
      ${Ot}`:n.innerHTML=`${Qe}${Ot}`,Se()}function Ge(t){return{id:t,openCount:0,closedCount:0,wins:0,openEntryCredit:0,unrealizedUsd:0,realizedPnl:0,annualizedSum:0,annualizedCount:0,annualizedWeightedSum:0,annualizedWeight:0,aprPnlUsdSum:0,aprCapitalDays:0,holdingSum:0,holdingCount:0,books:new Set}}function oo(t,e,n){let o=n||"";return e.add(o),t.has(o)||t.set(o,Ge(o)),t.get(o)}function Cr(t,e,n){if(n===null||n<=0)return null;let o=Qt(t,e);if(o===null||o<=0)return null;let r=U(t);if(r==="USDC")return o*n;let s=l(e?.underlying_index_usd?.[r])??l(a.lastSpotUsd?.[r]);return s===null||s<=0?null:o*s*n}function wr(t){return t.aprCapitalDays>0?t.aprPnlUsdSum/t.aprCapitalDays*365:null}function kr(t,e,n){let o=new Set(H.map(d=>d.id)),r=new Map;for(let d of o)r.set(d,Ge(d));let s=Ct(t,n);for(let d of s){let p=A(d);if(!V[p])continue;let f=oo(r,o,p);f.openCount+=1;let m=st(d,t,n);m!==null&&(f.openEntryCredit+=m);let v=$t(d,t,n);v!==null&&(f.unrealizedUsd+=v);let h=q(d);h&&f.books.add(h)}let i=Ue(e,n);for(let d of i){let p=A(d);if(!V[p])continue;let f=oo(r,o,p);f.closedCount+=1;let m=Xt(d,t);m!==null&&(f.realizedPnl+=m,m>0&&(f.wins+=1));let v=kt(d);v!==null&&(f.holdingSum+=v,f.holdingCount+=1);let h=Qt(d,t);if(m!==null&&h!==null&&h>0&&v!==null&&v>0){let C=U(d),T=h;if(C==="BTC"||C==="ETH"){let E=Tt(d,t);E===null||E<=0?T=null:T=h*E}T!==null&&(f.aprPnlUsdSum+=m,f.aprCapitalDays+=T*v)}let x=Oe(d,t);if(x!==null){f.annualizedSum+=x,f.annualizedCount+=1;let C=Cr(d,t,v);C!==null&&(f.annualizedWeightedSum+=x*C,f.annualizedWeight+=C)}let b=String(d.collateral_currency||d.currency||"").toUpperCase();b&&f.books.add(b)}return wt(o).map(d=>r.get(d)||Ge(d))}function Tr(t){let e=it(t.id),n=t.closedCount>0?t.wins/t.closedCount:null,o=wr(t),r=t.holdingCount>0?t.holdingSum/t.holdingCount:null,s=Array.from(t.books).sort().join(" / ")||"\u2014";return`
    <div class="rounded-2xl border ${e.accentClass} bg-slate-900/60 p-4 shadow">
      <div class="flex items-start justify-between gap-3 mb-2">
        <div>
          <h3 class="text-sm font-semibold tracking-wide text-slate-100">${g(e.title)}</h3>
          <p class="text-xs text-slate-500 mt-1">${g(e.description)}</p>
        </div>
        ${Q(t.id)}
      </div>
      <div class="stat-grid mt-4">
        <div class="stat-tile">
          <div class="label">${u("Open groups","\u6301\u5009\u7B46\u6578")}</div>
          <div class="value">${t.openCount}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Realized APR","\u5DF2\u5BE6\u73FE\u5E74\u5316\uFF08\u52A0\u6B0A\uFF09")}</div>
          <div class="value">${w(o,1)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Unrealized P&amp;L","\u672A\u5BE6\u73FE\u640D\u76CA")}</div>
          <div class="value ${k(t.unrealizedUsd)}">${$(t.unrealizedUsd)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Realized P&amp;L","\u5DF2\u5BE6\u73FE\u640D\u76CA")}</div>
          <div class="value ${k(t.realizedPnl)}">${$(t.realizedPnl)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Win rate","\u52DD\u7387")}</div>
          <div class="value">${w(n,1)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Avg holding","\u5E73\u5747\u6301\u6709")}</div>
          <div class="value">${r===null?"\u2014":S(r,2)+(B?" \u5929":"d")}</div>
        </div>
      </div>
      <div class="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <span>${t.closedCount} ${u("closed \xB7 books","\u7B46\u5DF2\u5E73 \xB7 \u5E33\u672C")} ${g(s)}</span>
        <span>${u("weighted annualized","\u52A0\u6B0A\u5E74\u5316")} ${w(o,1)}</span>
      </div>
    </div>
  `}function po(t){let e=X(t);return e==="covered_call"?"open-position-call":e==="bull_put_spread"?"open-position-spread":"open-position-put"}function fo(t){let e=l(t);return e===null||Math.abs(e)<.005?"open-position-flat":e>0?"open-position-profit":"open-position-loss"}function mo(t){let e=l(t);return e===null||Math.abs(e)<.005?u("Flat","\u6301\u5E73"):e>0?u("In profit","\u6D6E\u76C8"):u("Underwater","\u6D6E\u8667")}function vo(t){let e=l(t),n=e===null?0:Math.max(0,Math.min(100,e*100));return`<span class="credit-capture-bar"><span class="${e===null?"bar-muted":e>=.5?"bar-ok":e>=.15?"bar-warn":"bar-bad"}" style="width:${n}%"></span></span>`}function G(t,e,n="",{secondary:o=!1}={}){return`
    <div class="open-position-metric${o?" open-position-kpi-secondary":""} ${n}">
      <span class="open-position-label">${t}</span>
      <span class="open-position-value">${e}</span>
    </div>`}function ro(t,e,n,o){let r=o==="short",s=et(t),i=r?B?`\u8CE3\u51FA${s==="Call"?"\u8CB7\u6B0A":"\u8CE3\u6B0A"}`:`Short ${s}`:u("Long protection","\u4FDD\u8B77\u8CB7\u817F"),c=Z(t,o),d=nt(t,e,o),p=K(t,o),f=ot(t,e,o,"average_price"),m=ot(t,e,o,"mark_price"),v=ct(e,t,n,o),h=q(t)||t.collateral_currency||"";return`
    <div class="open-position-leg ${r?"leg-short":"leg-long"}">
      <div class="open-position-leg-head">
        <span class="chip ${r?"chip-warn":"chip-ok"}">${i}</span>
        <span class="open-position-leg-amount">${d===null?"\u2014":S(d,4)}</span>
      </div>
      <div class="open-position-leg-instrument">${g(c||"\u2014")}</div>
      <div class="open-position-leg-metrics">
        ${G(u("Strike","\u5C65\u7D04\u50F9"),pt(p))}
        ${G(u("Entry","\u9032\u5834\u50F9"),dt(f,h))}
        ${G(u("Mark","\u6A19\u8A18\u50F9"),dt(m,h))}
        ${G(u("Leg PNL","\u55AE\u817F\u640D\u76CA"),v===null?"\u2014":$(v),k(v))}
      </div>
    </div>`}function Er(t,e,n){let o=A(t),r=q(t)||t.collateral_currency||"";if(o==="bull_put_spread"){let s=Le(t),i=Kt(t,e,"average_price"),c=Kt(t,e,"mark_price");return`
      <span>${u("Width","\u50F9\u5DEE\u5BEC\u5EA6")} ${pt(s)}</span>
      <span>${u("Entry gap","\u9032\u5834\u50F9\u5DEE")} ${dt(i,r)}</span>
      <span>${u("Mark gap","\u5E02\u50F9\u50F9\u5DEE")} ${dt(c,r)}</span>`}return`
    <span>${u("Strike","\u5C65\u7D04\u50F9")} ${pt(K(t,"short"))}</span>
    <span>${g(Ae(t))}</span>`}function Rr(t,e,n){let o=A(t),r=o==="bull_put_spread",s=ke(t),i=$t(t,e,n),c=Te(t,e,n),d=q(t)||t.collateral_currency||"",p=l(t.profit_capture),f=st(t,e,n),m=se(t,e),v=Z(t,"long"),h=nt(t,e,"short"),x=nt(t,e,"long"),b=I=>I===null?"":` \xB7 ${S(I,4)}`,C=po(o),T=fo(i),E=mo(i),L=f===null?"\u2014":$(f),P=m===null?"":`<span class="inv-pos-metric-sub font-mono">${Gt(m,d)}</span>`,y=ne(t,e),R=y===null?"\u2014":w(y,1),O="";if(r){let I=Le(t),Y=Kt(t,e,"average_price");O=`
      <span class="inv-pos-tag">${u("Width","\u50F9\u5DEE")} ${pt(I)}</span>
      <span class="inv-pos-tag">${u("Entry gap","\u9032\u5834")} ${dt(Y,d)}</span>`}else O=`
      <span class="inv-pos-tag">${u("Strike","\u5C65\u7D04")} ${pt(K(t,"short"))}</span>
      <span class="inv-pos-tag">${g(Ae(t))}</span>`;return`
    <article class="inv-position ${C} ${T}">
      <header class="inv-position-head">
        <div class="inv-position-main">
          <div class="inv-position-titleline">
            ${Q(o,{compact:!0})}
            <h3 class="inv-position-name">${g(Wt(t))}</h3>
          </div>
          <p class="inv-position-contract font-mono">${g(t.short_instrument_name||"\u2014")}<span class="inv-position-size tabular-nums">${b(h)}</span></p>
          ${r&&v?`<p class="inv-position-contract font-mono inv-position-contract--long">${u("Long","\u8CB7\u817F")} ${g(v)}<span class="inv-position-size tabular-nums">${b(x)}</span></p>`:""}
          <div class="inv-position-tags">
            <span class="inv-pos-tag">${g(d)}</span>
            <span class="inv-pos-tag inv-pos-tag--status">${g(E)}</span>
            ${O}
          </div>
        </div>
        <div class="inv-position-pnl">
          <span class="inv-position-pnl-label">${u("Unrealized","\u672A\u5BE6\u73FE")}</span>
          <span class="inv-position-pnl-value font-mono tabular-nums ${k(i)}">${i===null?"\u2014":$(i)}</span>
          <span class="inv-position-pnl-native font-mono tabular-nums ${k(c)}">${Gt(c,d)}</span>
        </div>
      </header>
      <div class="inv-position-strip" role="list">
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("DTE","\u5230\u671F")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${s!==null?`${S(s,1)}${B?"\u5929":"d"}`:"\u2014"}</span>
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("Credit kept","\u6B0A\u5229\u91D1")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${w(p,1)}</span>
          ${vo(p)}
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("Entry","\u9032\u5834")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${L}</span>
          ${P}
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("Entry APR","\u9032\u5834\u5E74\u5316")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums ${y!==null&&y>=.15?"pnl-pos":""}">${R}</span>
        </div>
      </div>
    </article>`}function Dr(t,e,n){let o=A(t),r=o==="bull_put_spread",s=ke(t),i=$t(t,e,n),c=Te(t,e,n),d=q(t)||t.collateral_currency||"",p=l(t.profit_capture),f=st(t,e,n),m=se(t,e),v=oe(t),h=Me(t,e),x=re(t),b=Fe(t,e),C=Z(t,"long"),T=!_&&_t(t)?_t(t):"",E=po(o),L=fo(i),P=_?u(`${d} book`,`${d} \u5E33\u672C`):`${d} book`;return`
    <article class="open-position-card ${E} ${L}">
      <div class="open-position-glow"></div>
      <div class="open-position-header">
        <div class="open-position-main">
          <div class="open-position-title-row">
            ${Q(o)}
            <h3>${g(Wt(t))}</h3>
            <span class="open-book-pill">${g(P)}</span>
            <span class="open-status-pill">${mo(i)}</span>
          </div>
          <div class="open-position-instruments">
            <span>${g(t.short_instrument_name||"\u2014")}</span>
            ${r&&C?`<span>${u("Long","\u8CB7\u5165\u4FDD\u8B77")} ${g(C)}</span>`:""}
          </div>
          <div class="open-position-detail-row">
            ${Er(t,e,n)}
            ${T?`<span>${g(T)}</span>`:""}
          </div>
        </div>
        <div class="open-position-pnl-panel">
          <span class="open-position-label"${r?` title="${u("Sum of leg mark MTM when both legs load; otherwise engine entry\u2212debit (bid/ask close est.).","\u5169\u817F\u7686\u8F09\u5165\u6642\u70BA\u6A19\u8A18\u640D\u76CA\u52A0\u7E3D\uFF1B\u5426\u5247\u70BA\u5F15\u64CE\u9032\u5834\u6536\u6582\u8207\u73FE\u4F30\u5E73\u5009\u5DEE\u984D\u3002")}"`:""}>${u("Unrealized PNL","\u672A\u5BE6\u73FE\u640D\u76CA")}</span>
          <strong class="${k(i)}">${i===null?"\u2014":$(i)}</strong>
          <span class="open-position-native ${k(c)}">${Gt(c,d)}</span>
        </div>
      </div>
      <div class="open-position-kpis open-position-kpis-extended">
        ${G(u("DTE","\u8DDD\u5230\u671F\u5929\u6578"),s!==null?`${S(s,2)}${B?" \u5929":"d"}`:"\u2014")}
        ${G(u("Credit kept","\u5DF2\u6536\u6B0A\u5229\u91D1\u6BD4\u4F8B"),`${w(p,1)}${vo(p)}`)}
        ${G(u("Entry credit","\u9032\u5834\u6536\u6582"),f===null?"\u2014":te(f,m,d))}
        ${(()=>{let y=ne(t,e),R=y!==null&&y>=.15?"pnl-pos":"";return G(u("Entry net APR","\u9032\u5834\u6DE8\u5E74\u5316"),y===null?"\u2014":w(y,1),R)})()}
        ${G(u("Entry fee","\u9032\u5834\u624B\u7E8C\u8CBB"),v===null?"\u2014":te(v,h,d))}
        ${G(u("Est. close fee","\u9810\u4F30\u5E73\u5009\u8CBB"),x===null?"\u2014":te(x,b,d))}
      </div>
      <div class="open-position-legs ${r?"has-two-legs":"has-one-leg"}">
        ${ro(t,e,n,"short")}
        ${r?ro(t,e,n,"long"):""}
      </div>
    </article>`}function Br(t,e,n){let o=Dr(t,e,n);return _?`<div class="investor-view-desktop">${o}</div><div class="investor-view-mobile">${Rr(t,e,n)}</div>`:o}function Ur(t,e,n,o){let r=X(t)||t,s=it(r),i=e.map(c=>Br(c,n,o)).join("");return`
    <div class="rounded-2xl border ${s.accentClass} bg-slate-900/60 shadow overflow-hidden">
      <div class="flex flex-wrap items-baseline justify-between gap-3 px-4 py-3 border-b border-slate-800 bg-slate-950/40">
        <div class="flex flex-wrap items-center gap-2 min-w-0">
          <h3 class="text-sm font-semibold text-slate-200">${g(s.title)}</h3>
          ${Q(r)}
        </div>
        <span class="text-xs text-slate-500">${e.length} ${u("open","\u7B46\u6301\u5009")}</span>
      </div>
      <div class="p-4">
        <div class="open-position-list">
          ${i}
        </div>
      </div>
    </div>`}function me(t,e,n){let o=document.getElementById("strategy-cards"),r=document.getElementById("strategy-open-groups");if(!o&&!r)return;let s=kr(t,e,n),i=Ct(t,n),c=i.length,d=Ue(e,n).length,p=s.filter(v=>v.openCount||v.closedCount).length;if(M("strategy-meta",_?u(`${c} open \xB7 ${d} closed \xB7 ${p||0} active strategy groups`,`${c} \u7B46\u6301\u5009 \xB7 ${d} \u7B46\u5DF2\u5E73 \xB7 ${p||0} \u985E\u7B56\u7565`):`${c} open \xB7 ${d} closed \xB7 ${p||0} active strategy groups`),o&&(o.innerHTML=s.map(Tr).join("")),!r)return;if(!i.length){r.innerHTML=`
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-sm text-slate-400">
        ${u("No open strategy positions.","\u76EE\u524D\u6C92\u6709\u958B\u5009\u4E2D\u7684\u7B56\u7565\u90E8\u4F4D\u3002")}
      </div>`;return}let f=new Map,m=new Set(H.map(v=>v.id));for(let v of i){let h=A(v);V[h]&&(f.has(h)||f.set(h,[]),f.get(h).push(v))}r.innerHTML=wt(m).filter(v=>f.has(v)).map(v=>Ur(v,f.get(v),t,n)).join("")}function so(t,e,n,o,r){if(!t)return;if(!e.length){t.innerHTML=`<li class="activity-empty">${g(r)}</li>`;return}let s=[];for(let i of e)try{s.push(zn(i,n,o))}catch(c){console.warn("activity card skipped",i?.group_id,c)}t.innerHTML=s.length?s.join(""):`<li class="activity-empty">${g(r)}</li>`}function Nt(t,e,n){let o=document.getElementById("activity-open-list"),r=document.getElementById("activity-closed-list");if(!o&&!r)return;let s=Mn(t,n),i=Fn(t,e,n),c=ze(s,a.activityOpenPage,vt),d=ze(i,a.activityClosedPage,vt);a.activityOpenPage=c.page,a.activityClosedPage=d.page,M("activity-meta",u(`${s.length} open \xB7 ${i.length} closed`,`${s.length} \u6301\u5009\u4E2D \xB7 ${i.length} \u5DF2\u5E73\u5009`)),so(o,c.rows,t,n,u("No open positions","\u5C1A\u7121\u6301\u5009")),so(r,d.rows,t,n,u("No closed trades","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D00\u9304"));let p=document.getElementById("activity-open-pagination"),f=document.getElementById("activity-closed-pagination");p&&(p.innerHTML=He("open",c),p.hidden=!p.innerHTML),f&&(f.innerHTML=He("closed",d),f.hidden=!f.innerHTML)}function Lr(t){let e=Array.isArray(t?.strategy_stresses)?t.strategy_stresses.filter(Boolean):[];return e.length?e:[t]}function Ar(t,e){let n=t.equity_usdc_by_book||{},o=t.strategy_analysis||{},r=X(t.option_strategy||o.label||"naked_short"),s=Object.values(n).reduce((m,v)=>m+(l(v)||0),0),i=(t.accounts||[]).map(m=>m?.name).filter(Boolean).join(", "),c=Array.isArray(o.actions)?o.actions:[],d=z.map(m=>`
        <div class="rounded-xl bg-slate-800/40 px-3 py-2">
          <div class="text-[11px] text-slate-400 uppercase tracking-wide">${m} book</div>
          <div class="font-mono text-sm">${$(n[m])}</div>
        </div>`).join(""),p=(t.scenarios||[]).map(m=>{let v=l(m.loss_usdc_total),h=l(m.loss_usdc_pct_of_total_equity),x=m.loss_by_book_usdc||{};return`
        <tr>
          <td class="px-3 py-2 font-mono">${w(l(m.shock),0)}</td>
          <td class="px-3 py-2 font-mono">${w(l(m.slippage),0)}</td>
          <td class="px-3 py-2 text-right font-mono ${k(v)}">${$(v)}</td>
          <td class="px-3 py-2 text-right font-mono">${w(h,2)}</td>
          <td class="px-3 py-2 text-right font-mono ${k(l(x.BTC))}">${$(x.BTC)}</td>
          <td class="px-3 py-2 text-right font-mono ${k(l(x.ETH))}">${$(x.ETH)}</td>
          <td class="px-3 py-2 text-right font-mono ${k(l(x.USDC))}">${$(x.USDC)}</td>
        </tr>`}).join(""),f=c.length?`<ul class="mt-2 list-disc list-inside text-xs text-slate-500 space-y-1">
        ${c.map(m=>`<li>${g(m)}</li>`).join("")}
      </ul>`:"";return`
    <div class="${e>1?"rounded-2xl border border-slate-800 bg-slate-900/40 p-4":""}">
      <div class="rounded-xl bg-slate-800/40 px-3 py-3 mb-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div class="text-[11px] text-slate-400 uppercase tracking-wide">Strategy black swan read</div>
            <div class="mt-1 flex items-center gap-2 text-sm text-slate-200">
              <span>${g(Yt(r))}</span>
              ${Q(r)}
            </div>
          </div>
          <div class="text-[11px] text-slate-500">
            ${g(i||`${t.scenarios?.length||0} scenarios \xB7 ${t.positions?.length||0} legs`)}
          </div>
        </div>
        <p class="mt-2 text-xs text-slate-400">${g(o.summary||"")}</p>
        <p class="mt-1 text-xs text-slate-500">${g(o.focus||"")}</p>
        ${f}
      </div>
      <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
        ${d}
        <div class="rounded-xl bg-slate-800/40 px-3 py-2">
          <div class="text-[11px] text-slate-400 uppercase tracking-wide">Total equity (USDC)</div>
          <div class="font-mono text-sm">${$(s)}</div>
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
  `}function Pt(t){if(_)return;let e=document.getElementById("stress-card");if(!e)return;let n=!!document.getElementById("stress-section")?.open;if(!t&&!a.stressDataLoaded&&!n)return;if(!t){a.stressLoadInFlight||a.health?.has_private_creds?e.innerHTML='<p class="text-slate-500 text-sm">Loading\u2026</p>':e.innerHTML='<p class="text-sm text-slate-400">Set DERIBIT_CLIENT_ID and DERIBIT_CLIENT_SECRET to load live stress data.</p>',M("stress-meta","\u2014");return}let o=Lr(t),r=o.reduce((i,c)=>i+(c.scenarios?.length||0),0),s=o.reduce((i,c)=>i+(c.positions?.length||0),0);M("stress-meta",`${o.length} strategy view${o.length===1?"":"s"} \xB7 ${r} scenarios \xB7 ${s} legs`),e.innerHTML=`
    <div class="space-y-4">
      ${o.map(i=>Ar(i,o.length)).join("")}
    </div>
    <p class="text-xs text-slate-500 mt-3">
      Per-book loss is capped at that book's equity (liquidation-style floor). Spot shock is a negative index move.
      For bull put spread, long option legs are netted when present; for covered call, BTC/ETH spot cover drawdown is included.
    </p>
  `}var Pr=["/vendor/luxon.min.js","/vendor/chart.umd.min.js","/vendor/chartjs-adapter-luxon.umd.min.js"],It=null;function Ir(t){return new Promise((e,n)=>{let o=document.querySelector(`script[src="${t}"]`);if(o){if(o.dataset.loaded==="true"){e();return}o.addEventListener("load",()=>e(),{once:!0}),o.addEventListener("error",()=>n(new Error(`failed to load ${t}`)),{once:!0});return}let r=document.createElement("script");r.src=t,r.async=!1,r.onload=()=>{r.dataset.loaded="true",e()},r.onerror=()=>n(new Error(`failed to load ${t}`)),document.head.appendChild(r)})}function mt(){return globalThis.Chart&&globalThis.luxon?Promise.resolve():It||(It=(async()=>{for(let t of Pr)await Ir(t);if(!globalThis.Chart||!globalThis.luxon)throw new Error("Chart.js vendor failed to initialize")})().catch(t=>{throw It=null,t}),It)}var We=null;function Ye(){We?.()}function je(){let t=document.getElementById("header-spot-btc"),e=document.getElementById("header-spot-eth"),n=a.lastSpotUsd.BTC,o=a.lastSpotUsd.ETH;t&&(t.textContent=n!==null&&n>0?`BTC ${F.usd2.format(n)}`:"BTC \u2014"),e&&(e.textContent=o!==null&&o>0?`ETH ${F.usd2.format(o)}`:"ETH \u2014")}async function ve(){if(!he())return;try{await mt()}catch(e){console.error("chart vendor load failed",e),D(`charts: ${e.message}`);return}let t=[["risk-capital",ue],["cum-pnl",de],["daily-pnl",pe],["apr",fe]];for(let[e,n]of t)try{n()}catch(o){console.error(`${e} chart render failed`,o)}Lt()}var Or={spot:{en:"Fetching BTC / ETH market prices\u2026",zh:"\u6B63\u5728\u53D6\u5F97 BTC / ETH \u5373\u6642\u5831\u50F9\u2026"},snapshot:{en:"Loading last equity snapshot\u2026",zh:"\u6B63\u5728\u8B80\u53D6\u6700\u8FD1\u6B0A\u76CA\u5FEB\u7167\u2026"},health:{en:"Checking account connection\u2026",zh:"\u6B63\u5728\u78BA\u8A8D\u5E33\u6236\u9023\u7DDA\u2026"},groups:{en:"Loading open positions and spreads\u2026",zh:"\u6B63\u5728\u8B80\u53D6\u6301\u5009\u8207\u50F9\u5DEE\u90E8\u4F4D\u2026"},cumulative:{en:"Loading realized P&L history\u2026",zh:"\u6B63\u5728\u8F09\u5165\u5DF2\u5BE6\u73FE\u640D\u76CA\u6B77\u53F2\u2026"},apr:{en:"Calculating rolling performance (APR)\u2026",zh:"\u6B63\u5728\u8A08\u7B97\u6EFE\u52D5\u5E74\u5316\u5831\u916C\u2026"},status:{en:"Syncing live equity and margin\u2026",zh:"\u6B63\u5728\u540C\u6B65\u5373\u6642\u6B0A\u76CA\u8207\u4FDD\u8B49\u91D1\u2026"},summary:{en:"Loading performance summary from local records\u2026",zh:"\u6B63\u5728\u5F9E\u672C\u5730\u7D00\u9304\u8F09\u5165\u7E3E\u6548\u6458\u8981\u2026"},render:{en:"Preparing your dashboard\u2026",zh:"\u6B63\u5728\u6574\u7406\u5100\u8868\u677F\u986F\u793A\u2026"},done:{en:"Done",zh:"\u5B8C\u6210"}};function Mr(t){let e=Or[t];return e?u(e.en,e.zh):""}function yo(t,{includeCharts:e=!0}={}){let n=3+(t?2:0)+1;return e&&(n+=2),n}function Ze(t,e){let n=Math.min(100,Math.max(0,Math.round(t*100))),o=document.getElementById("investor-load-bar-fill");o&&(o.style.width=`${n}%`);let r=document.querySelector("[data-investor-load-pct]");r&&(r.textContent=`${n}%`);let s=document.querySelector("[data-investor-load-step]");s&&e&&(s.textContent=Mr(e))}function _o(){if(!_)return;let t=(e,n,o)=>{let r=document.querySelector(`[data-investor-load-${e}]`);r&&(r.textContent=u(n,o))};t("eyebrow","Please wait","\u8ACB\u7A0D\u5019"),t("title","Loading your portfolio","\u6B63\u5728\u8F09\u5165\u60A8\u7684\u6295\u8CC7\u7D44\u5408"),t("hint","Showing snapshot first; live positions and P&L sync in the background.","\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\uFF1B\u6301\u5009\u8207\u640D\u76CA\u65BC\u80CC\u666F\u540C\u6B65\u4E2D\u3002")}function xo({blocking:t=!0}={}){if(!_)return;a.investorLoadDone=0,a.investorLoadTotal=yo(!1),document.body.classList.toggle("investor-blocking-load",t);let e=document.getElementById("investor-load-overlay");e&&(e.classList.remove("hidden"),e.classList.toggle("investor-load-overlay--refresh",!t),e.setAttribute("aria-busy","true"));let n=document.getElementById("refresh-now");n&&(n.disabled=!0),Ze(0,"spot")}function at(t){if(!_)return;a.investorLoadDone=Math.min(a.investorLoadTotal||1,a.investorLoadDone+1);let e=a.investorLoadTotal>0?a.investorLoadDone/a.investorLoadTotal:0;Ze(e,t)}function Fr(t){if(!_)return;if(!t){xo({blocking:!a.investorReady});return}Ze(1,"done"),a.investorReady=!0,document.body.classList.remove("investor-blocking-load"),document.body.classList.add("investor-ready");let e=document.getElementById("investor-load-overlay");e&&(e.classList.add("hidden"),e.classList.remove("investor-load-overlay--refresh"),e.setAttribute("aria-busy","false"));let n=document.getElementById("refresh-now");n&&(n.disabled=!1),Lt()}async function zr({renderDependentViews:t=!0,updateDom:e=!0}={}){try{let n=await N("/api/spot");a.lastSpotUsd.BTC=l(n.BTC),a.lastSpotUsd.ETH=l(n.ETH),e&&(je(),t&&(me(a.status,a.report,a.groups),Nt(a.status,a.report,a.groups)))}catch{}}function he(){return!!document.getElementById("charts-section")?.open}function go(){return!!document.getElementById("stress-section")?.open}async function ho(){try{let t=await N("/api/portfolio/snapshot");a.portfolioSnapshot=t,t?.source==="ledger"&&(a.dataFreshness.source="snapshot",a.dataFreshness.snapshotMs=l(t.freshness_ms),a.dataFreshness.live=!1)}catch{}}async function Hr(){let t=ge,e=!1,n=ut(t).then(()=>{throw e=!0,new Error("status timeout")});try{let o=await Promise.race([N("/api/status"),n]);return a.status=o,a.statusErrorOnce=!1,a.dataFreshness.source="live",a.dataFreshness.live=!0,a.dataFreshness.statusMs=0,o}catch(o){return e&&a.portfolioSnapshot?.portfolio?(a.statusErrorOnce||(D(u("Live sync is slow; showing last snapshot.","\u5373\u6642\u540C\u6B65\u8F03\u6162\uFF0C\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\u3002")),a.statusErrorOnce=!0),N("/api/status").then(r=>{a.status=r,a.dataFreshness.source="live",a.dataFreshness.live=!0,Ye()}).catch(()=>{}),null):(a.status=null,a.statusErrorOnce||(D(`status: ${o.message}`),a.statusErrorOnce=!0),null)}}async function Ve({backgroundOnTimeout:t=!1,sections:e=null}={}){let n=ge,o=!1,r=N(De(30,{sections:e})),s=_?Promise.race([r,ut(n).then(()=>{throw o=!0,new Error("dashboard bundle timeout")})]):r;try{return Be(await s),!0}catch(i){return _&&o&&a.portfolioSnapshot?.portfolio?(a.statusErrorOnce||(D(u("Live sync is slow; showing last snapshot.","\u5373\u6642\u540C\u6B65\u8F03\u6162\uFF0C\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\u3002")),a.statusErrorOnce=!0),t&&N(De(30,{sections:e})).then(c=>{Be(c),Ye()}).catch(()=>{}),!1):((!_||!o)&&D(`dashboard bundle: ${i.message}`),!1)}}async function Je({force:t=!1,investorFetchWrap:e=null}={}){if(!(!t&&!he())){if(!t&&a.chartsDataLoaded){await ve();return}if(!a.chartsLoadInFlight){a.chartsLoadInFlight=!0;try{await mt();let n=()=>N("/api/cumulative_pnl_series").then(r=>{a.cumulativePnl=r}).catch(r=>D(`cumulative pnl: ${r.message}`)),o=()=>N(ce()).then(r=>{a.aprSeries=r}).catch(r=>D(`apr series: ${r.message}`));e?await Promise.all([e("cumulative",n),e("apr",o)]):await Promise.all([n(),o()]),a.chartsDataLoaded=!0,await ve()}finally{a.chartsLoadInFlight=!1}}}}async function Ke({force:t=!1}={}){if(!_&&!(!t&&!go())){if(!t&&a.stressDataLoaded){Pt(a.stress);return}if(!a.stressLoadInFlight&&a.health?.has_private_creds){a.stressLoadInFlight=!0;try{let e=await N("/api/stress?shocks=0.1,0.2,0.3,0.4,0.5");a.stress=e,a.stressDataLoaded=!0,Pt(a.stress)}catch(e){D(`stress: ${e.message}`)}finally{a.stressLoadInFlight=!1}}}}function qr(){return a.lastRefreshStartedMs?Math.max(0,Mt-(Date.now()-a.lastRefreshStartedMs)):0}async function ye({force:t=!1,silentIfLimited:e=!1,renderDashboard:n}={}){if(We=n??null,a.refreshInFlight){e||D(u("refresh already running","\u5DF2\u6709\u66F4\u65B0\u6B63\u5728\u9032\u884C"));return}let o=qr();if(!t&&o>0){e||D(u(`refresh rate limited; wait ${Math.ceil(o/1e3)}s`,`\u8ACB\u7A0D\u5019 ${Math.ceil(o/1e3)} \u79D2\u5F8C\u518D\u8A66`));return}a.refreshInFlight=!0,a.lastRefreshStartedMs=Date.now();let r=_&&!a.investorReady;r?xo({blocking:!0}):_&&gt(!0,{indeterminate:!0});try{let i=function(){s||(s=!0,requestAnimationFrame(()=>{s=!1;try{n?.()}catch(y){console.error("renderDashboard failed",y),D(`render failed: ${y.message}`)}}))},c=function(y,R){return r?R().finally(()=>at(y)):R()},v=function(){return N("/api/groups").then(y=>{a.groups=y,i()}).catch(y=>{D(`groups: ${y.message}`)})},h=function(){return N(Un(30)).then(y=>{a.report=y,i()}).catch(y=>D(`realized summary: ${y.message}`))},x=function(){return N("/api/status").then(y=>{a.status=y,a.statusErrorOnce=!1,i()}).catch(y=>{a.status=null,a.statusErrorOnce||(D(`status: ${y.message}`),a.statusErrorOnce=!0)})},b=function(){return Ke({force:!0})},s=!1;try{let y=c("spot",()=>zr({renderDependentViews:!_,updateDom:!0})),R=c("health",()=>N("/api/health").then(O=>{a.health=O}));await Promise.all([y,R])}catch(y){D(`health failed: ${y.message}`)}let d=!!a.health?.has_private_creds;r&&(a.investorLoadTotal=yo(d,{includeCharts:he()}));let p=!1;if(_&&r){try{await Promise.race([c("snapshot",ho),ut(on)])}catch{}p=!0,Fr(!0),gt(!0,{indeterminate:!0}),i()}let f=r?(y,R)=>c(y,R):null,m=(y,R)=>f?f(y,R):R();async function C(){await m("groups",v),_?(await m("status",()=>Hr().then(()=>i())),await m("summary",h)):(await x(),await h())}async function T(){if(!d){await m("groups",v),a.status=null,a.report=null,a.stress=null,a.stressDataLoaded=!1;return}if(nn){if(d&&(_&&r||!_&&t)&&await Ve({sections:"status,groups"})){r&&(at("groups"),at("status")),i(),Ve({sections:"realized_summary"}).then(I=>{I&&r&&at("summary"),Ye()}).catch(()=>{});return}if(await Ve({backgroundOnTimeout:_})){r&&(at("groups"),at("status"),at("summary")),i();return}}await C()}let E=[()=>T()];!_&&d&&go()&&E.push(()=>b()),_&&!p&&E.push(()=>m("snapshot",ho)),he()&&E.push(()=>Je({force:!1,investorFetchWrap:f})),await Hn(E,en),_&&(a.stress=null),(!_||a.investorReady)&&i(),gt(!1),M("last-refresh",`${u("last refresh:","\u4E0A\u6B21\u66F4\u65B0\uFF1A")} ${an()}`)}finally{a.refreshInFlight=!1,gt(!1),We=null}}function Xe(){Sn(a.status,a.groups),ao(a.status),io(a.health),je(),_||(co(a.health,a.status),lo(a.status)),uo(a.status,a.report),me(a.status,a.report,a.groups),ve().catch(t=>{console.error("performance charts failed",t)}),Nt(a.status,a.report,a.groups),_||Pt(a.stress)}function Vr(t){a.bookFilter=t;let e=document.querySelector("#book-filter");e&&e.querySelectorAll("button[data-book]").forEach(n=>{n.classList.toggle("filter-active",n.dataset.book===t)}),ue(),de(),pe()}function Wr(){let t=document.getElementById("auto-refresh");if(!t)return;function e(){a.autoRefreshHandle&&(clearInterval(a.autoRefreshHandle),a.autoRefreshHandle=null),t.checked&&(a.autoRefreshHandle=setInterval(()=>ye({silentIfLimited:!0,renderDashboard:Xe}),Mt))}t.addEventListener("change",e),e()}function Yr(){document.getElementById("refresh-now")?.addEventListener("click",()=>ye({renderDashboard:Xe})),document.getElementById("book-filter")?.addEventListener("click",t=>{let e=t.target.closest("button[data-book]");e&&Vr(e.dataset.book)}),document.getElementById("activity-section")?.addEventListener("click",t=>{let e=t.target.closest("button.activity-page-btn");if(!e||e.disabled)return;let n=e.dataset.activitySection,o=e.dataset.direction==="next"?1:-1;n==="open"?a.activityOpenPage+=o:n==="closed"&&(a.activityClosedPage+=o),Nt(a.status,a.report,a.groups)}),document.getElementById("apr-window")?.addEventListener("change",async t=>{a.aprWindow=parseInt(t.target.value,10)||30;try{await mt(),a.aprSeries=await N(ce())}catch(e){D(`apr series: ${e.message}`)}fe()})}function jr(){let e=document.getElementById("charts-section")?.querySelector("summary");e&&e.addEventListener("mouseenter",()=>{mt().catch(()=>{})},{once:!0})}function Zr(){document.querySelectorAll("details.collapsible-section").forEach(t=>{t.addEventListener("toggle",()=>{if(t.open){if(t.id==="charts-section"){Je().catch(e=>{console.error("chart data load failed",e)});return}if(t.id==="stress-section"){Ke().catch(e=>{console.error("stress load failed",e)});return}Lt()}})})}function bo(){let t=()=>{_o(),jn(),Yr(),Zr(),jr(),Wr(),ye({force:!0,renderDashboard:Xe})};document.readyState==="loading"?document.addEventListener("DOMContentLoaded",t):t()}bo();})();
