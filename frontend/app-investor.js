(()=>{var _o="investor",_=_o==="investor",xo=(()=>{if(!_)return"en";let t=String(typeof window<"u"&&window.__INVESTOR_LOCALE__||"en").trim().toLowerCase();return t==="zh-hant"||t==="zh_tw"||t==="zh-tw"||t==="zh-hk"||t==="zh"?"zh":"en"})(),L=_&&xo==="zh";function u(t,e){return _&&L?e:t}function go(){try{return document.querySelector('meta[name="dashboard-api-base"]')?.getAttribute("content")?.trim()||""}catch{return""}}function Xe(t){if(/^https?:\/\//i.test(t))return t;let n=((typeof window<"u"&&window.__API_BASE__?String(window.__API_BASE__).trim():"")||go()).replace(/\/$/,""),o=t.startsWith("/")?t:`/${t}`;return n?`${n}${o}`:o}var F={usd0:new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:0}),usd2:new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:2}),num4:new Intl.NumberFormat("en-US",{maximumFractionDigits:4}),num8:new Intl.NumberFormat("en-US",{maximumFractionDigits:8}),pct2:new Intl.NumberFormat("en-US",{style:"percent",maximumFractionDigits:2,minimumFractionDigits:2}),pct1:new Intl.NumberFormat("en-US",{style:"percent",maximumFractionDigits:1,minimumFractionDigits:1})},at={BTC:"#fb923c",ETH:"#818cf8",USDC:"#38bdf8",TOTAL:"#a3e635"},z=["BTC","ETH","USDC"],It=18e4,Qe=_?6:3,tn=!0,_e=45e3,en=3e3,nn=new Set([502,503,504]),on=2,xe=450,ft=10,H=[{id:"covered_call",title:"Covered Call",titleZh:"\u5099\u514C\u8CB7\u6B0A",short:"Covered Call",shortZh:"\u5099\u514C",chipShort:"CC",chipShortZh:"\u5099\u514C",accentClass:"strategy-card-call",description:"Short call backed by existing BTC/ETH spot collateral.",descriptionZh:"\u5728\u6301\u6709\u73FE\u8CA8\u64D4\u4FDD\u4E0B\u8CE3\u51FA\u8CB7\u6B0A\uFF0C\u4EE5\u6B0A\u5229\u91D1\u589E\u5F37\u6536\u76CA\u3002"},{id:"naked_short",title:"Naked Short",titleZh:"\u55AE\u8CE3\u9078\u64C7\u6B0A\uFF08\u88F8\u8CE3\uFF09",short:"Naked Short",shortZh:"\u88F8\u8CE3",chipShort:"Naked",chipShortZh:"\u88F8\u8CE3",accentClass:"strategy-card-put",description:"Single-leg short option (put / call / both) with uncapped tail risk on the chosen side.",descriptionZh:"\u55AE\u908A\u8CE3\u51FA\u8CB7\uFF0F\u8CE3\u6B0A\uFF1B\u5728\u5C0D\u61C9\u65B9\u5411\u5177\u5C3E\u90E8\u98A8\u96AA\uFF0C\u9700\u56B4\u683C\u98A8\u63A7\u3002"},{id:"bull_put_spread",title:"Bull Put Spread",titleZh:"\u725B\u52E2\u8CE3\u6B0A\u50F9\u5DEE",short:"Put Spread",shortZh:"\u8CE3\u6B0A\u50F9\u5DEE",chipShort:"Spread",chipShortZh:"\u50F9\u5DEE",accentClass:"strategy-card-spread",description:"Short put paired with a lower-strike long put protection leg.",descriptionZh:"\u8CE3\u51FA\u8F03\u9AD8\u5C65\u7D04\u50F9\u8CE3\u6B0A\uFF0C\u4E26\u8CB7\u5165\u8F03\u4F4E\u5C65\u7D04\u50F9\u8CE3\u6B0A\u4F5C\u4FDD\u8B77\u3002"}],G=Object.fromEntries(H.map(t=>[t.id,t]));var a={health:null,status:null,report:null,stress:null,groups:null,cumulativePnl:null,aprSeries:null,portfolioSnapshot:null,dataFreshness:{source:null,snapshotMs:null,statusMs:null,live:!1},chartsDataLoaded:!1,chartsLoadInFlight:!1,stressDataLoaded:!1,stressLoadInFlight:!1,bookFilter:"ALL",aprWindow:30,charts:{},autoRefreshHandle:null,refreshInFlight:!1,investorReady:!1,investorLoadTotal:0,investorLoadDone:0,lastRefreshStartedMs:0,statusErrorOnce:!1,lastUnderlyingIndexUsd:{},lastSpotUsd:{BTC:null,ETH:null},activityOpenPage:1,activityClosedPage:1};function l(t){if(t==null||t==="")return null;let e=typeof t=="number"?t:Number(t);return Number.isFinite(e)?e:null}function S(t,e=2){let n=l(t);return n===null?"\u2014":e===0?F.usd0.format(n):F.usd2.format(n)}function w(t,e=2){let n=l(t);return n===null?"\u2014":e===1?F.pct1.format(n):F.pct2.format(n)}function yt(){if(a.status?.portfolio)return{portfolio:a.status.portfolio,source:"live",freshnessMs:a.dataFreshness.statusMs??0};let t=a.portfolioSnapshot?.portfolio;return t&&Object.keys(t).length>0?{portfolio:t,source:"snapshot",freshnessMs:l(a.portfolioSnapshot?.freshness_ms)}:{portfolio:null,source:null,freshnessMs:null}}function bo(t){let e=l(t);return e===null||e<0?null:Math.max(1,Math.round(e/6e4))}function So(){let t=yt();if(t.source==="live"){let e=l(a.dataFreshness.statusMs);if(e!==null&&e<3e4)return`<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-emerald-500/40 bg-emerald-500/10 text-emerald-200">${u("Live","\u5373\u6642")}</span>`}if(t.source==="snapshot"){let e=bo(t.freshnessMs);return`<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-200">${e!==null?u(`Snapshot \xB7 ~${e}m ago`,`\u5FEB\u7167 \xB7 \u7D04 ${e} \u5206\u9418\u524D`):u("Snapshot","\u5FEB\u7167")}</span>`}return`<span id="data-freshness-badge" class="text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-800/60 text-slate-400">${u("Loading\u2026","\u8F09\u5165\u4E2D\u2026")}</span>`}function be(){if(!_)return;let t=document.getElementById("data-freshness-slot");t&&(t.innerHTML=So())}function ht(t,{indeterminate:e=!1}={}){let n=document.getElementById("investor-progress-bar");n&&(n.classList.toggle("hidden",!t),n.classList.toggle("investor-progress-bar--indeterminate",t&&e))}function pn(){let e=`<div class="overview-metrics-grid">${'<div class="skeleton-block h-16 rounded-lg"></div>'.repeat(8)}</div>`;return _?`<div class="investor-view-desktop">${e}</div><div class="investor-view-mobile"><div class="inv-dashboard">
      <div class="inv-panel skeleton-block" style="height:5.5rem"></div>
      <div class="inv-panel skeleton-block" style="height:4rem"></div>
      <div class="inv-panel skeleton-block" style="height:7rem"></div>
    </div></div>`:e}function fn(t){let{totalEquity:e,dayStart:n,dayPnl:o,dayDrawdown:r,openCredit:s,creditByStrategy:i,summary:c,winRate:d,avgHolding:p,sinceLine:f,lifetimePnl:m,lifetimeNativeByBook:v,closedCount:y,windowLabelDays:x,windowPnl:b,windowNativeByBook:C,lifetimeApr:E,windowApr:T,equityNativeByBook:U,equityUsdByBook:N}=t;return`
    <div class="overview-metrics-grid">
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Total equity","\u7E3D\u6B0A\u76CA")}</div>
        <div class="text-2xl font-mono">${S(e)}</div>
        <div class="text-[11px] text-slate-500">${u("USDC equivalent (all books)","USDC \u7D04\u7576\uFF08\u5168\u5E33\u672C\u5408\u8A08\uFF09")}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${Cn(U,N)}</div>
          <div class="overview-metric-line">${u("day-start","\u65E5\u521D")} ${S(n)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Day P&L","\u672C\u65E5\u640D\u76CA")}</div>
        <div class="text-2xl font-mono ${k(o)}">${S(o)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${u("drawdown","\u56DE\u64A4")} ${w(r)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Open credit","\u672A\u5BE6\u73FE\u6B0A\u5229\u91D1\uFF08\u9032\u5834\u6536\u6582\uFF09")}</div>
        <div class="text-2xl font-mono">${S(s)}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${Fo(i)}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Win rate \xB7 avg holding","\u52DD\u7387 \xB7 \u5E73\u5747\u6301\u6709")}</div>
        <div class="text-2xl font-mono">${c?`${w(d,1)} \xB7 ${$(p,2)}${L?" \u5929":"d"}`:"\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${c?f:u("Loading performance\u2026","\u7E3E\u6548\u6458\u8981\u8F09\u5165\u4E2D\u2026")}</div>
        </div>
      </div>

      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Total profit (lifetime)","\u7D2F\u8A08\u5DF2\u5BE6\u73FE\u640D\u76CA")}</div>
        <div class="text-2xl font-mono ${k(m)}">${c?S(m):"\u2014"}</div>
        <div class="overview-metric-meta">
          ${c?`<div class="overview-metric-line">${cn(v)}</div>`:""}
          <div class="overview-metric-line">${c?`${y??0} ${u("closed groups","\u7B46\u5DF2\u5E73\u5009\u90E8\u4F4D")}`:""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${Io(x)}</div>
        <div class="text-2xl font-mono ${k(b)}">${c?S(b):"\u2014"}</div>
        <div class="overview-metric-meta">
          ${c?`<div class="overview-metric-line">${cn(C)}</div>`:""}
          <div class="overview-metric-line">${c?Sn(x):""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${u("Realized APR (lifetime)","\u5DF2\u5BE6\u73FE\u5E74\u5316\uFF08\u5B58\u7E8C\u671F\uFF09")}</div>
        <div class="text-2xl font-mono">${c?w(E):"\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line">${c?u("annualized on actual span","\u4F9D\u5BE6\u969B\u5340\u9593\u5E74\u5316"):""}</div>
        </div>
      </div>
      <div class="overview-metric-cell">
        <div class="text-xs text-slate-400">${Oo(x)}</div>
        <div class="text-2xl font-mono">${c?w(T):"\u2014"}</div>
        <div class="overview-metric-meta">
          <div class="overview-metric-line overview-metric-line--hint">${c?$n(x):""}</div>
        </div>
      </div>
    </div>`}function rn(t,{pnl:e=!1,places:n={BTC:5,ETH:4,USDC:2}}={}){let o={BTC:"\u20BF",ETH:"\u25C6",USDC:"$"};return["BTC","ETH","USDC"].map(r=>{let s=l(t[r]),i=s===null?"\u2014":$(s,n[r]??4);return`<span class="inv-chip ${e?k(t[r]):""}"><span class="inv-chip-sym">${o[r]}</span><span class="inv-chip-val font-mono tabular-nums">${i}</span></span>`}).join("")}function $o(t){return St(new Set(H.map(e=>e.id))).map(e=>{let n=g(st(e).short),o=l(t[e]),r=o===null?"\u2014":S(o);return`<div class="inv-mini-row"><span class="inv-mini-label">${n}</span><span class="inv-mini-value font-mono tabular-nums">${r}</span></div>`}).join("")}function mn(t){let{totalEquity:e,dayStart:n,dayPnl:o,dayDrawdown:r,openCredit:s,creditByStrategy:i,summary:c,winRate:d,avgHolding:p,sinceLine:f,lifetimePnl:m,lifetimeNativeByBook:v,closedCount:y,windowLabelDays:x,windowPnl:b,windowNativeByBook:C,lifetimeApr:E,windowApr:T,equityNativeByBook:U,equityUsdByBook:N}=t,h=c!=null?`${w(d,1)} \xB7 ${$(p,2)}${L?" \u5929":"d"}`:"\u2014",R=c?f:u("Loading performance\u2026","\u7E3E\u6548\u6458\u8981\u8F09\u5165\u4E2D\u2026");return`<div class="inv-dashboard">
    <section class="inv-panel inv-panel--hero" aria-label="${u("Account snapshot","\u5E33\u6236\u5FEB\u7167")}">
      <div class="inv-split">
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Total equity","\u7E3D\u6B0A\u76CA")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${S(e)}</span>
          <span class="inv-kpi-foot">${u("USDC equivalent","USDC \u7D04\u7576")} \xB7 ${u("day-start","\u65E5\u521D")} ${S(n)}</span>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Day P&L","\u672C\u65E5\u640D\u76CA")}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${k(o)}">${S(o)}</span>
          <span class="inv-kpi-foot">${u("drawdown","\u56DE\u64A4")} ${w(r)}</span>
        </div>
      </div>
      <div class="inv-equity-dual">${Cn(U,N)}</div>
    </section>

    <section class="inv-panel" aria-label="${u("Open risk","\u672A\u5E73\u5009\u98A8\u96AA")}">
      <div class="inv-split">
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Open credit","\u672A\u5BE6\u73FE\u6B0A\u5229\u91D1")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${S(s)}</span>
          <div class="inv-mini-list">${$o(i)}</div>
        </div>
        <div class="inv-kpi">
          <span class="inv-kpi-label">${u("Win rate \xB7 hold","\u52DD\u7387 \xB7 \u6301\u6709")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${h}</span>
          <span class="inv-kpi-foot">${R}</span>
        </div>
      </div>
    </section>

    <section class="inv-panel" aria-label="${u("Realized performance","\u5DF2\u5BE6\u73FE\u7E3E\u6548")}">
      <h3 class="inv-panel-title">${u("Realized P&L","\u5DF2\u5BE6\u73FE\u640D\u76CA")}</h3>
      <div class="inv-compare">
        <div class="inv-compare-col">
          <span class="inv-compare-tag">${u("Lifetime","\u5B58\u7E8C")}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${k(m)}">${c?S(m):"\u2014"}</span>
          <div class="inv-chips-row inv-chips-row--compact">${c?rn(v,{pnl:!0}):""}</div>
          <span class="inv-kpi-foot">${c?`${y??0} ${u("closed","\u7B46\u5E73\u5009")}`:""}</span>
        </div>
        <div class="inv-compare-col">
          <span class="inv-compare-tag">${u("Last","\u8FD1")} ${x}${L?" \u65E5":"d"}</span>
          <span class="inv-kpi-value font-mono tabular-nums ${k(b)}">${c?S(b):"\u2014"}</span>
          <div class="inv-chips-row inv-chips-row--compact">${c?rn(C,{pnl:!0}):""}</div>
          <span class="inv-kpi-foot">${c?Sn(x):""}</span>
        </div>
      </div>
      <div class="inv-split inv-split--apr">
        <div class="inv-kpi inv-kpi--compact">
          <span class="inv-kpi-label">${u("APR lifetime","\u5E74\u5316\xB7\u5B58\u7E8C")}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${c?w(E):"\u2014"}</span>
        </div>
        <div class="inv-kpi inv-kpi--compact">
          <span class="inv-kpi-label">${u("APR","\u5E74\u5316")} ${x}${L?" \u65E5":"d"}</span>
          <span class="inv-kpi-value font-mono tabular-nums">${c?w(T):"\u2014"}</span>
          <span class="inv-kpi-foot">${c?$n(x):""}</span>
        </div>
      </div>
    </section>
  </div>`}function ut(t,e){let n=l(t);if(n===null)return"\u2014";let o=String(e||"").toUpperCase(),r='<span class="text-slate-500">',s="</span>";return o==="USDC"?`${r}($)${s}\xA0${$(n,4)}`:o==="BTC"?`${r}\u20BF${s}\xA0${$(n,5)}`:o==="ETH"?`${r}\u2666${s}\xA0${$(n,5)}`:$(n,4)}function Se(t){if(!t)return null;let e=String(t.kind||"").toLowerCase(),n=String(t.direction||"").toLowerCase()==="sell";if(e==="option"){let s=l(t.size);return s===null||s===0?null:n?-Math.abs(s):Math.abs(s)}let o=l(t.size_currency);if(o!==null&&o!==0)return n&&o>0?-Math.abs(o):o;let r=l(t.size);return r===null||r===0?null:n&&r>0?-Math.abs(r):r}function Co(t,e,n=null){let o=n??a.groups;if(_t(e,o,t,"short")>1){let d=zt(t,"short");if(d!==null)return $(d,4)}let r=Q(e,t),s=Se(r);if(s!==null)return $(s,4);let i=l(t.quantity);if(i===null)return"\u2014";let c=i>0?-Math.abs(i):i;return $(c,4)}function Y(t,e){return String(e==="long"?t?.long_instrument_name||"":t?.short_instrument_name||"")}function zt(t,e){let n=l(t.quantity);return n===null?null:e==="short"?-Math.abs(n):Math.abs(n)}function _t(t,e,n,o){let r=Y(n,o);if(!r)return 0;let s=String(n?.account_name||""),i=new Set,c=0;for(let d of[t?.trade_groups||[],e?.open||[]])for(let p of d){if(!Ft(p))continue;let f=mt(p);i.has(f)||(i.add(f),Y(p,o)===r&&(s&&String(p?.account_name||"")!==s||c++))}return c}function $e(t,e,n){let o=Y(e,n);if(!o)return null;let r=t?.positions||[],s=String(e?.account_name||"");if(s){let i=r.find(c=>c.instrument_name===o&&String(c.account_name||"")===s);if(i)return i}return r.find(i=>i.instrument_name===o)||null}function Q(t,e){return $e(t,e,"short")}function wo(t,e,n=null){let o=n??a.groups,r=e.short_average_price,s=e.short_mark_price,i=e.short_floating_profit_loss,c=e.short_has_floating_profit_loss,d=e.short_floating_profit_loss_usd,p=e.short_has_floating_profit_loss_usd,f=r==null||r==="",m=s==null||s==="",v=i==null||i==="",y=d==null||d==="",x=_t(t,o,e,"short")>1;if((f||m||v||y||c===void 0||p===void 0)&&t?.positions?.length){let b=Q(t,e);b&&(f&&(r=b.average_price),m&&(s=b.mark_price),x||(v&&(i=b.floating_profit_loss),c===void 0&&(c=b.has_floating_profit_loss),y&&(d=b.floating_profit_loss_usd),p===void 0&&(p=b.has_floating_profit_loss_usd)))}return{...e,short_average_price:r,short_mark_price:s,short_floating_profit_loss:i,short_has_floating_profit_loss:c,short_floating_profit_loss_usd:d,short_has_floating_profit_loss_usd:p}}function vn(t){let e=t.expiration_timestamp_ms;if(e!=null&&e!==""){if(typeof e=="number"&&Number.isFinite(e))return Math.round(e);if(typeof e=="bigint")return Number(e);let n=String(e).trim();if(/^\d+$/.test(n)){let o=Number(n);return Number.isFinite(o)?o:null}}if(t.expiry){let n=luxon.DateTime.fromISO(String(t.expiry),{zone:"utc"});if(n.isValid)return n.toMillis()}return null}function Ce(t){let e=l(t.dte_days)??l(t.dte);if(e!==null)return e;let n=vn(t);if(n===null)return null;let o=luxon.DateTime.fromMillis(n,{zone:"utc"});return o.isValid?o.diff(luxon.DateTime.utc(),"days").days:null}function tt(t){let e=String(t.option_type||"").toLowerCase();if(e==="call")return"Call";if(e==="put")return"Put";let n=String(t.short_instrument_name||"");return/-C$/i.test(n)||n.endsWith("-C")?"Call":"Put"}function yn(t,e){for(let n of["BTC","ETH"]){let o=l(t?.underlying_index_usd?.[n]),r=l(e?.underlying_index_usd?.[n]),s=o>0?o:r>0?r:null;s!==null&&(a.lastUnderlyingIndexUsd[n]=s)}}function ko(t,e){let n={};for(let o of["BTC","ETH"]){let r=l(t?.underlying_index_usd?.[o]),s=l(e?.underlying_index_usd?.[o]),i=l(a.lastUnderlyingIndexUsd[o]),c=r>0?r:s>0?s:i>0?i:null;c!==null&&(n[o]=c)}return n}function Eo(t,e,n){let o=String(n||"").toUpperCase(),r=ko(t,e);return l(r[o])}function q(t){let e=String(t.collateral_currency||"").toUpperCase();if(e==="BTC"||e==="ETH"||e==="USDC")return e;let n=String(t.short_instrument_name||"");return n.includes("_USDC-")?"USDC":n.startsWith("BTC-")?"BTC":n.startsWith("ETH-")?"ETH":String(t.currency||"").toUpperCase()||"BTC"}function hn(t){let e=q(t);return e==="BTC"||e==="ETH"?e:String(t.currency||"BTC").toUpperCase()}function To(t,e){let n=q(e);if(n!=="BTC"&&n!=="ETH")return null;let o=n==="BTC"?"BTC-":"ETH-",r=t?.positions;if(!r?.length)return null;let s=String(e?.account_name||"");for(let i of r){if(s&&String(i.account_name||"")!==s)continue;let c=String(i.instrument_name||""),d=String(i.kind||"").toLowerCase();if(!c.startsWith(o)||d!=="option"&&d!=="future")continue;let p=l(i.index_price);if(p!==null&&p>0)return p}return null}function Ro(t,e,n){let o=hn(t),r=l(a.lastSpotUsd[o]);if(r!==null&&r>0)return r;let s=Eo(e,n,o);if(s!==null&&s>0)return s;let i=Q(e,t),c=l(i?.index_price);if(c!==null&&c>0)return c;let d=To(e,t);return d!==null&&d>0?d:null}function Mt(t,e,n){let o=q(t);if(o==="USDC")return 1;if(o==="BTC"||o==="ETH"){let r=Ro(t,e,n);return r!==null&&r>0?r:null}return null}function _n(t){return Se(t)}function et(t,e,n,o=null){let r=o??a.groups,s=zt(t,n);if(s!==null&&_t(e,r,t,n)>1)return s;let i=$e(e,t,n),c=Se(i);return c!==null?c:s}function nt(t,e,n,o){if(n==="short"&&ke(t,`short_${o}`)){let s=t[`short_${o}`];if(s!=null&&s!=="")return s}return $e(e,t,n)?.[o]??null}function Bo(t,e,n,o=null){let r=l(nt(e,t,n,"average_price")),s=l(nt(e,t,n,"mark_price")),i=et(e,t,n,o);return r===null||s===null||i===null?null:(s-r)*i}function lt(t,e,n,o){let r=Bo(t,e,o,n);if(r===null)return null;let s=Mt(e,t,n);return s===null||s<=0?null:r*s}function Lo(t,e,n=null){let o=n??a.groups;if(_t(t,o,e,"short")>1){let d=Q(t,e);if(!d)return null;let p=l(d.average_price),f=l(d.mark_price),m=zt(e,"short");return p===null||f===null||m===null?null:(f-p)*m}let r=Q(t,e);if(!r)return null;let s=l(r.average_price),i=l(r.mark_price),c=_n(r);return s===null||i===null||c===null?null:(i-s)*c}function Do(t,e){let n=Lo(e,t);if(n!==null)return n;if(t.short_has_floating_profit_loss){let o=l(t.short_floating_profit_loss);if(o!==null)return o}return null}function Uo(t,e,n){let o=n??a.groups;if(_t(t,o,e,"short")>1){let p=Q(t,e);if(!p)return null;let f=l(p.average_price),m=l(p.mark_price),v=zt(e,"short");if(f===null||m===null||v===null)return null;let y=Mt(e,t,n);return y===null||y<=0?null:(m-f)*v*y}let r=Q(t,e);if(!r)return null;let s=l(r.average_price),i=l(r.mark_price),c=_n(r);if(s===null||i===null||c===null)return null;let d=Mt(e,t,n);return d===null||d<=0?null:(i-s)*c*d}function sn(t,e,n){let o=Uo(e,t,n);if(o!==null)return o;if(t.short_has_floating_profit_loss_usd){let r=l(t.short_floating_profit_loss_usd);if(r!==null)return r}return null}function Ao(t){let e=l(t);return e===null?"\u2014":`<span class="text-slate-500">($)</span>\xA0${new Intl.NumberFormat("en-US",{maximumFractionDigits:2,minimumFractionDigits:2}).format(e)}`}function an(t){let e=l(t.unrealized_usdc_estimate);if(e!==null)return e;let n=l(t.entry_credit),o=l(t.current_debit);return n!==null&&o!==null?n-o:null}function Po(t,e,n){let o=lt(t,e,n,"short"),r=lt(t,e,n,"long");return o===null&&r===null?null:(o||0)+(r||0)}function No(t,e,n){let o=lt(t,e,n,"short"),r=lt(t,e,n,"long");return o===null||r===null?null:o+r}function xt(t,e,n){return A(t)==="bull_put_spread"?No(e,t,n)??an(t)??Po(e,t,n)??sn(t,e,n):sn(t,e,n)??an(t)}function rt(t,e,n){return l(t.entry_credit)}function Ht(t,e){let n=String(e||"").toUpperCase();return n==="USDC"?t===null?"\u2014":Ao(t):t===null?"\u2014":n==="BTC"?`<span class="text-slate-500">\u20BF</span>\xA0${$(t,8)}`:n==="ETH"?`<span class="text-slate-500">\u2666</span>\xA0${$(t,8)}`:$(t,8)}function we(t,e,n){if(A(t)!=="bull_put_spread")return Do(t,e);let o=l(t.unrealized_coin_native);if(o!==null)return o;let r=xt(t,e,n),s=Mt(t,e,n);return r===null||s===null||s<=0?null:r/s}function $(t,e=4){let n=l(t);return n===null?"\u2014":(e>=8?F.num8:F.num4).format(n)}function g(t){return String(t??"").replace(/[&<>"']/g,e=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[e])}function ke(t,e){return Object.prototype.hasOwnProperty.call(t||{},e)}function gt(t,e){let n=String(t||"").toUpperCase(),o=e?.portfolio||{},r=l(o?.equity_by_book?.[n]);if(r!==null)return r;let s=l(e?.accounts?.[n]?.equity);if(s===null)return null;if(n==="USDC")return s;let i=l(e?.underlying_index_usd?.[n])??l(a.lastSpotUsd?.[n]);return i===null||i<=0?null:s*i}function xn(t){let e={};for(let n of z)e[n]=gt(n,t);return e}function Ee(t,e,n){let o=l(t?.day_net_flow_usdc);return l(t?.day_pnl_usdc_ex_flow)??l(t?.day_pnl_usdc_ex_flow_ex_spot)??(e!==null&&n!==null?e-n-(o??0):null)}function gn(t,e,n,o){let r=String(t||"").toUpperCase(),s=e?.portfolio||{},i=l(s?.day_net_flow_usdc_by_book?.[r]);return l(s?.day_pnl_usdc_ex_flow_by_book?.[r])??l(s?.day_pnl_usdc_ex_flow_ex_spot_by_book?.[r])??(n!==null&&o!==null?n-o-(i??0):null)}function k(t){let e=l(t);return e===null||e===0?"":e>0?"pnl-pos":"pnl-neg"}function ln(t){if(t==null)return"\u2014";let e;return typeof t=="number"?e=luxon.DateTime.fromMillis(t,{zone:"utc"}):e=luxon.DateTime.fromISO(String(t),{zone:"utc"}),e.isValid?e.toLocal().toFormat("yyyy-LL-dd HH:mm"):"\u2014"}function bn(t){if(t==null)return"\u2014";let e;return typeof t=="number"?e=luxon.DateTime.fromMillis(t,{zone:"utc"}):e=luxon.DateTime.fromISO(String(t),{zone:"utc"}),e.isValid?e.toLocal().toFormat("yyyy-LL-dd"):"\u2014"}function Io(t){let e=Math.round(t??30);return u(`Total profit (rolling ${e}d)`,`\u5DF2\u5BE6\u73FE\u640D\u76CA\uFF08\u6EFE\u52D5 ${e} \u65E5\u8996\u7A97\uFF09`)}function Oo(t){let e=Math.round(t??30);return u(`Realized APR (rolling ${e}d)`,`\u5DF2\u5BE6\u73FE\u5E74\u5316\uFF08\u6EFE\u52D5 ${e} \u65E5\u8996\u7A97\uFF09`)}function Sn(t){let e=Math.round(t??30);return u(`Closes in last ${e}d only`,`\u50C5\u8A08\u6700\u8FD1 ${e} \u65E5\u5167\u5E73\u5009`)}function $n(t){let e=Math.round(t??30);return u(`Last ${e}d closes \xF7 ledger total equity`,`\u8FD1 ${e} \u65E5\u5E73\u5009 \xF7 \u7576\u65E5\u7E3D\u6B0A\u76CA`)}function qt(){let t=l(a.status?.portfolio?.total_equity_usdc);return t!==null&&t>0?t:null}function Z(t){let e=l(t.closed_timestamp_ms);if(e!==null)return e;if(t.closed_timestamp){let n=luxon.DateTime.fromISO(String(t.closed_timestamp),{zone:"utc"});if(n.isValid)return n.toMillis()}return null}function Vt(t){let e=String(t.currency||"").toUpperCase()||"Option";if(A(t)==="bull_put_spread")return L?`${e} \u8CE3\u6B0A\u50F9\u5DEE`:`${e} put spread`;let o=tt(t);if(L){let r=o.toLowerCase()==="call"?"\u8CB7\u6B0A":"\u8CE3\u6B0A";return`${e} \u8CE3\u51FA${r}`}return`${e} short ${o.toLowerCase()}`}function Mo(t,{places:e={BTC:5,ETH:4,USDC:2},pnl:n=!1}={}){let o={BTC:"\u20BF",ETH:"\u2666",USDC:"($)"};return`<span class="native-book-breakdown">${["BTC","ETH","USDC"].map(s=>{let i=l(t[s]),c=i===null?"\u2014":$(i,e[s]??4),d=n?` ${k(t[s])}`:"";return`<span class="native-book-item"><span class="native-book-symbol text-slate-500">${o[s]}</span> <span class="font-mono tabular-nums${d}">${c}</span></span>`}).join("")}</span>`}function cn(t){return Mo(t,{pnl:!0})}function Cn(t,e){let n={BTC:"\u20BF",ETH:"\u2666",USDC:"($)"},o={BTC:5,ETH:4,USDC:2},r=["BTC","ETH","USDC"].map(s=>{let i=l(t?.[s]),c=l(e?.[s]);if(i===null&&c===null)return null;if(s==="USDC"){let f=c??i;return f===null?null:`<div class="book-equity-dual-row">
          <span class="native-book-symbol text-slate-500">${n[s]}</span>
          <span class="font-mono tabular-nums">${S(f)}</span>
        </div>`}let d=i===null?"\u2014":$(i,o[s]),p=c===null?"\u2014":S(c);return`<div class="book-equity-dual-row">
        <span class="native-book-symbol text-slate-500">${n[s]}</span>
        <span class="font-mono tabular-nums">${d}</span>
        <span class="book-equity-dual-sep text-slate-600" aria-hidden="true">\xB7</span>
        <span class="font-mono tabular-nums text-slate-400">${p}</span>
      </div>`}).filter(Boolean);return r.length?`<div class="book-equity-dual-breakdown">${r.join("")}</div>`:'<span class="text-slate-500">\u2014</span>'}function Fo(t){return`<div class="open-credit-breakdown">${St(new Set(H.map(n=>n.id))).map(n=>{let o=g(st(n).short),r=l(t[n]),s=r===null?"\u2014":S(r);return`<div class="open-credit-row"><span class="open-credit-label text-slate-500">${o}</span><span class="open-credit-value font-mono tabular-nums text-slate-300">${s}</span></div>`}).join("")}</div>`}function wn(t=30){let e=`/api/realized_summary?days=${t}`,n=qt();return n!==null&&(e+=`&effective_capital_usdc=${encodeURIComponent(String(n))}`),e}function Te(t=30,{sections:e=null}={}){let n=`/api/dashboard_bundle?days=${t}`;e&&(n+=`&sections=${encodeURIComponent(e)}`);let o=qt();return o!==null&&(n+=`&effective_capital_usdc=${encodeURIComponent(String(o))}`),n}function Re(t){t?.groups&&(a.groups=t.groups),t?.status&&(a.status=t.status,a.statusErrorOnce=!1,a.dataFreshness.source="live",a.dataFreshness.live=!0,a.dataFreshness.statusMs=0),t?.realized_summary&&(a.report=t.realized_summary)}function kn(t,e){let n=null,o=r=>{if(!r||l(r.realized_pnl)===null||!Yt(r,a.status,e))return;let s=ot(r);s===null||s<=0||(n===null||s<n)&&(n=s)};for(let r of e?.closed||[])o(r);for(let r of t?.recent_closed_trades||[])o(r);return n}function un(t){if(!t||tt(t).toLowerCase()!=="call")return!1;let e=l(t.covered_underlying_quantity);return e!==null&&e>0||String(t.short_label||"").startsWith("covered_call-")||String(t.account_name||"")==="covered_call"?!0:String(t.account_env_file||"").includes(".env.covered_call")}function K(t){let e=String(t||"").trim().toLowerCase().replaceAll("-","_").replaceAll(" ","_");return e?{naked:"naked_short",naked_put:"naked_short",naked_call:"naked_short",short_put:"naked_short",short_call:"naked_short",shortput:"naked_short",shortcall:"naked_short",naked_short_put:"naked_short",naked_short_call:"naked_short",put_spread:"bull_put_spread",short_put_spread:"bull_put_spread",bullputspread:"bull_put_spread",bull_put:"bull_put_spread",coveredcall:"covered_call"}[e]||e:""}function A(t){let e=K(t?.strategy),n=String(t?.long_instrument_name||"").trim();return(e===""||e==="naked_short")&&n&&tt(t).toLowerCase()==="put"?"bull_put_spread":e==="naked_short"&&un(t)?"covered_call":e||(tt(t).toLowerCase()==="call"&&un(t)?"covered_call":"naked_short")}function st(t){let e=K(t);if(G[e]){let o=G[e];return!_||!L?o:{...o,title:o.titleZh||o.title,short:o.shortZh||o.short,chipShort:o.chipShortZh||o.chipShort||o.shortZh||o.short,description:o.descriptionZh||o.description}}let n=e?e.replaceAll("_"," "):"\u2014";return{id:e||"",title:n,short:n,chipShort:n,accentClass:"border-slate-700",description:""}}function Gt(t){return st(t).title}function zo(t){let e=K(t);return e==="naked_short"?"chip-strategy-naked":e==="bull_put_spread"?"chip-strategy-spread":e==="covered_call"?"chip-strategy-covered":"chip-strategy-unknown"}function X(t,{compact:e=!1}={}){let n=st(t),o=zo(n.id||t),r=e&&n.chipShort||n.short;return`<span class="chip ${o}${e?" chip--compact":""}">${g(r)}</span>`}function mt(t){return[String(t?.account_name||""),String(t?.group_id||""),String(t?.short_instrument_name||"")].join("\0")}var Ho=["realized_pnl_collateral_native","short_entry_average_price","short_close_average_price","entry_index_usd","close_index_usd","realized_close_debit","realized_close_fee","entry_fee","entry_credit","collateral_currency","strategy","option_type","covered_underlying_quantity","realized_apr_on_equity","close_book_equity","quantity","realized_pnl","contract_size","short_strike"];function dn(t){return!(t==null||t===""||typeof t=="number"&&!Number.isFinite(t))}function qo(t,e){let n={...e,...t};for(let o of Ho)dn(t[o])?n[o]=t[o]:dn(e[o])&&(n[o]=e[o]);return n}function Wt(t){let e=new Map;for(let n of t||[]){let o=mt(n),r=e.get(o);e.set(o,r?qo(r,n):n)}return[...e.values()]}function Ft(t){return String(t?.status||"open").toLowerCase()!=="closed"}function jt(t){return String(t?.status||"").toLowerCase()==="closed"?!0:Z(t)!==null}var Vo=3e5;function Go(t,e){let n=new Set;for(let o of bt(t,e)){let r=String(o?.short_instrument_name||"").trim();r&&n.add(r)}return n}function Wo(t,e,n){if(!jt(t)||String(t?.close_reason||"").toLowerCase()!=="reconciled_external")return!1;let o=ot(t),r=Z(t);if(o===null||r===null||r<=o||r-o>Vo)return!1;let s=String(t?.short_instrument_name||"").trim();return s?Go(e,n).has(s):!1}function Yt(t,e,n){return jt(t)&&!Wo(t,e,n)}function bt(t,e){let n=[],o=new Set;for(let r of t?.trade_groups||[]){if(!Ft(r))continue;let s=mt(r);o.has(s)||(o.add(s),n.push(r))}for(let r of e?.open||[]){if(!Ft(r))continue;let s=mt(r);o.has(s)||(o.add(s),n.push(r))}return n.map(r=>wo(t,r,e))}function En(t,e,n=20,o=null){let r=o??a.status,s=Wt([...e?.closed||[],...t?.recent_closed_trades||[]]).filter(i=>Yt(i,r,e));return s.sort((i,c)=>(Z(c)||0)-(Z(i)||0)),s.slice(0,n)}function Be(t,e){return En(t,e,500)}function St(t){let e=H.map(r=>r.id),n=e.filter(r=>t.has(r)),o=[...t].filter(r=>!e.includes(r)).sort();return n.concat(o)}function jo(t){let e=String(t||"").match(/-([0-9]+(?:\.[0-9]+)?)-[CP]$/i);return e?l(e[1]):null}function J(t,e){let n=l(e==="long"?t?.long_strike:t?.short_strike);return n!==null?n:jo(Y(t,e))}function dt(t){let e=l(t);return e===null?"\u2014":S(e,0)}function Le(t){let e=J(t,"short"),n=J(t,"long");return e===null||n===null?null:e-n}function Zt(t,e,n){let o=l(nt(t,e,"short",n)),r=l(nt(t,e,"long",n));return o===null||r===null?null:o-r}function De(t){let e=String(t?.long_instrument_name||"").trim();if(e)return u(`Long ${e}`,`\u8CB7\u817F ${e}`);let n=l(t?.covered_underlying_quantity);return n!==null&&n>0?u(`Covered ${$(n,4)} ${String(t.currency||"").toUpperCase()}`,`\u5099\u514C ${$(n,4)} ${String(t.currency||"").toUpperCase()}`):u("Single short leg","\u55AE\u908A\u8CE3\u51FA")}function vt(t){let e=String(t?.account_name||"").trim();return e?`Account ${e}`:""}function $t(t){let e=l(t?.holding_days);if(e!==null)return e;let n=Z(t),o=ot(t);return n===null||o===null||o<=0?null:Math.max(n-o,0)/864e5}function ot(t){let e=l(t?.entry_timestamp_ms);if(e!==null)return e;if(t?.entry_timestamp){let n=luxon.DateTime.fromISO(String(t.entry_timestamp),{zone:"utc"});if(n.isValid)return n.toMillis()}return null}function Yo(t){let e=ot(t),n=vn(t);return e===null||n===null||n<=e?null:(n-e)/864e5}function Ue(t,e){let n=String(e||"USDC").toUpperCase(),o=l(t?.accounts?.[n]?.equity);return o===null||o<=0?null:o}function D(t){return q(t)}function Ct(t,e){let n=D(t);return n==="USDC"?null:l(e?.underlying_index_usd?.[n])??l(a.groups?.underlying_index_usd?.[n])??l(a.lastSpotUsd?.[n])??l(t?.close_index_usd)}function Ae(t,e){let n=l(t?.realized_pnl_collateral_native);if(n!==null)return n;if(D(t)==="USDC")return l(t?.realized_pnl);let r=l(t?.quantity);if(r===null||r<=0)return null;let s=l(t?.entry_index_usd),i=l(t?.close_index_usd)??s,c=l(t?.entry_fee)??0,d=l(t?.realized_close_fee)??0,p=null,f=null,m=l(t?.short_entry_average_price),v=l(t?.short_close_average_price),y=l(t?.entry_credit),x=l(t?.realized_close_debit);if(m!==null&&m>0?(p=m*r,(s===null||s<=0)&&y!==null&&(s=(y+c)/(m*r))):y!==null&&s!==null&&s>0&&(p=(y+c)/s),v!==null&&v>0?(f=v*r,(i===null||i<=0)&&x!==null&&(i=Math.max(0,x-d)/(v*r))):x!==null&&i!==null&&i>0&&(f=Math.max(0,x-d)/i),p===null||f===null)return null;let b=0;if(c>0){if(s===null||s<=0)return null;b+=c/s}if(d>0){if(i===null||i<=0)return null;b+=d/i}return p-f-b}function Zo(t){let e=D(t);return e==="BTC"||e==="ETH"}function Jt(t,e){if(D(t)==="USDC")return l(t?.realized_pnl);let o=Ae(t,e),r=Ct(t,e);return o!==null&&r!==null&&r>0?o*r:null}function wt(t,e){let n=D(t);if(n==="USDC")return l(t?.realized_pnl);let o=Ae(t,e);if(o!==null)return o;let r=l(t?.realized_pnl);if(r===null)return null;let s=l(t?.close_index_usd)??l(e?.underlying_index_usd?.[n])??l(a.groups?.underlying_index_usd?.[n])??l(a.lastSpotUsd?.[n]);return s===null||s<=0?null:r/s}function Jo(t){let e=l(t?.contract_size);return e!==null&&e>0?e:1}function Ko(t,e){let n=l(t?.quantity);if(n===null||n<=0)return null;let o=Jo(t),r=A(t),s=l(t?.estimated_im_collateral);if(r==="bull_put_spread"&&s!==null&&s>0)return s/n;if(D(t)==="USDC"){if(tt(t).toLowerCase()==="call"){let d=Qo(t,e)??Qt(t,e)??Ct(t,e)??Ln(t,e);if(d!==null&&d>0)return d}else{let d=J(t,"short");if(d!==null&&d>0)return d}return null}return o}function Tn(t,e){let n=Ko(t,e),o=l(t?.quantity);if(n===null||o===null||o<=0)return null;let r=A(t);if(r==="covered_call"){let i=l(t?.covered_underlying_quantity);return i!==null&&i>0?i:o}return D(t)==="USDC"||r==="bull_put_spread",n*o}function Kt(t,e){return Tn(t,e)}function Rn(t,e){let n=wt(t,e),o=$t(t),r=Kt(t,e);return n===null||r===null||r<=0||o===null||o<=0?null:n/r*(365/o)}function Xo(t,e){let n=D(t);if(!Zo(t)){let c=l(t?.realized_pnl);return c===null?"\u2014":S(c)}let o=Ae(t,e);if(o===null){let c=l(t?.realized_pnl);return c===null?"\u2014":S(c)}let r=Jt(t,e),i=`${$(o,n==="BTC"?5:4)} ${n}`;return L?`${S(r)}\uFF08${i}\uFF09`:`${S(r)} (${i})`}function Bn(t,e){let n=l(t);return n===null?`\u2014 ${e||""}`.trim():`${new Intl.NumberFormat("en-US",{maximumFractionDigits:8}).format(n)} ${e}`}function Ot(t,e,n){let o=S(t);if(e===null||!n||n==="USDC")return o;let r=Bn(e,n);return L?`${o}\uFF08${r}\uFF09`:`${o} (${r})`}function Xt(t,e,n){let o=S(t);if(e===null||!n||n==="USDC")return o;let r=g(Bn(e,n));return`<span class="open-position-value-stack"><span class="open-position-value-line">${o}</span><span class="open-position-value-sub">${r}</span></span>`}function Pe(t,e){let n=l(t),o=l(e);return n===null||o===null||o<=0?null:n/o}function Qt(t,e){let n=D(t);return n==="USDC"?null:l(t?.entry_index_usd)??l(e?.underlying_index_usd?.[n])??l(a.groups?.underlying_index_usd?.[n])??l(a.lastSpotUsd?.[n])}function Ln(t,e){let n=D(t);return n==="USDC"?null:l(t?.close_index_usd)??l(e?.underlying_index_usd?.[n])??l(a.groups?.underlying_index_usd?.[n])??l(a.lastSpotUsd?.[n])??l(t?.entry_index_usd)}function Qo(t,e){let n=hn(t);if(n!=="BTC"&&n!=="ETH")return null;let o=[l(t?.entry_index_usd),l(t?.close_index_usd),l(e?.underlying_index_usd?.[n]),l(a.groups?.underlying_index_usd?.[n]),l(a.lastSpotUsd?.[n]),J(t,"short")];for(let r of o)if(r!==null&&r>100)return r;return null}function Ne(t,e){let n=$t(t);if(n===null||n<=0)return null;let o=l(t?.realized_apr_on_equity)??l(t?.realized_annualized_return);return o!==null?o:Rn(t,e)}function tr(t,e,n=null){let o=n??a.groups;if(A(t)==="bull_put_spread"){let i=et(t,e,"short",o),c=et(t,e,"long",o);if(i===null&&c===null){let p=l(t.quantity);return p===null?null:`${$(-Math.abs(p),4)} / ${$(Math.abs(p),4)}`}let d=[];return i!==null&&d.push($(i,4)),c!==null&&d.push($(c,4)),d.length?d.join(" / "):null}if(!jt(t)){let i=Co(t,e,o);return i==="\u2014"?null:i}let s=l(t.quantity);return s===null?null:$(-Math.abs(s),4)}function er(t,e){let n=l(t?.entry_credit);if(n===null)return null;let o=l(t?.entry_fee)??0,r=D(t),s=l(t?.short_entry_average_price),i=l(t?.quantity),c=Qt(t,e),d=n;if(o>0&&s!==null&&s>0&&i!==null&&i>0&&c!==null&&c>0){let p=s*i*c,f=Math.max(.01,Math.abs(p)*.001);Math.abs(p-n)<=f?d=n-o:Math.abs(p-(n+o))<=f&&(d=n)}return r==="USDC"?d:c===null||c<=0?null:d/c}function te(t,e){let n=Yo(t),o=Tn(t,e),r=er(t,e);return r===null||r<=0||n===null||n<=0||o===null||o<=0?l(t?.entry_net_apr):r/o*(365/n)}function ee(t){return l(t?.entry_fee)}function Ie(t,e){return Pe(ee(t),Qt(t,e))}function ne(t){let e=l(t?.current_close_fee);return e!==null&&e>0?e:l(t?.realized_close_fee)}function Oe(t,e){let n=l(t?.current_close_fee),o=n!==null&&n>0?Ct(t,e):Ln(t,e);return Pe(ne(t),o)}function oe(t,e){return Pe(l(t?.entry_credit),Qt(t,e))}function nr(t,e){let n=[],o=new Set,r=s=>{if(!s)return;let i=mt(s);o.has(i)||(o.add(i),n.push(s))};for(let s of t?.trade_groups||[])r(s);for(let s of e?.open||[])r(s);for(let s of e?.closed||[])r(s);return n}function Dn(t,e){return Wt(nr(t,e)).filter(n=>Ft(n)).sort((n,o)=>(ot(o)||0)-(ot(n)||0))}function Un(t,e,n){return En(e,n,500,t)}function Me(t,e,n){let o=t.length,r=Math.max(1,Math.ceil(o/n)),s=Math.min(Math.max(1,e),r),i=(s-1)*n;return{rows:t.slice(i,i+n),page:s,totalPages:r,total:o,start:o?i+1:0,end:Math.min(i+n,o)}}function Fe(t,e){let{page:n,totalPages:o,total:r,start:s,end:i}=e;if(r<=ft)return"";let c=n<=1,d=n>=o,p=u(`${s}\u2013${i} of ${r} \xB7 page ${n} of ${o}`,`${s}\u2013${i} / \u5171 ${r} \u7B46 \xB7 \u7B2C ${n} / ${o} \u9801`);return`<div class="activity-pagination" data-activity-section="${g(t)}">
      <button type="button" class="filter-chip activity-page-btn" data-activity-section="${g(t)}" data-direction="prev"${c?" disabled":""}>${u("Prev","\u4E0A\u4E00\u9801")}</button>
      <span class="activity-pagination-label">${g(p)}</span>
      <button type="button" class="filter-chip activity-page-btn" data-activity-section="${g(t)}" data-direction="next"${d?" disabled":""}>${u("Next","\u4E0B\u4E00\u9801")}</button>
    </div>`}function or(t){let e=String(t?.currency||"").toUpperCase()||"Option",n=String(t?.short_instrument_name||"");if(n){let o=n.split("-").slice(-2).join(" ");return`${e} ${o}`.trim()}try{return Vt(t)}catch{return`${e} trade`}}function ge(t){return t.filter(e=>e).map(e=>typeof e=="string"?`<span>${g(e)}</span>`:`<span>${g(e[0])} <strong>${g(String(e[1]))}</strong></span>`).join("")}function An(t,e,n){let o=A(t),r=D(t)||"\u2014",s=te(t,e),i=ee(t),c=ne(t),d=l(t.entry_credit),p=Ie(t,e),f=Oe(t,e),m=oe(t,e),v=ot(t),y=jt(t),x=Jt(t,e),b=$t(t),C=y?Ne(t,e):null,E=tr(t,e,n),T=or(t),U=d===null?"\u2014":Ot(d,m,r),N=s===null?"\u2014":w(s,1),h=i===null?null:Ot(i,p,r),R=[[u("Opened","\u958B\u5009"),ln(v)],E!==null?[u("Amount","\u6578\u91CF"),E]:null,h?[u("Entry fee","\u9032\u5834\u624B\u7E8C\u8CBB"),h]:null].filter(Boolean),I=`<div class="activity-entry-metrics">
      <div class="activity-entry-metric">
        <span class="activity-entry-metric-label">${u("Credit","\u6536\u6B0A\u5229\u91D1")}</span>
        <span class="activity-entry-metric-value ${k(d)}">${g(U)}</span>
      </div>
      <div class="activity-entry-metric">
        <span class="activity-entry-metric-label">${u("Net APR","\u6DE8\u5E74\u5316\u5831\u916C\u7387")}</span>
        <span class="activity-entry-metric-value ${k(s)}">${g(N)}</span>
      </div>
    </div>
    <div class="activity-phase-meta activity-phase-meta-secondary">
      ${ge(R)}
    </div>`,M="";if(y){let j=[[u("Closed","\u5E73\u5009"),ln(Z(t))],c!==null?[u("Close fee","\u5E73\u5009\u624B\u7E8C\u8CBB"),Ot(c,f,r)]:null,b!==null?[u("Held","\u6301\u6709"),`${$(b,1)}${L?" \u5929":"d"}`]:null].filter(Boolean),ye=x!==null?`<span class="activity-closed-pnl-value ${k(x)}">${Xo(t,e)}</span>`:'<span class="activity-closed-pnl-value activity-closed-pnl-value-missing">\u2014</span>',Nt=C!==null?`<span class="activity-closed-pnl-value ${k(C)}">${w(C,1)}</span>`:'<span class="activity-closed-pnl-value activity-closed-pnl-value-missing">\u2014</span>';M=`${`<div class="activity-closed-metrics">
        <div class="activity-closed-pnl">
          <span class="activity-closed-pnl-label">${u("Realized PnL","\u5DF2\u5BE6\u73FE\u640D\u76CA")}</span>
          ${ye}
        </div>
        <div class="activity-closed-pnl">
          <span class="activity-closed-pnl-label">${u("Realized APR","\u5BE6\u73FE\u5E74\u5316\u5831\u916C")}</span>
          ${Nt}
        </div>
      </div>`}<div class="activity-phase-meta activity-phase-meta-secondary">${ge(j)}</div>`}else{let j=[c!==null?[u("Est. close fee","\u9810\u4F30\u5E73\u5009\u8CBB"),Ot(c,f,r)]:null].filter(Boolean);M=`<div class="activity-phase-meta">
        <span class="activity-status-pill is-open">${u("Open","\u6301\u5009\u4E2D")}</span>
        ${j.length?ge(j):`<span>${u("Est. close fee","\u9810\u4F30\u5E73\u5009\u8CBB")} <strong>\u2014</strong></span>`}
      </div>`}let W=!_&&vt(t)?vt(t):"";return`
    <li class="activity-card">
      <div class="activity-card-head">
        ${X(o)}
        <span class="activity-card-title">${g(T)}</span>
        <span class="text-[11px] text-slate-500">${g(r)}</span>
        ${W?`<span class="text-[11px] text-slate-500">${g(W)}</span>`:""}
      </div>
      <div class="activity-card-instrument">${g(t.short_instrument_name||"")}</div>
      <div class="activity-lifecycle">
        <div class="activity-phase activity-phase-entry">
          <div class="activity-phase-label">${u("Entry","\u9032\u5834")}</div>
          ${I}
        </div>
        <div class="activity-phase-divider" aria-hidden="true"></div>
        <div class="activity-phase activity-phase-exit">
          <div class="activity-phase-label">${u("Exit","\u51FA\u5834")}</div>
          ${M}
        </div>
      </div>
    </li>`}function O(t,e){let n=document.getElementById(t);n&&(n.textContent=e)}function B(t){let e=document.getElementById("toast");e&&(e.textContent=t,e.classList.remove("hidden"),clearTimeout(B._t),B._t=setTimeout(()=>e.classList.add("hidden"),5e3))}function ct(t){return new Promise(e=>setTimeout(e,t))}async function Pn(t,e){let n=0,o=Math.max(1,Math.min(e||1,t.length));async function r(){for(;;){let s=n++;if(s>=t.length)break;await t[s]()}}await Promise.all(Array.from({length:o},()=>r()))}async function P(t,e={}){let n=Xe(t),o=on+1;for(let r=0;r<o;r++){let s;try{s=await fetch(n,e)}catch(c){if(r<o-1){await ct(xe*(r+1));continue}throw c}if(s.ok)return s.json();let i=`${s.status} ${s.statusText}`;try{let c=await s.json();c?.detail&&(i=`${s.status} ${c.detail}`)}catch{}if(nn.has(s.status)&&r<o-1){await ct(xe*(r+1));continue}throw new Error(i)}}function ie(){return{responsive:!0,maintainAspectRatio:!1,animation:!1,interaction:{mode:"nearest",intersect:!1},plugins:{legend:{labels:{color:"rgb(203 213 225)",boxWidth:12,padding:8}},tooltip:{backgroundColor:"rgba(15,23,42,0.95)",borderColor:"rgb(51,65,85)",borderWidth:1,titleColor:"rgb(226,232,240)",bodyColor:"rgb(226,232,240)"}},scales:{x:{type:"time",time:{tooltipFormat:"yyyy-LL-dd HH:mm"},grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)"}},y:{grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)"}}}}}function Tt(t){let e=a.charts[t];if(!e)return;let n=e.canvas;e.destroy(),a.charts[t]=null,n&&(n.removeAttribute("width"),n.removeAttribute("height"),n.style.width="",n.style.height="")}function Rt(t){let e=document.getElementById(t);return e?e.getContext("2d"):null}function re(){Object.values(a.charts).forEach(t=>{try{t?.resize?.()}catch{}})}function Bt(){requestAnimationFrame(()=>{re(),window.setTimeout(re,80),window.setTimeout(re,320)})}var Nn=!1;function zn(){Nn||typeof ResizeObserver>"u"||(Nn=!0,document.querySelectorAll(".chart-panel-canvas").forEach(t=>{t.querySelector("canvas")?.id&&new ResizeObserver(()=>re()).observe(t)}))}function ae(){let t=`/api/apr_series?window_days=${a.aprWindow}`,e=qt();return e!==null&&(t+=`&effective_capital_usdc=${encodeURIComponent(String(e))}`),t}function Hn(){let t=luxon.DateTime.now().toUTC().startOf("day");return{min:t.minus({days:Math.max(a.aprWindow,30)}).toMillis(),max:t.toMillis()}}function sr(t){let e=document.getElementById(t);return e?e.closest(".chart-panel-canvas")||e.parentElement:null}function Lt(t,{empty:e,message:n=""}={}){let o=sr(t);if(!o)return;let r=o.querySelector(".chart-empty-overlay");if(!e){r?.remove(),o.classList.remove("chart-panel-canvas--empty");return}o.classList.add("chart-panel-canvas--empty"),r||(r=document.createElement("div"),r.className="chart-empty-overlay",o.appendChild(r)),r.textContent=n}var In={realized:{en:"No closed positions yet \u2014 this chart fills in after the first close.",zh:"\u5C1A\u7121\u5E73\u5009\u7D00\u9304 \u2014 \u9996\u6B21\u5E73\u5009\u5F8C\u6B64\u5716\u8868\u624D\u6703\u958B\u59CB\u7D2F\u7A4D\u3002"},apr:{en:"Rolling APR needs closed trades and daily equity snapshots.",zh:"\u6EFE\u52D5\u5E74\u5316\u9700\u6709\u5E73\u5009\u7D00\u9304\u8207\u6BCF\u65E5\u6B0A\u76CA\u5FEB\u7167\u3002"}};function ir(t){let e=In[t]||In.realized;return u(e.en,e.zh)}function ar({yPercent:t=!1,chartType:e="line"}={}){let n=Hn(),o=ie(),r=t?-.1:-50,s=t?.1:50;return{...o,plugins:{...o.plugins,legend:{display:!1},tooltip:{enabled:!1}},scales:{x:{...o.scales.x,...n,display:!0,offset:e==="bar",time:{unit:"day",round:"day",tooltipFormat:"yyyy-LL-dd"}},y:{...o.scales.y,display:!0,min:r,max:s,ticks:{...o.scales.y.ticks,maxTicksLimit:6,...t?{callback:i=>w(i,1)}:{}}}}}}function Et(t,e,{yPercent:n=!1,chartType:o="line",messageKind:r="realized"}={}){let s=Rt(t);if(!s)return;Tt(e),Lt(t,{empty:!0,message:ir(r)});let i=Hn(),c=[{x:i.min,y:0},{x:i.max,y:0}];a.charts[e]=new Chart(s,{type:"line",data:{datasets:[{label:u("No realized history yet","\u5C1A\u7121\u5DF2\u5BE6\u73FE\u7D00\u9304"),data:c,borderWidth:1,pointRadius:0,borderColor:"rgba(148, 163, 184, 0.35)",backgroundColor:"transparent"}]},options:ar({yPercent:n,chartType:o})})}function ze(){return a.bookFilter==="ALL"?z:[a.bookFilter]}function qn(t,e,n){let o=Object.fromEntries(H.map(r=>[r.id,0]));for(let r of t||[]){let s=A(r);if(!G[s])continue;let i=rt(r,e,n);i!==null&&(o[s]+=i)}return o}function Vn(t,e,n=null){let o=n??a.status;return Wt([...e?.closed||[],...t?.recent_closed_trades||[]]).filter(r=>Yt(r,o,e)).filter(r=>l(r?.realized_pnl)!==null)}function Gn(t,e,n){let o={BTC:0,ETH:0,USDC:0};for(let r of Vn(t,e)){let s=D(r);if(s!=="BTC"&&s!=="ETH"&&s!=="USDC")continue;let i=wt(r,n);i!==null&&(o[s]+=i)}return o}function Wn(t,e,n,o){let r={BTC:0,ETH:0,USDC:0},s=o??30,i=Date.now()-s*24*3600*1e3;for(let c of Vn(t,e)){let d=Z(c);if(d===null||d<i)continue;let p=D(c);if(p!=="BTC"&&p!=="ETH"&&p!=="USDC")continue;let f=wt(c,n);f!==null&&(r[p]+=f)}return r}function jn(t){let e={},n=!1;for(let o of z){let r=Ue(t,o);e[o]=r,r!==null&&(n=!0)}if(!n){let{portfolio:o}=yt();for(let r of z){if(r==="USDC"){e[r]=l(o?.equity_by_book?.[r]);continue}let s=l(o?.equity_by_book?.[r]),i=l(t?.underlying_index_usd?.[r])??l(a.lastSpotUsd?.[r]);e[r]=s!==null&&i!==null&&i>0?s/i:null}}return e}function lr(){return{responsive:!0,maintainAspectRatio:!1,animation:!1,interaction:{mode:"index",intersect:!1},plugins:{legend:{labels:{color:"rgb(203 213 225)",boxWidth:12,padding:8}},tooltip:{backgroundColor:"rgba(15,23,42,0.95)",borderColor:"rgb(51,65,85)",borderWidth:1,titleColor:"rgb(226,232,240)",bodyColor:"rgb(226,232,240)"}},scales:{x:{grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)"}},y:{beginAtZero:!0,grid:{color:"rgba(51,65,85,0.4)"},ticks:{color:"rgb(148,163,184)",maxTicksLimit:8}}}}}function le(){let t=Rt("chart-risk-capital");if(!t)return;Tt("riskCapital");let e=ze(),n=a.status?.portfolio,o=e.map(p=>{let f=gt(p,a.status);return f!==null?f:0}),r=l(n?.total_equity_usdc),s=o.reduce((p,f)=>p+f,0),i=u(`Total ${S(r)}`,`\u5408\u8A08 ${S(r)}`);r!==null&&s>0&&Math.abs(s-r)>1?i+=u(" \xB7 bars sum may differ from headline"," \xB7 \u5404\u5E33\u52A0\u7E3D\u53EF\u80FD\u8207\u7E3D\u89BD\u7565\u6709\u5DEE\u7570"):a.status||(i=u("Awaiting live snapshot","\u7B49\u5F85\u5373\u6642\u5FEB\u7167")),O("risk-capital-meta",i),O("risk-capital-hint",u("Per-book equity in USDC equivalent from the live snapshot (or last saved snapshot).","\u5404\u5E33\u672C\u6B0A\u76CA\u4EE5 USDC \u7D04\u7576\u986F\u793A\uFF0C\u4F86\u81EA\u5373\u6642\u6216\u6700\u8FD1\u5FEB\u7167\u3002"));let c=e.map(p=>at[p]||"#94a3b8"),d=lr();Lt("chart-risk-capital",{empty:!1}),a.charts.riskCapital=new Chart(t,{type:"bar",data:{labels:e,datasets:[{label:u("Book equity (USDC eq.)","\u5E33\u672C\u6B0A\u76CA\uFF08USDC \u7D04\u7576\uFF09"),data:o,backgroundColor:c.map(p=>p+"cc"),borderColor:c,borderWidth:1}]},options:{...d,plugins:{...d.plugins,tooltip:{...d.plugins.tooltip,callbacks:{afterBody(p){if(!p?.length)return"";let f=p[0].dataIndex;if(f===void 0)return"";let m=o[f]??0,v=r>0?m/r:null;return[`${u("Share of total: ","\u4F54\u7E3D\u6B0A\u76CA\uFF1A")}${w(v,2)}`]}}}}}})}var se=864e5;function pt(t){let e=luxon.DateTime.fromISO(String(t||"").trim(),{zone:"utc"});return e.isValid?e.toMillis():NaN}function On(t){let e=t.filter(n=>Number.isFinite(n.x)&&n.y!==null&&Number.isFinite(n.y)).sort((n,o)=>n.x-o.x);if(e.length===0)return[];if(e.length===1){let n=e[0];return[{x:n.x-se,y:0},{x:n.x,y:n.y},{x:n.x+se,y:n.y}]}return e}function kt(t){return t.filter(e=>Number.isFinite(e.x)&&e.y!==null&&Number.isFinite(e.y)).sort((e,n)=>e.x-n.x)}function Yn(t){let e=kt(t);if(e.length===0)return[];if(e.length===1){let n=e[0];return[n,{x:n.x+se,y:n.y}]}return e}function Zn(t){let e=(t||[]).map(i=>i.x).filter(Number.isFinite);if(!e.length)return{};let n=Math.min(...e),o=Math.max(...e),r=o-n,s=se;return e.length===1||r<s*.25?{min:n-s,max:o+s}:{}}function ce(){let t=Rt("chart-cum-pnl");if(!t)return;Tt("cumPnl");let e=a.cumulativePnl,n=e?.realized_count?`${e.realized_count} closed groups`:u("no closed groups","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44");if(O("cum-pnl-meta",n),!e){Et("chart-cum-pnl","cumPnl");return}let o=[],r=ze();for(let s of r){let i=e.cumulative_by_book?.[s]||[];if(i.length){let c=On(i.map(d=>({x:pt(d.date),y:l(d.pnl_usdc)})));c.length&&o.push({label:`${s} cum. PnL`,data:c,borderColor:at[s],backgroundColor:at[s]+"22",stepped:!0,pointRadius:0,borderWidth:2})}}if(a.bookFilter==="ALL"&&e.cumulative_total?.length){let s=On(e.cumulative_total.map(i=>({x:pt(i.date),y:l(i.pnl_usdc)})));s.length&&o.push({label:"Total cum. PnL",data:s,borderColor:at.TOTAL,backgroundColor:at.TOTAL+"22",stepped:!0,pointRadius:0,borderWidth:2,borderDash:[4,4]})}if(!o.length){Et("chart-cum-pnl","cumPnl");return}Lt("chart-cum-pnl",{empty:!1}),a.charts.cumPnl=new Chart(t,{type:"line",data:{datasets:o},options:ie()})}function cr(t){return t.filter(e=>Math.abs(e.y)>1e-12)}var ur="rgba(52, 211, 153, 0.67)",dr="#34d399",pr="rgba(251, 113, 133, 0.67)",fr="#fb7185";function Mn(t){return t.map(e=>{let n=l(e.y)??0;return n>0?ur:n<0?pr:"rgba(148, 163, 184, 0.4)"})}function Fn(t){return t.map(e=>{let n=l(e.y)??0;return n>0?dr:n<0?fr:"#94a3b8"})}function ue(){let t=Rt("chart-daily-pnl");if(!t)return;Tt("dailyPnl");let e=30,n=a.cumulativePnl;if(!n){O("daily-pnl-meta",u("no closed groups","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44")),Et("chart-daily-pnl","dailyPnl",{chartType:"bar"});return}let o=ze(),r=(n.daily_total||[]).filter(m=>Number.isFinite(pt(m.date))),s=n?.daily_total?.length?`${n.daily_total.length} ${u("active days","\u500B\u6709\u6548\u4EA4\u6613\u65E5")}`:u("no closed groups","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D44");a.bookFilter==="ALL"&&r.length>=e&&(s+=" \xB7 30d SMA"),O("daily-pnl-meta",s);let i=m=>({x:pt(m.date),y:l(m.pnl_usdc)}),c=[];if(a.bookFilter==="ALL"){let m=kt((n.daily_total||[]).map(i));m.length&&c.push({type:"bar",label:u("Daily total","\u6BCF\u65E5\u5408\u8A08"),data:m,order:1,backgroundColor:Mn(m),borderColor:Fn(m),borderWidth:1})}else for(let m of o){let v=n.daily_by_book?.[m]||[],y=kt(v.map(i));y=cr(y),y.length&&c.push({type:"bar",label:`${m} ${u("daily","\u6BCF\u65E5")}`,data:y,order:1,backgroundColor:Mn(y),borderColor:Fn(y),borderWidth:1})}if(a.bookFilter==="ALL"&&r.length>=e){let m=[];for(let y=e-1;y<r.length;y++){let x=0;for(let b=y-e+1;b<=y;b++)x+=l(r[b].pnl_usdc)||0;m.push({x:pt(r[y].date),y:x/e})}let v=Yn(kt(m));v.length&&c.push({type:"line",label:`30d SMA (${e}-day realized avg.)`,data:v,order:2,borderColor:"#f472b6",backgroundColor:"#f472b633",tension:.15,pointRadius:0,borderWidth:2})}if(!c.length){Et("chart-daily-pnl","dailyPnl",{chartType:"bar"});return}Lt("chart-daily-pnl",{empty:!1});let d=c.flatMap(m=>m.data||[]),p=Zn(d),f=ie();a.charts.dailyPnl=new Chart(t,{type:"bar",data:{datasets:c},options:{...f,scales:{x:{...f.scales.x,...p,offset:!0,time:{unit:"day",tooltipFormat:"yyyy-LL-dd"}},y:{...f.scales.y,ticks:{...f.scales.y.ticks,maxTicksLimit:10}}}}})}function de(){let t=Rt("chart-apr");if(!t)return;Tt("apr");let e=a.aprSeries?.rows||[],n=Yn(kt(e.map(s=>({x:pt(s.date),y:l(s.apr)}))));if(!n.length){Et("chart-apr","apr",{yPercent:!0,messageKind:"apr"});return}Lt("chart-apr",{empty:!1});let o=Zn(n),r=ie();a.charts.apr=new Chart(t,{type:"line",data:{datasets:[{label:`Rolling APR (${a.aprWindow}d)`,data:n,borderColor:"#facc15",backgroundColor:"rgba(250,204,21,0.15)",tension:.25,pointRadius:0,borderWidth:2,fill:!0}]},options:{...r,scales:{x:{...r.scales.x,...o,time:{unit:"day",tooltipFormat:"yyyy-LL-dd"}},y:{...r.scales.y,ticks:{...r.scales.y.ticks,callback:s=>w(s,1)}}}}})}function vr(t){if(!_||!t)return;let e=String(t.investor_display_name||t.investor_id||"").trim(),n=document.querySelector(".app-header h1");n&&e&&(n.textContent=`${e} \xB7 ${L?"\u6295\u8CC7\u7D44\u5408\u7E3D\u89BD":"Investor summary"}`);let o=document.querySelector(".app-header h1 + p");if(!o)return;o.dataset.investorBaseCopy||(o.dataset.investorBaseCopy=o.textContent||"");let r=o.dataset.investorBaseCopy,s=String(t.investor_id||"").trim();o.textContent=s&&s!==e?`${u("Investor id","\u6295\u8CC7\u4EBA ID")}: ${s} \xB7 ${r}`:r}function yr(t){return _?t==="mainnet"?"border-sky-500/50 bg-sky-500/10 text-sky-200":t==="test"?"border-amber-500/50 bg-amber-500/10 text-amber-200":"border-slate-500/50 bg-slate-500/10 text-slate-200":t==="mainnet"?"border-rose-500/50 bg-rose-500/10 text-rose-200":"border-emerald-500/50 bg-emerald-500/10 text-emerald-200"}function Qn(t){if(!t)return;vr(t);let e=(t.env||"").toLowerCase(),n=document.getElementById("env-badge");n&&(n.textContent=_?e==="mainnet"?u("Network: Mainnet","\u7DB2\u8DEF\uFF1A\u4E3B\u7DB2"):e==="multi"?u("Network: Multi-account","\u7DB2\u8DEF\uFF1A\u591A\u5E33\u6236"):e==="test"?u("Network: Test","\u7DB2\u8DEF\uFF1A\u6E2C\u8A66"):`${u("Network:","\u7DB2\u8DEF\uFF1A")} ${e||"\u2014"}`:`env: ${e||"?"}`,n.className="text-xs px-2 py-0.5 rounded-full border "+yr(e));let o=document.getElementById("strategy-badge");if(o){let i=K(t.option_strategy||""),c=t.accounts?.length||0;o.textContent=t.multi_account?u(`strategy: multi (${c} accounts)`,`\u7B56\u7565\uFF1A\u591A\u5E33\u6236\uFF08${c}\uFF09`):_?`${u("Strategy:","\u7B56\u7565\uFF1A")} ${i?Gt(i):"\u2014"}`:`strategy: ${i?Gt(i):"?"}`,o.className="text-xs px-2 py-0.5 rounded-full border border-sky-500/50 bg-sky-500/10 text-sky-200"}let r=document.getElementById("creds-badge");r&&(r.textContent=t.has_private_creds?"creds: ok":"creds: missing",r.className="text-xs px-2 py-0.5 rounded-full border "+(t.has_private_creds?"border-emerald-500/50 bg-emerald-500/10 text-emerald-200":"border-rose-500/50 bg-rose-500/10 text-rose-200"));let s=document.getElementById("scheduler-badge");if(s)if(t.scheduler_running){let i=t.snapshot_interval_sec||300,c=Math.round(i/60);s.textContent=u(`scheduler: on (every ${c} min)`,`\u5FEB\u7167\u6392\u7A0B\uFF1A\u6BCF ${c} \u5206\u9418`),s.className="text-xs px-2 py-0.5 rounded-full border border-emerald-500/50 bg-emerald-500/10 text-emerald-200"}else s.textContent=u("scheduler: off","\u5FEB\u7167\u6392\u7A0B\uFF1A\u95DC\u9589"),s.className="text-xs px-2 py-0.5 rounded-full border border-slate-600 bg-slate-700/30 text-slate-300";be()}function to(t){let e=document.getElementById("regime-badge");if(!e)return;let n=t?.portfolio?.regime||"?",o=String(n).toLowerCase(),r={normal:"\u6B63\u5E38",elevated:"\u504F\u9AD8",crisis:"\u8B66\u6212"},s={normal:"Normal",elevated:"Elevated",crisis:"Crisis"};e.textContent=_?`${u("Risk posture:","\u98A8\u63A7\u72C0\u614B\uFF1A")} ${L?r[o]||n:s[o]||n}`:`regime: ${n}`;let i=n==="normal"?"border-emerald-500/50 bg-emerald-500/10 text-emerald-200":n==="elevated"?"border-amber-500/50 bg-amber-500/10 text-amber-200":n==="crisis"?"border-rose-500/50 bg-rose-500/10 text-rose-200":"border-slate-600 bg-slate-700/30 text-slate-300";e.className=`text-xs px-2 py-0.5 rounded-full border ${i}`}function hr(t,e){let n=e?.portfolio||{},r=(e?.accounts||{})[t]||{},s=ke(n?.equity_by_book,t),i=l(r.equity),c=gt(t,e),d=s?l(n?.day_start_equity_by_book?.[t]):null,p=l(n?.day_drawdown_pct_by_book?.[t]),f=gn(t,e,c,d),m=n?.margin_ratios_by_currency?.[t]||{},v=l(m.im_ratio),y=l(m.mm_ratio),x=l(n?.delta_totals_by_currency?.[t]),b=n?.regime_by_currency?.[t],C=n?.cooling_down_by_book?.[t],E=n?.hard_derisk_by_book?.[t],T=n?.halt_entries_by_book?.[t],U=n?.halt_entry_reasons_by_book?.[t]||[],N=t==="BTC"?"book-card-btc":t==="ETH"?"book-card-eth":"book-card-usdc",h=[];if(s||h.push('<span class="chip chip-muted">not traded</span>'),b&&s){let j=b==="normal"?"chip-ok":b==="elevated"?"chip-warn":"chip-bad";h.push(`<span class="chip ${j}">${b}</span>`)}C&&h.push('<span class="chip chip-warn">cooling</span>'),E&&h.push('<span class="chip chip-bad">hard derisk</span>'),T&&h.push('<span class="chip chip-warn">halt entries</span>'),h.length===0&&h.push('<span class="chip chip-ok">healthy</span>');let R=v!==null?Math.min(1,Math.max(0,v)):0,I=v===null?"bar-ok":v>=.45?"bar-bad":v>=.35?"bar-warn":"bar-ok",M=y!==null?Math.min(1,Math.max(0,y)):0,W=y===null?"bar-ok":y>=.33?"bar-bad":y>=.22?"bar-warn":"bar-ok";return`
    <div class="rounded-2xl border ${N} bg-slate-900/60 p-4 shadow">
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-sm font-semibold tracking-wide text-slate-200">${t} BOOK</h3>
        <div class="flex flex-wrap gap-1">${h.join("")}</div>
      </div>
      <div class="text-2xl font-mono">${S(c)}</div>
      <div class="text-xs text-slate-500 mb-3">
        ${i!==null?$(i,8)+" "+t:""}
        ${d!==null?"\xB7 day-start "+S(d):""}
      </div>
      <div class="kv"><span class="k">Day P&amp;L</span><span class="v ${k(f)}">${S(f)}</span></div>
      <div class="kv"><span class="k">Day drawdown</span><span class="v ${k(p===null?null:-p)}">${w(p)}</span></div>
      <div class="kv"><span class="k">Delta total</span><span class="v">${$(x,4)}</span></div>
      <div class="mt-3 space-y-2">
        <div>
          <div class="flex justify-between text-xs text-slate-400">
            <span>IM ratio</span><span class="font-mono">${w(v,2)}</span>
          </div>
          <div class="mini-bar"><span class="${I}" style="width:${(R*100).toFixed(1)}%"></span></div>
        </div>
        <div>
          <div class="flex justify-between text-xs text-slate-400">
            <span>MM ratio</span><span class="font-mono">${w(y,2)}</span>
          </div>
          <div class="mini-bar"><span class="${W}" style="width:${(M*100).toFixed(1)}%"></span></div>
        </div>
      </div>
      ${U.length?`<p class="mt-3 text-xs text-rose-300">${U.map(g).join("<br>")}</p>`:""}
    </div>
  `}function eo(t){let e=document.getElementById("book-cards");if(!e)return;if(!t){e.innerHTML=`
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm md:col-span-3">
        Need DERIBIT_CLIENT_ID/SECRET in <code>.env</code> to load live status.
        Read-only views (closed trades, cumulative PnL) still work below.
      </div>`;return}let n=Object.keys(t?.portfolio?.equity_by_book||{}).map(s=>String(s).toUpperCase()).filter(s=>z.includes(s)),r=(n.length?n:z).map(s=>hr(s,t)).join("");e.innerHTML=r}function no(t,e){let n=document.getElementById("account-cards");if(!n)return;let o=t?.accounts||e?.dashboard_accounts||[],r=new Map((e?.account_statuses||[]).map(i=>[String(i.name||""),i])),s=o.length?o:e?.account_statuses||[];if(!s.length){n.innerHTML=`
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-slate-400 text-sm md:col-span-3">
        No dashboard account metadata yet.
      </div>`;return}n.innerHTML=s.map(i=>{let c=String(i.name||""),d=r.get(c)||i,p=d.portfolio||{},f=l(p.total_equity_usdc),m=l(p.day_start_equity_usdc),v=Ee(p,f,m),y=p.regime||"\u2014",x=l(d.trade_group_count),b=i.has_private_creds,C=d.option_strategy||i.option_strategy||"",E=d.env||i.env||"",T=i.state_file||d.state_file||"",U=[C?X(C):"",b===void 0?"":`<span class="chip ${b?"chip-ok":"chip-bad"}">creds ${b?"ok":"missing"}</span>`].filter(Boolean);return`
        <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 shadow">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h3 class="text-sm font-semibold tracking-wide text-slate-100">${g(c||"account")}</h3>
              <p class="text-xs text-slate-500 mt-1 break-all">${g(E)} \xB7 ${g(T)}</p>
            </div>
            <div class="flex flex-wrap justify-end gap-1 flex-shrink-0">${U.join("")}</div>
          </div>
          <div class="stat-grid mt-4">
            <div class="stat-tile">
              <div class="label">Equity</div>
              <div class="value">${S(f)}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Day P&amp;L</div>
              <div class="value ${k(v)}">${S(v)}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Open groups</div>
              <div class="value">${x??"\u2014"}</div>
            </div>
            <div class="stat-tile">
              <div class="label">Regime</div>
              <div class="value">${g(y)}</div>
            </div>
          </div>
        </div>
      `}).join("")}function oo(t,e){let n=document.getElementById("aggregate-card");if(!n)return;let{portfolio:o,source:r}=yt(),s=e?.summary;if(!o&&!s){_&&!a.investorReady?n.innerHTML=pn():n.innerHTML=`<p class="text-sm text-slate-400">${u("No status / report data yet.","\u5C1A\u7121\u5373\u6642\u5E33\u6236\u6216\u7E3E\u6548\u6458\u8981\u8CC7\u6599\u3002")}</p>`;return}let i=l(o?.total_equity_usdc),c=l(o?.day_start_equity_usdc),d=Ee(o,i,c),p=l(o?.day_drawdown_pct),f=bt(t,a.groups),m=f.reduce((yo,ho)=>yo+(rt(ho,t,a.groups)||0),0),v=qn(f,t,a.groups),y=l(s?.realized_pnl_usdc),x=l(s?.lifetime_realized_apr),b=l(s?.realized_win_rate),C=l(s?.avg_holding_days),E=l(s?.realized_closed_group_count),T=l(s?.window_days_used),U=l(s?.window_realized_pnl_usdc),N=l(s?.window_realized_apr),h=kn(e,a.groups),R=Gn(e,a.groups,t),I=T??30,M=Wn(e,a.groups,t,I),W=jn(t),j=xn(t),ye=h!==null?`${u("since","\u81EA")} ${bn(h)}`:u("no realized history yet","\u5C1A\u7121\u5DF2\u5BE6\u73FE\u7D00\u9304"),Nt=r==="snapshot"&&_?`<p class="text-xs text-amber-200/80 mt-3">${u("Equity from last snapshot; live sync continues in background.","\u6B0A\u76CA\u4F86\u81EA\u6700\u8FD1\u5FEB\u7167\uFF1B\u5373\u6642\u540C\u6B65\u65BC\u80CC\u666F\u9032\u884C\u4E2D\u3002")}</p>`:r==="live"&&_?`<p class="text-xs text-emerald-200/70 mt-3">${u("Live Deribit sync","\u5DF2\u540C\u6B65 Deribit \u5373\u6642\u8CC7\u6599")}</p>`:"",he={totalEquity:i,dayStart:c,dayPnl:d,dayDrawdown:p,openCredit:m,creditByStrategy:v,summary:s,winRate:b,avgHolding:C,sinceLine:ye,lifetimePnl:y,lifetimeNativeByBook:R,closedCount:E,windowLabelDays:I,windowPnl:U,windowNativeByBook:M,lifetimeApr:x,windowApr:N,equityNativeByBook:W,equityUsdByBook:j},Ke=fn(he);_?n.innerHTML=`
      <div class="investor-view-desktop">${Ke}</div>
      <div class="investor-view-mobile">${mn(he)}</div>
      ${Nt}`:n.innerHTML=`${Ke}${Nt}`,be()}function He(t){return{id:t,openCount:0,closedCount:0,wins:0,openEntryCredit:0,unrealizedUsd:0,realizedPnl:0,annualizedSum:0,annualizedCount:0,annualizedWeightedSum:0,annualizedWeight:0,aprPnlUsdSum:0,aprCapitalDays:0,holdingSum:0,holdingCount:0,books:new Set}}function Jn(t,e,n){let o=n||"";return e.add(o),t.has(o)||t.set(o,He(o)),t.get(o)}function _r(t,e,n){if(n===null||n<=0)return null;let o=Kt(t,e);if(o===null||o<=0)return null;let r=D(t);if(r==="USDC")return o*n;let s=l(e?.underlying_index_usd?.[r])??l(a.lastSpotUsd?.[r]);return s===null||s<=0?null:o*s*n}function xr(t){return t.aprCapitalDays>0?t.aprPnlUsdSum/t.aprCapitalDays*365:null}function gr(t,e,n){let o=new Set(H.map(d=>d.id)),r=new Map;for(let d of o)r.set(d,He(d));let s=bt(t,n);for(let d of s){let p=A(d);if(!G[p])continue;let f=Jn(r,o,p);f.openCount+=1;let m=rt(d,t,n);m!==null&&(f.openEntryCredit+=m);let v=xt(d,t,n);v!==null&&(f.unrealizedUsd+=v);let y=q(d);y&&f.books.add(y)}let i=Be(e,n);for(let d of i){let p=A(d);if(!G[p])continue;let f=Jn(r,o,p);f.closedCount+=1;let m=Jt(d,t);m!==null&&(f.realizedPnl+=m,m>0&&(f.wins+=1));let v=$t(d);v!==null&&(f.holdingSum+=v,f.holdingCount+=1);let y=Kt(d,t);if(m!==null&&y!==null&&y>0&&v!==null&&v>0){let C=D(d),E=y;if(C==="BTC"||C==="ETH"){let T=Ct(d,t);T===null||T<=0?E=null:E=y*T}E!==null&&(f.aprPnlUsdSum+=m,f.aprCapitalDays+=E*v)}let x=Ne(d,t);if(x!==null){f.annualizedSum+=x,f.annualizedCount+=1;let C=_r(d,t,v);C!==null&&(f.annualizedWeightedSum+=x*C,f.annualizedWeight+=C)}let b=String(d.collateral_currency||d.currency||"").toUpperCase();b&&f.books.add(b)}return St(o).map(d=>r.get(d)||He(d))}function br(t){let e=st(t.id),n=t.closedCount>0?t.wins/t.closedCount:null,o=xr(t),r=t.holdingCount>0?t.holdingSum/t.holdingCount:null,s=Array.from(t.books).sort().join(" / ")||"\u2014";return`
    <div class="rounded-2xl border ${e.accentClass} bg-slate-900/60 p-4 shadow">
      <div class="flex items-start justify-between gap-3 mb-2">
        <div>
          <h3 class="text-sm font-semibold tracking-wide text-slate-100">${g(e.title)}</h3>
          <p class="text-xs text-slate-500 mt-1">${g(e.description)}</p>
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
          <div class="value">${w(o,1)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Unrealized P&amp;L","\u672A\u5BE6\u73FE\u640D\u76CA")}</div>
          <div class="value ${k(t.unrealizedUsd)}">${S(t.unrealizedUsd)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Realized P&amp;L","\u5DF2\u5BE6\u73FE\u640D\u76CA")}</div>
          <div class="value ${k(t.realizedPnl)}">${S(t.realizedPnl)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Win rate","\u52DD\u7387")}</div>
          <div class="value">${w(n,1)}</div>
        </div>
        <div class="stat-tile">
          <div class="label">${u("Avg holding","\u5E73\u5747\u6301\u6709")}</div>
          <div class="value">${r===null?"\u2014":$(r,2)+(L?" \u5929":"d")}</div>
        </div>
      </div>
      <div class="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <span>${t.closedCount} ${u("closed \xB7 books","\u7B46\u5DF2\u5E73 \xB7 \u5E33\u672C")} ${g(s)}</span>
        <span>${u("weighted annualized","\u52A0\u6B0A\u5E74\u5316")} ${w(o,1)}</span>
      </div>
    </div>
  `}function ro(t){let e=K(t);return e==="covered_call"?"open-position-call":e==="bull_put_spread"?"open-position-spread":"open-position-put"}function so(t){let e=l(t);return e===null||Math.abs(e)<.005?"open-position-flat":e>0?"open-position-profit":"open-position-loss"}function io(t){let e=l(t);return e===null||Math.abs(e)<.005?u("Flat","\u6301\u5E73"):e>0?u("In profit","\u6D6E\u76C8"):u("Underwater","\u6D6E\u8667")}function ao(t){let e=l(t),n=e===null?0:Math.max(0,Math.min(100,e*100));return`<span class="credit-capture-bar"><span class="${e===null?"bar-muted":e>=.5?"bar-ok":e>=.15?"bar-warn":"bar-bad"}" style="width:${n}%"></span></span>`}function V(t,e,n="",{secondary:o=!1}={}){return`
    <div class="open-position-metric${o?" open-position-kpi-secondary":""} ${n}">
      <span class="open-position-label">${t}</span>
      <span class="open-position-value">${e}</span>
    </div>`}function Kn(t,e,n,o){let r=o==="short",s=tt(t),i=r?L?`\u8CE3\u51FA${s==="Call"?"\u8CB7\u6B0A":"\u8CE3\u6B0A"}`:`Short ${s}`:u("Long protection","\u4FDD\u8B77\u8CB7\u817F"),c=Y(t,o),d=et(t,e,o),p=J(t,o),f=nt(t,e,o,"average_price"),m=nt(t,e,o,"mark_price"),v=lt(e,t,n,o),y=q(t)||t.collateral_currency||"";return`
    <div class="open-position-leg ${r?"leg-short":"leg-long"}">
      <div class="open-position-leg-head">
        <span class="chip ${r?"chip-warn":"chip-ok"}">${i}</span>
        <span class="open-position-leg-amount">${d===null?"\u2014":$(d,4)}</span>
      </div>
      <div class="open-position-leg-instrument">${g(c||"\u2014")}</div>
      <div class="open-position-leg-metrics">
        ${V(u("Strike","\u5C65\u7D04\u50F9"),dt(p))}
        ${V(u("Entry","\u9032\u5834\u50F9"),ut(f,y))}
        ${V(u("Mark","\u6A19\u8A18\u50F9"),ut(m,y))}
        ${V(u("Leg PNL","\u55AE\u817F\u640D\u76CA"),v===null?"\u2014":S(v),k(v))}
      </div>
    </div>`}function Sr(t,e,n){let o=A(t),r=q(t)||t.collateral_currency||"";if(o==="bull_put_spread"){let s=Le(t),i=Zt(t,e,"average_price"),c=Zt(t,e,"mark_price");return`
      <span>${u("Width","\u50F9\u5DEE\u5BEC\u5EA6")} ${dt(s)}</span>
      <span>${u("Entry gap","\u9032\u5834\u50F9\u5DEE")} ${ut(i,r)}</span>
      <span>${u("Mark gap","\u5E02\u50F9\u50F9\u5DEE")} ${ut(c,r)}</span>`}return`
    <span>${u("Strike","\u5C65\u7D04\u50F9")} ${dt(J(t,"short"))}</span>
    <span>${g(De(t))}</span>`}function $r(t,e,n){let o=A(t),r=o==="bull_put_spread",s=Ce(t),i=xt(t,e,n),c=we(t,e,n),d=q(t)||t.collateral_currency||"",p=l(t.profit_capture),f=rt(t,e,n),m=oe(t,e),v=Y(t,"long"),y=et(t,e,"short"),x=et(t,e,"long"),b=M=>M===null?"":` \xB7 ${$(M,4)}`,C=ro(o),E=so(i),T=io(i),U=f===null?"\u2014":S(f),N=m===null?"":`<span class="inv-pos-metric-sub font-mono">${Ht(m,d)}</span>`,h=te(t,e),R=h===null?"\u2014":w(h,1),I="";if(r){let M=Le(t),W=Zt(t,e,"average_price");I=`
      <span class="inv-pos-tag">${u("Width","\u50F9\u5DEE")} ${dt(M)}</span>
      <span class="inv-pos-tag">${u("Entry gap","\u9032\u5834")} ${ut(W,d)}</span>`}else I=`
      <span class="inv-pos-tag">${u("Strike","\u5C65\u7D04")} ${dt(J(t,"short"))}</span>
      <span class="inv-pos-tag">${g(De(t))}</span>`;return`
    <article class="inv-position ${C} ${E}">
      <header class="inv-position-head">
        <div class="inv-position-main">
          <div class="inv-position-titleline">
            ${X(o,{compact:!0})}
            <h3 class="inv-position-name">${g(Vt(t))}</h3>
          </div>
          <p class="inv-position-contract font-mono">${g(t.short_instrument_name||"\u2014")}<span class="inv-position-size tabular-nums">${b(y)}</span></p>
          ${r&&v?`<p class="inv-position-contract font-mono inv-position-contract--long">${u("Long","\u8CB7\u817F")} ${g(v)}<span class="inv-position-size tabular-nums">${b(x)}</span></p>`:""}
          <div class="inv-position-tags">
            <span class="inv-pos-tag">${g(d)}</span>
            <span class="inv-pos-tag inv-pos-tag--status">${g(T)}</span>
            ${I}
          </div>
        </div>
        <div class="inv-position-pnl">
          <span class="inv-position-pnl-label">${u("Unrealized","\u672A\u5BE6\u73FE")}</span>
          <span class="inv-position-pnl-value font-mono tabular-nums ${k(i)}">${i===null?"\u2014":S(i)}</span>
          <span class="inv-position-pnl-native font-mono tabular-nums ${k(c)}">${Ht(c,d)}</span>
        </div>
      </header>
      <div class="inv-position-strip" role="list">
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("DTE","\u5230\u671F")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${s!==null?`${$(s,1)}${L?"\u5929":"d"}`:"\u2014"}</span>
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("Credit kept","\u6B0A\u5229\u91D1")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${w(p,1)}</span>
          ${ao(p)}
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("Entry","\u9032\u5834")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums">${U}</span>
          ${N}
        </div>
        <div class="inv-pos-metric" role="listitem">
          <span class="inv-pos-metric-k">${u("Entry APR","\u9032\u5834\u5E74\u5316")}</span>
          <span class="inv-pos-metric-v font-mono tabular-nums ${h!==null&&h>=.15?"pnl-pos":""}">${R}</span>
        </div>
      </div>
    </article>`}function Cr(t,e,n){let o=A(t),r=o==="bull_put_spread",s=Ce(t),i=xt(t,e,n),c=we(t,e,n),d=q(t)||t.collateral_currency||"",p=l(t.profit_capture),f=rt(t,e,n),m=oe(t,e),v=ee(t),y=Ie(t,e),x=ne(t),b=Oe(t,e),C=Y(t,"long"),E=!_&&vt(t)?vt(t):"",T=ro(o),U=so(i),N=_?u(`${d} book`,`${d} \u5E33\u672C`):`${d} book`;return`
    <article class="open-position-card ${T} ${U}">
      <div class="open-position-glow"></div>
      <div class="open-position-header">
        <div class="open-position-main">
          <div class="open-position-title-row">
            ${X(o)}
            <h3>${g(Vt(t))}</h3>
            <span class="open-book-pill">${g(N)}</span>
            <span class="open-status-pill">${io(i)}</span>
          </div>
          <div class="open-position-instruments">
            <span>${g(t.short_instrument_name||"\u2014")}</span>
            ${r&&C?`<span>${u("Long","\u8CB7\u5165\u4FDD\u8B77")} ${g(C)}</span>`:""}
          </div>
          <div class="open-position-detail-row">
            ${Sr(t,e,n)}
            ${E?`<span>${g(E)}</span>`:""}
          </div>
        </div>
        <div class="open-position-pnl-panel">
          <span class="open-position-label"${r?` title="${u("Sum of leg mark MTM when both legs load; otherwise engine entry\u2212debit (bid/ask close est.).","\u5169\u817F\u7686\u8F09\u5165\u6642\u70BA\u6A19\u8A18\u640D\u76CA\u52A0\u7E3D\uFF1B\u5426\u5247\u70BA\u5F15\u64CE\u9032\u5834\u6536\u6582\u8207\u73FE\u4F30\u5E73\u5009\u5DEE\u984D\u3002")}"`:""}>${u("Unrealized PNL","\u672A\u5BE6\u73FE\u640D\u76CA")}</span>
          <strong class="${k(i)}">${i===null?"\u2014":S(i)}</strong>
          <span class="open-position-native ${k(c)}">${Ht(c,d)}</span>
        </div>
      </div>
      <div class="open-position-kpis open-position-kpis-extended">
        ${V(u("DTE","\u8DDD\u5230\u671F\u5929\u6578"),s!==null?`${$(s,2)}${L?" \u5929":"d"}`:"\u2014")}
        ${V(u("Credit kept","\u5DF2\u6536\u6B0A\u5229\u91D1\u6BD4\u4F8B"),`${w(p,1)}${ao(p)}`)}
        ${V(u("Entry credit","\u9032\u5834\u6536\u6582"),f===null?"\u2014":Xt(f,m,d))}
        ${(()=>{let h=te(t,e),R=h!==null&&h>=.15?"pnl-pos":"";return V(u("Entry net APR","\u9032\u5834\u6DE8\u5E74\u5316"),h===null?"\u2014":w(h,1),R)})()}
        ${V(u("Entry fee","\u9032\u5834\u624B\u7E8C\u8CBB"),v===null?"\u2014":Xt(v,y,d))}
        ${V(u("Est. close fee","\u9810\u4F30\u5E73\u5009\u8CBB"),x===null?"\u2014":Xt(x,b,d))}
      </div>
      <div class="open-position-legs ${r?"has-two-legs":"has-one-leg"}">
        ${Kn(t,e,n,"short")}
        ${r?Kn(t,e,n,"long"):""}
      </div>
    </article>`}function wr(t,e,n){let o=Cr(t,e,n);return _?`<div class="investor-view-desktop">${o}</div><div class="investor-view-mobile">${$r(t,e,n)}</div>`:o}function kr(t,e,n,o){let r=K(t)||t,s=st(r),i=e.map(c=>wr(c,n,o)).join("");return`
    <div class="rounded-2xl border ${s.accentClass} bg-slate-900/60 shadow overflow-hidden">
      <div class="flex flex-wrap items-baseline justify-between gap-3 px-4 py-3 border-b border-slate-800 bg-slate-950/40">
        <div class="flex flex-wrap items-center gap-2 min-w-0">
          <h3 class="text-sm font-semibold text-slate-200">${g(s.title)}</h3>
          ${X(r)}
        </div>
        <span class="text-xs text-slate-500">${e.length} ${u("open","\u7B46\u6301\u5009")}</span>
      </div>
      <div class="p-4">
        <div class="open-position-list">
          ${i}
        </div>
      </div>
    </div>`}function pe(t,e,n){let o=document.getElementById("strategy-cards"),r=document.getElementById("strategy-open-groups");if(!o&&!r)return;let s=gr(t,e,n),i=bt(t,n),c=i.length,d=Be(e,n).length,p=s.filter(v=>v.openCount||v.closedCount).length;if(O("strategy-meta",_?u(`${c} open \xB7 ${d} closed \xB7 ${p||0} active strategy groups`,`${c} \u7B46\u6301\u5009 \xB7 ${d} \u7B46\u5DF2\u5E73 \xB7 ${p||0} \u985E\u7B56\u7565`):`${c} open \xB7 ${d} closed \xB7 ${p||0} active strategy groups`),o&&(o.innerHTML=s.map(br).join("")),!r)return;if(!i.length){r.innerHTML=`
      <div class="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 text-sm text-slate-400">
        ${u("No open strategy positions.","\u76EE\u524D\u6C92\u6709\u958B\u5009\u4E2D\u7684\u7B56\u7565\u90E8\u4F4D\u3002")}
      </div>`;return}let f=new Map,m=new Set(H.map(v=>v.id));for(let v of i){let y=A(v);G[y]&&(f.has(y)||f.set(y,[]),f.get(y).push(v))}r.innerHTML=St(m).filter(v=>f.has(v)).map(v=>kr(v,f.get(v),t,n)).join("")}function Xn(t,e,n,o,r){if(!t)return;if(!e.length){t.innerHTML=`<li class="activity-empty">${g(r)}</li>`;return}let s=[];for(let i of e)try{s.push(An(i,n,o))}catch(c){console.warn("activity card skipped",i?.group_id,c)}t.innerHTML=s.length?s.join(""):`<li class="activity-empty">${g(r)}</li>`}function Dt(t,e,n){let o=document.getElementById("activity-open-list"),r=document.getElementById("activity-closed-list");if(!o&&!r)return;let s=Dn(t,n),i=Un(t,e,n),c=Me(s,a.activityOpenPage,ft),d=Me(i,a.activityClosedPage,ft);a.activityOpenPage=c.page,a.activityClosedPage=d.page,O("activity-meta",u(`${s.length} open \xB7 ${i.length} closed`,`${s.length} \u6301\u5009\u4E2D \xB7 ${i.length} \u5DF2\u5E73\u5009`)),Xn(o,c.rows,t,n,u("No open positions","\u5C1A\u7121\u6301\u5009")),Xn(r,d.rows,t,n,u("No closed trades","\u5C1A\u7121\u5DF2\u5E73\u5009\u7D00\u9304"));let p=document.getElementById("activity-open-pagination"),f=document.getElementById("activity-closed-pagination");p&&(p.innerHTML=Fe("open",c),p.hidden=!p.innerHTML),f&&(f.innerHTML=Fe("closed",d),f.hidden=!f.innerHTML)}function Er(t){let e=Array.isArray(t?.strategy_stresses)?t.strategy_stresses.filter(Boolean):[];return e.length?e:[t]}function Tr(t,e){let n=t.equity_usdc_by_book||{},o=t.strategy_analysis||{},r=K(t.option_strategy||o.label||"naked_short"),s=Object.values(n).reduce((m,v)=>m+(l(v)||0),0),i=(t.accounts||[]).map(m=>m?.name).filter(Boolean).join(", "),c=Array.isArray(o.actions)?o.actions:[],d=z.map(m=>`
        <div class="rounded-xl bg-slate-800/40 px-3 py-2">
          <div class="text-[11px] text-slate-400 uppercase tracking-wide">${m} book</div>
          <div class="font-mono text-sm">${S(n[m])}</div>
        </div>`).join(""),p=(t.scenarios||[]).map(m=>{let v=l(m.loss_usdc_total),y=l(m.loss_usdc_pct_of_total_equity),x=m.loss_by_book_usdc||{};return`
        <tr>
          <td class="px-3 py-2 font-mono">${w(l(m.shock),0)}</td>
          <td class="px-3 py-2 font-mono">${w(l(m.slippage),0)}</td>
          <td class="px-3 py-2 text-right font-mono ${k(v)}">${S(v)}</td>
          <td class="px-3 py-2 text-right font-mono">${w(y,2)}</td>
          <td class="px-3 py-2 text-right font-mono ${k(l(x.BTC))}">${S(x.BTC)}</td>
          <td class="px-3 py-2 text-right font-mono ${k(l(x.ETH))}">${S(x.ETH)}</td>
          <td class="px-3 py-2 text-right font-mono ${k(l(x.USDC))}">${S(x.USDC)}</td>
        </tr>`}).join(""),f=c.length?`<ul class="mt-2 list-disc list-inside text-xs text-slate-500 space-y-1">
        ${c.map(m=>`<li>${g(m)}</li>`).join("")}
      </ul>`:"";return`
    <div class="${e>1?"rounded-2xl border border-slate-800 bg-slate-900/40 p-4":""}">
      <div class="rounded-xl bg-slate-800/40 px-3 py-3 mb-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div class="text-[11px] text-slate-400 uppercase tracking-wide">Strategy black swan read</div>
            <div class="mt-1 flex items-center gap-2 text-sm text-slate-200">
              <span>${g(Gt(r))}</span>
              ${X(r)}
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
          <div class="font-mono text-sm">${S(s)}</div>
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
  `}function Ut(t){if(_)return;let e=document.getElementById("stress-card");if(!e)return;let n=!!document.getElementById("stress-section")?.open;if(!t&&!a.stressDataLoaded&&!n)return;if(!t){a.stressLoadInFlight||a.health?.has_private_creds?e.innerHTML='<p class="text-slate-500 text-sm">Loading\u2026</p>':e.innerHTML='<p class="text-sm text-slate-400">Set DERIBIT_CLIENT_ID and DERIBIT_CLIENT_SECRET to load live stress data.</p>',O("stress-meta","\u2014");return}let o=Er(t),r=o.reduce((i,c)=>i+(c.scenarios?.length||0),0),s=o.reduce((i,c)=>i+(c.positions?.length||0),0);O("stress-meta",`${o.length} strategy view${o.length===1?"":"s"} \xB7 ${r} scenarios \xB7 ${s} legs`),e.innerHTML=`
    <div class="space-y-4">
      ${o.map(i=>Tr(i,o.length)).join("")}
    </div>
    <p class="text-xs text-slate-500 mt-3">
      Per-book loss is capped at that book's equity (liquidation-style floor). Spot shock is a negative index move.
      For bull put spread, long option legs are netted when present; for covered call, BTC/ETH spot cover drawdown is included.
    </p>
  `}var Br=["/vendor/chart.umd.min.js","/vendor/chartjs-adapter-luxon.umd.min.js"],At=null;function Lr(t){return new Promise((e,n)=>{let o=document.querySelector(`script[src="${t}"]`);if(o){if(o.dataset.loaded==="true"){e();return}o.addEventListener("load",()=>e(),{once:!0}),o.addEventListener("error",()=>n(new Error(`failed to load ${t}`)),{once:!0});return}let r=document.createElement("script");r.src=t,r.async=!1,r.onload=()=>{r.dataset.loaded="true",e()},r.onerror=()=>n(new Error(`failed to load ${t}`)),document.head.appendChild(r)})}function Pt(){return globalThis.Chart?Promise.resolve():At||(At=(async()=>{for(let t of Br)await Lr(t);if(!globalThis.Chart)throw new Error("Chart.js failed to initialize")})().catch(t=>{throw At=null,t}),At)}function lo(t=new Date){let e=String(t.getUTCHours()).padStart(2,"0"),n=String(t.getUTCMinutes()).padStart(2,"0"),o=String(t.getUTCSeconds()).padStart(2,"0");return`${e}:${n}:${o}`}var Ve=null;function Ge(){Ve?.()}function We(){let t=document.getElementById("header-spot-btc"),e=document.getElementById("header-spot-eth"),n=a.lastSpotUsd.BTC,o=a.lastSpotUsd.ETH;t&&(t.textContent=n!==null&&n>0?`BTC ${F.usd2.format(n)}`:"BTC \u2014"),e&&(e.textContent=o!==null&&o>0?`ETH ${F.usd2.format(o)}`:"ETH \u2014")}async function fe(){if(!me())return;try{await Pt()}catch(e){console.error("chart vendor load failed",e),B(`charts: ${e.message}`);return}let t=[["risk-capital",le],["cum-pnl",ce],["daily-pnl",ue],["apr",de]];for(let[e,n]of t)try{n()}catch(o){console.error(`${e} chart render failed`,o)}Bt()}var Dr={spot:{en:"Fetching BTC / ETH market prices\u2026",zh:"\u6B63\u5728\u53D6\u5F97 BTC / ETH \u5373\u6642\u5831\u50F9\u2026"},snapshot:{en:"Loading last equity snapshot\u2026",zh:"\u6B63\u5728\u8B80\u53D6\u6700\u8FD1\u6B0A\u76CA\u5FEB\u7167\u2026"},health:{en:"Checking account connection\u2026",zh:"\u6B63\u5728\u78BA\u8A8D\u5E33\u6236\u9023\u7DDA\u2026"},groups:{en:"Loading open positions and spreads\u2026",zh:"\u6B63\u5728\u8B80\u53D6\u6301\u5009\u8207\u50F9\u5DEE\u90E8\u4F4D\u2026"},cumulative:{en:"Loading realized P&L history\u2026",zh:"\u6B63\u5728\u8F09\u5165\u5DF2\u5BE6\u73FE\u640D\u76CA\u6B77\u53F2\u2026"},apr:{en:"Calculating rolling performance (APR)\u2026",zh:"\u6B63\u5728\u8A08\u7B97\u6EFE\u52D5\u5E74\u5316\u5831\u916C\u2026"},status:{en:"Syncing live equity and margin\u2026",zh:"\u6B63\u5728\u540C\u6B65\u5373\u6642\u6B0A\u76CA\u8207\u4FDD\u8B49\u91D1\u2026"},summary:{en:"Loading performance summary from local records\u2026",zh:"\u6B63\u5728\u5F9E\u672C\u5730\u7D00\u9304\u8F09\u5165\u7E3E\u6548\u6458\u8981\u2026"},render:{en:"Preparing your dashboard\u2026",zh:"\u6B63\u5728\u6574\u7406\u5100\u8868\u677F\u986F\u793A\u2026"},done:{en:"Done",zh:"\u5B8C\u6210"}};function Ur(t){let e=Dr[t];return e?u(e.en,e.zh):""}function uo(t,{includeCharts:e=!0}={}){let n=3+(t?2:0)+1;return e&&(n+=2),n}function je(t,e){let n=Math.min(100,Math.max(0,Math.round(t*100))),o=document.getElementById("investor-load-bar-fill");o&&(o.style.width=`${n}%`);let r=document.querySelector("[data-investor-load-pct]");r&&(r.textContent=`${n}%`);let s=document.querySelector("[data-investor-load-step]");s&&e&&(s.textContent=Ur(e))}function po(){if(!_)return;let t=(e,n,o)=>{let r=document.querySelector(`[data-investor-load-${e}]`);r&&(r.textContent=u(n,o))};t("eyebrow","Please wait","\u8ACB\u7A0D\u5019"),t("title","Loading your portfolio","\u6B63\u5728\u8F09\u5165\u60A8\u7684\u6295\u8CC7\u7D44\u5408"),t("hint","Showing snapshot first; live positions and P&L sync in the background.","\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\uFF1B\u6301\u5009\u8207\u640D\u76CA\u65BC\u80CC\u666F\u540C\u6B65\u4E2D\u3002")}function fo({blocking:t=!0}={}){if(!_)return;a.investorLoadDone=0,a.investorLoadTotal=uo(!1),document.body.classList.toggle("investor-blocking-load",t);let e=document.getElementById("investor-load-overlay");e&&(e.classList.remove("hidden"),e.classList.toggle("investor-load-overlay--refresh",!t),e.setAttribute("aria-busy","true"));let n=document.getElementById("refresh-now");n&&(n.disabled=!0),je(0,"spot")}function it(t){if(!_)return;a.investorLoadDone=Math.min(a.investorLoadTotal||1,a.investorLoadDone+1);let e=a.investorLoadTotal>0?a.investorLoadDone/a.investorLoadTotal:0;je(e,t)}function Ar(t){if(!_)return;if(!t){fo({blocking:!a.investorReady});return}je(1,"done"),a.investorReady=!0,document.body.classList.remove("investor-blocking-load"),document.body.classList.add("investor-ready");let e=document.getElementById("investor-load-overlay");e&&(e.classList.add("hidden"),e.classList.remove("investor-load-overlay--refresh"),e.setAttribute("aria-busy","false"));let n=document.getElementById("refresh-now");n&&(n.disabled=!1),Bt()}async function Pr({renderDependentViews:t=!0,updateDom:e=!0}={}){try{let n=await P("/api/spot");a.lastSpotUsd.BTC=l(n.BTC),a.lastSpotUsd.ETH=l(n.ETH),e&&(We(),t&&(pe(a.status,a.report,a.groups),Dt(a.status,a.report,a.groups)))}catch{}}function me(){return!!document.getElementById("charts-section")?.open}function mo(){return!!document.getElementById("stress-section")?.open}async function co(){try{let t=await P("/api/portfolio/snapshot");a.portfolioSnapshot=t,t?.source==="ledger"&&(a.dataFreshness.source="snapshot",a.dataFreshness.snapshotMs=l(t.freshness_ms),a.dataFreshness.live=!1)}catch{}}async function Nr(){let t=_e,e=!1,n=ct(t).then(()=>{throw e=!0,new Error("status timeout")});try{let o=await Promise.race([P("/api/status"),n]);return a.status=o,a.statusErrorOnce=!1,a.dataFreshness.source="live",a.dataFreshness.live=!0,a.dataFreshness.statusMs=0,o}catch(o){return e&&a.portfolioSnapshot?.portfolio?(a.statusErrorOnce||(B(u("Live sync is slow; showing last snapshot.","\u5373\u6642\u540C\u6B65\u8F03\u6162\uFF0C\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\u3002")),a.statusErrorOnce=!0),P("/api/status").then(r=>{a.status=r,a.dataFreshness.source="live",a.dataFreshness.live=!0,Ge()}).catch(()=>{}),null):(a.status=null,a.statusErrorOnce||(B(`status: ${o.message}`),a.statusErrorOnce=!0),null)}}async function qe({backgroundOnTimeout:t=!1,sections:e=null}={}){let n=_e,o=!1,r=P(Te(30,{sections:e})),s=_?Promise.race([r,ct(n).then(()=>{throw o=!0,new Error("dashboard bundle timeout")})]):r;try{return Re(await s),!0}catch(i){return _&&o&&a.portfolioSnapshot?.portfolio?(a.statusErrorOnce||(B(u("Live sync is slow; showing last snapshot.","\u5373\u6642\u540C\u6B65\u8F03\u6162\uFF0C\u5148\u986F\u793A\u6700\u8FD1\u5FEB\u7167\u3002")),a.statusErrorOnce=!0),t&&P(Te(30,{sections:e})).then(c=>{Re(c),Ge()}).catch(()=>{}),!1):((!_||!o)&&B(`dashboard bundle: ${i.message}`),!1)}}async function Ye({force:t=!1,investorFetchWrap:e=null}={}){if(!(!t&&!me())){if(!t&&a.chartsDataLoaded){await fe();return}if(!a.chartsLoadInFlight){a.chartsLoadInFlight=!0;try{await Pt();let n=()=>P("/api/cumulative_pnl_series").then(r=>{a.cumulativePnl=r}).catch(r=>B(`cumulative pnl: ${r.message}`)),o=()=>P(ae()).then(r=>{a.aprSeries=r}).catch(r=>B(`apr series: ${r.message}`));e?await Promise.all([e("cumulative",n),e("apr",o)]):await Promise.all([n(),o()]),a.chartsDataLoaded=!0,await fe()}finally{a.chartsLoadInFlight=!1}}}}async function Ze({force:t=!1}={}){if(!_&&!(!t&&!mo())){if(!t&&a.stressDataLoaded){Ut(a.stress);return}if(!a.stressLoadInFlight&&a.health?.has_private_creds){a.stressLoadInFlight=!0;try{let e=await P("/api/stress?shocks=0.1,0.2,0.3,0.4,0.5");a.stress=e,a.stressDataLoaded=!0,Ut(a.stress)}catch(e){B(`stress: ${e.message}`)}finally{a.stressLoadInFlight=!1}}}}function Ir(){return a.lastRefreshStartedMs?Math.max(0,It-(Date.now()-a.lastRefreshStartedMs)):0}async function ve({force:t=!1,silentIfLimited:e=!1,renderDashboard:n}={}){if(Ve=n??null,a.refreshInFlight){e||B(u("refresh already running","\u5DF2\u6709\u66F4\u65B0\u6B63\u5728\u9032\u884C"));return}let o=Ir();if(!t&&o>0){e||B(u(`refresh rate limited; wait ${Math.ceil(o/1e3)}s`,`\u8ACB\u7A0D\u5019 ${Math.ceil(o/1e3)} \u79D2\u5F8C\u518D\u8A66`));return}a.refreshInFlight=!0,a.lastRefreshStartedMs=Date.now();let r=_&&!a.investorReady;r?fo({blocking:!0}):_&&ht(!0,{indeterminate:!0});try{let i=function(){s||(s=!0,requestAnimationFrame(()=>{s=!1;try{n?.()}catch(h){console.error("renderDashboard failed",h),B(`render failed: ${h.message}`)}}))},c=function(h,R){return r?R().finally(()=>it(h)):R()},v=function(){return P("/api/groups").then(h=>{a.groups=h,i()}).catch(h=>{B(`groups: ${h.message}`)})},y=function(){return P(wn(30)).then(h=>{a.report=h,i()}).catch(h=>B(`realized summary: ${h.message}`))},x=function(){return P("/api/status").then(h=>{a.status=h,a.statusErrorOnce=!1,i()}).catch(h=>{a.status=null,a.statusErrorOnce||(B(`status: ${h.message}`),a.statusErrorOnce=!0)})},b=function(){return Ze({force:!0})},s=!1;try{let h=c("spot",()=>Pr({renderDependentViews:!_,updateDom:!0})),R=c("health",()=>P("/api/health").then(I=>{a.health=I}));await Promise.all([h,R])}catch(h){B(`health failed: ${h.message}`)}let d=!!a.health?.has_private_creds;r&&(a.investorLoadTotal=uo(d,{includeCharts:me()}));let p=!1;if(_&&r){try{await Promise.race([c("snapshot",co),ct(en)])}catch{}p=!0,Ar(!0),ht(!0,{indeterminate:!0}),i()}let f=r?(h,R)=>c(h,R):null,m=(h,R)=>f?f(h,R):R();async function C(){await m("groups",v),_?(await m("status",()=>Nr().then(()=>i())),await m("summary",y)):(await x(),await y())}async function E(){if(!d){await m("groups",v),a.status=null,a.report=null,a.stress=null,a.stressDataLoaded=!1;return}if(tn){if(_&&r&&await qe({sections:"status,groups"})){it("groups"),it("status"),i(),qe({sections:"realized_summary"}).then(I=>{I&&it("summary"),Ge()}).catch(()=>{});return}if(await qe({backgroundOnTimeout:_})){r&&(it("groups"),it("status"),it("summary")),i();return}}await C()}let T=[()=>E()];!_&&d&&mo()&&T.push(()=>b()),_&&!p&&T.push(()=>m("snapshot",co)),me()&&T.push(()=>Ye({force:!1,investorFetchWrap:f})),await Pn(T,Qe),_&&(a.stress=null),(!_||a.investorReady)&&i(),ht(!1),O("last-refresh",`${u("last refresh:","\u4E0A\u6B21\u66F4\u65B0\uFF1A")} ${lo()}`)}finally{a.refreshInFlight=!1,ht(!1),Ve=null}}function Je(){yn(a.status,a.groups),to(a.status),Qn(a.health),We(),_||(no(a.health,a.status),eo(a.status)),oo(a.status,a.report),pe(a.status,a.report,a.groups),fe().catch(t=>{console.error("performance charts failed",t)}),Dt(a.status,a.report,a.groups),_||Ut(a.stress)}function Mr(t){a.bookFilter=t;let e=document.querySelector("#book-filter");e&&e.querySelectorAll("button[data-book]").forEach(n=>{n.classList.toggle("filter-active",n.dataset.book===t)}),le(),ce(),ue()}function Fr(){let t=document.getElementById("auto-refresh");if(!t)return;function e(){a.autoRefreshHandle&&(clearInterval(a.autoRefreshHandle),a.autoRefreshHandle=null),t.checked&&(a.autoRefreshHandle=setInterval(()=>ve({silentIfLimited:!0,renderDashboard:Je}),It))}t.addEventListener("change",e),e()}function zr(){document.getElementById("refresh-now")?.addEventListener("click",()=>ve({renderDashboard:Je})),document.getElementById("book-filter")?.addEventListener("click",t=>{let e=t.target.closest("button[data-book]");e&&Mr(e.dataset.book)}),document.getElementById("activity-section")?.addEventListener("click",t=>{let e=t.target.closest("button.activity-page-btn");if(!e||e.disabled)return;let n=e.dataset.activitySection,o=e.dataset.direction==="next"?1:-1;n==="open"?a.activityOpenPage+=o:n==="closed"&&(a.activityClosedPage+=o),Dt(a.status,a.report,a.groups)}),document.getElementById("apr-window")?.addEventListener("change",async t=>{a.aprWindow=parseInt(t.target.value,10)||30;try{await Pt(),a.aprSeries=await P(ae())}catch(e){B(`apr series: ${e.message}`)}de()})}function Hr(){document.querySelectorAll("details.collapsible-section").forEach(t=>{t.addEventListener("toggle",()=>{if(t.open){if(t.id==="charts-section"){Ye().catch(e=>{console.error("chart data load failed",e)});return}if(t.id==="stress-section"){Ze().catch(e=>{console.error("stress load failed",e)});return}Bt()}})})}function vo(){let t=()=>{po(),zn(),zr(),Hr(),Fr(),ve({force:!0,renderDashboard:Je})};document.readyState==="loading"?document.addEventListener("DOMContentLoaded",t):t()}vo();})();
