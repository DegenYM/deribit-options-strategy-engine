# 策略模型

## 核心設計

- `naked_short`：單腿 short option，骨架預設 `SHORT_OPTION_SIDE=put`（可改 `call` / `both`）；選約以 delta 為主、OTM 僅地板（無 max）。舊名 `naked_short_put` / `naked_short_call` 會自動正規化成 `naked_short`
- `bull_put_spread`：先買 long put 保護腿，再賣 short put，最大虧損以 spread width 封頂
- `covered_call`：只在既有 BTC/ETH 庫存足夠時賣 call，不自動買底層，也不使用 perp 作 cover；可選擇在 ITM 退場時同步賣 Deribit spot
- 可選擇是否啟用 `perp` delta hedge
- `spot` 不參與正常收益流程，只留給異常庫存處理
- 目標是 `1000 USDC` 參考資金下年化淨利 `200 USDC+`
- 預設 `dry-run first`，只有 `--live` 才會真的下單

## 掃描與風控

- 掃描 `Deribit Linear USDC Options` 與 `BTC/ETH-settled reversed options`
- 進場 DTE 由策略 **tier profile** 的 `PUT_DTE_MIN` / `PUT_DTE_MAX` 決定（covered call 多為 **7–35 天**；naked short **10–35 天**；bull put spread low tier 為 **12–21 天**）。`.env.example` 的 10–21 僅作 legacy 單檔 fallback
- short leg 會先過 delta、OTM、OI、book notional、spread ratio、APR 與 book IM/MM 門檻
- `bull_put_spread` 的 long put 以 `BULL_PUT_LONG_DELTA_MIN/MAX` 選擇，同到期且 strike 低於 short put
- `covered_call` 只使用 BTC/ETH 本位 book 的既有可用庫存作 cover，不會自動買現貨或用 perp 補 cover；tier profile 預設 **`COVERED_CALL_SPOT_EXIT_ENABLED=true`**（ITM 結算後賣 Deribit spot）
- 投資人 layout 下 **IV Rank 進場閘門**預設開啟（`config/shared/.env.defaults`）；`covered_call` 與 `naked_short` 策略骨架另覆寫為較寬鬆（BTC/ETH `MIN_IV_RANK=0.05`，`MIN_IV_MINUS_RV=0`），品質交由 delta／APR
- 只做流動性足夠的 short leg：`OI`、`book notional`、`spread ratio` 都要過門檻
- `MIN_LIQUID_EXPIRIES_REQUIRED` 可控制 DTE 視窗內至少需要幾個可交易 expiry 才允許開倉
- regime 分為 `normal / elevated / crisis`
- `crisis` 仍**完全停開新倉**。`elevated`（含 24h 指數回撤與 DVOL 放大）對 **`covered_call`** 不停開：仍可進場，但把有效 `*_DELTA_MAX` 收緊 `ELEVATED_DELTA_MAX_TIGHTEN`（預設 **0.02**，絕不低於既有 `*_DELTA_MIN`）。**`naked_short` 預設停開**（無底倉的 short put 是下跌尾部，elevated／連續下跌正是最不該新賣保險的時候）；要改走「收緊後仍可賣」須設 `NAKED_ALLOW_ELEVATED_ENTRY=true`，此時用較大的 `NAKED_ELEVATED_DELTA_MAX_TIGHTEN`（預設 **0.04**）。Naked 連續下跌（兩個交易日各自 ≥ `NAKED_ENTRY_DOWN_DAY_PCT`）升為 elevated 後走同一條停開路徑。`data_unavailable` 一律停開。`hard stop` 直接平倉；`soft trigger` 優先 roll，不行就平倉；`TP` 與 `time exit` 都會主動退場。naked short 防守需連續 **2** 個 manage cycle 確認（`DEFENSE_CONFIRM_CYCLES=2`）。

## 策略比較

### `naked_short`

單腿賣 OTM option，依 `SHORT_OPTION_SIDE` 控制方向（骨架預設 **`put`**）：

- `put`：只掃 short put（等同舊版 `naked_short_put`），下跌尾端風險最大。
- `call`：只掃 short call，上漲尾端風險最大。
- `both`：put 與 call 候選合併競爭 `TOP_N`；engine 不強制保留 call 名額。

選約以 **delta 為硬門檻與排序主軸**（優先於 TARGET APR）；`*_PUT_OTM_MIN` 僅作安全地板（**無 OTM max**），同 delta 下偏好更深 OTM。骨架 IV 閘門較寬鬆，薄權利金仍由 `MIN_NET_APR` 過濾（IVR 高可略收緊門檻，**預設不因低 IVR 放寬**）。連續下跌升為 elevated 後**停開新倉**（見上方 regime）。

### `bull_put_spread`

賣較高 strike put，同時買較低 strike put 作保護腿，最大虧損約為 spread width 減淨權利金。因為虧損被 long put 封頂，short put delta 可比 naked short 稍高，但淨權利金、long leg 流動性與 max-loss APR 要一起檢查。

### `covered_call`

只用既有 BTC/ETH 現貨庫存賣 call；現貨 cover 會降低 upside short call 的爆倉型風險。選約以**保留現貨**為原則：**delta 為硬門檻與排序主軸**（優先於 TARGET APR），`CALL_OTM_MIN` 僅作安全地板（依 tier：low 較高、high 較低），**不設 OTM max**；同 delta 下偏好更深 OTM。風險是上漲收益被履約價封頂，以及 ITM 結算後仍可能留下 spot exposure。Low 的進場 spread 上限為 **18%**（`INVERSE_MAX_SPREAD_RATIO=0.18`；medium／high 仍 15%）：上漲週期只放寬 bid-ask，**不放寬 OTM／delta**，以免把 LOW 做成更近 strike。

**獲利／退場口徑（幣本位）**：BTC/ETH 本位 covered call 的 take-profit、time-exit、`profit_capture` 以**權利金幣數**（進場均價 × 數量 − 進場 fee）對比買回成本衡量，**不**因標的指數上漲把同一 ETH/BTC premium 換算成更高 USDC 而誤判未達門檻。USDC linear 部位仍走 USDC 口徑。

**ITM 退場（預設 tier 設定）**：

- **Settlement spot exit**（`COVERED_CALL_SPOT_EXIT_ENABLED=true`）：僅在 short call **到期** ITM 結算後才標記 pending；下一輪 `manage` market 賣 **BTC_USDT / ETH_USDT**，數量為 **`cover − settlement_loss`**（僅 cover；權利金走 Profit swap）。該幣別 **SPOT SELL 尚未賣完**（pending／partial）時不會再開新 covered call，待賣量也不計入 available cover。到期前的 income exit（TP / time / early）或外部買回**不會**賣 cover（可另做 profit sweep）。若開啟 `COVERED_CALL_PROFIT_SWEEP_ENABLED`，ITM exit 完成後會另排程權利金 sweep。settlement 優先 Deribit transaction log，否則 intrinsic 估算。
- **ITM → cash-secured put**（`COVERED_CALL_ITM_TO_CASH_SECURED_ENABLED=false` 預設關閉）：ITM spot exit **賣成 USDC** 後，同一 covered_call 子帳賣短天期 USDC linear put，履約價貼近原 call 行權價。手續費／結算／進位若讓 USDC 剛好不夠鎖滿 cover，會在設定窗內往下抓 strike。開啟後舊的 USDT 賣出也當已換成 USDC 來掃 CSP；該 group 不再自動買回 cover。進場 **IOC 打 bid**（不成交下個 cycle 重試，不掛 GTC mid）。預覽挑選：`./bot --account covered_call scan --cash-secured [--from-group 0095]`（不下單；已開倉也可看排名）。OI／名目仍過 CSP 門檻（預設 BTC 0.5／ETH 5／名目 3000）。先前取消 mid 掛單的 `operator_cancelled` 會再掃一次。CSP **預設持有至到期**；put **OTM** 到期後同一母倉會再用剩餘 USDC **再賣下一輪**短天期 put（cover 已補回或 put ITM 指派則停止）。母倉顯示 call＋各輪 CSP 的累計 PnL。ITM 且時間價值夠薄／臨近到期、且 put 盤口流動性過關時可 **self-assign**（`COVERED_CALL_CSP_SELF_ASSIGN_*`：買回 put＋買 spot；短 DTE 價差過寬則等到期）。到期 **ITM** 後用剩餘 USDC 掛 mid 買 `BTC_USDC` / `ETH_USDC` 補回 cover；OTM 到期只留現金、不買現貨。權利金去向由 `COVERED_CALL_CSP_PREMIUM_TARGET`（`usdc` 預設／`spot`）決定：設 `spot` 時在 put **平倉／到期後**依實收權利金換成 native 現貨（ITM 補 cover 完成後才 swap）；履約保證金不動。**主動 roll** 見下節，預設關。
- **自動買回 cover**（`COVERED_CALL_AUTO_SPOT_RESTORE_ENABLED=false` 預設關閉）：ITM spot exit **賣完**後，live `manage` 立刻掛 **GTC 限價**於買回上限（損益兩平 × `(1 − MIN_EDGE_PCT)`，預設 0.1%），數量為 native unrestored（進位到 **USDC linear 最小下單量**，BTC `0.01` / ETH `0.1`，不超過 cover）。成交前只對帳，**不再下市價單**。你在交易所或後台**取消未成交買單**後會記 `operator_cancelled`，**不會自動重掛**。`spot_exit_status=skipped`（例如手動提領）不會掛。手動仍可用 `./bot spot-restore`。已改走 cash-secured（USDC）的 group 不會掛買回。
- **Robust exit**（`COVERED_CALL_ROBUST_EXIT_ENABLED=false` 為 tier 預設）：接近到期且 ITM 時**先買回** short call，再賣 spot cover（不扣 settlement；premium 已用於買回）。可設 `COVERED_CALL_ITM_CONFIRM_CYCLES` 避免 wick 假觸發。

預設啟用 **槽位分配**（`COVERED_CALL_SLOT_SIZING=true`）：依合約最小單位（BTC 通常 0.1）把可填 cover 整數均分到剩餘 `MAX_GROUPS_PER_CURRENCY` 槽位（`ceil(units / slots)`），避免 `cover ÷ 槽位數` 再 floor 後留下無法再開的碎量。例如 0.5 BTC、3 槽、min 0.1 → 依序約 **0.2 / 0.2 / 0.1**（仍受盤口 `best_bid_amount` 上限）。

## Payoff 示意

下列圖表是單位化 payoff 示意，用來快速比較到期價格與收益形狀；實際收益仍以 `scan` / `enter-best` 的成交 credit、debit、fee、slippage 與持倉天數為準。

**`naked_short`（短 put 範例）**：假設 short put strike `K=100`、收到權利金 `P=2`。價格高於 `K` 時收益封頂為權利金，跌破損益兩平點後虧損跟著標的下跌擴大。short call 形狀對稱，只是價格上漲超過 `K` 後虧損擴大。

![Naked short put payoff](strategy-payoffs/naked-short-put.png)

**`bull_put_spread`**：假設 short put `K=100`、long put `L=90`、淨 credit `P=2`。上方收益同樣封頂，但下跌最大虧損被 long put 限制。

![Bull put spread payoff](strategy-payoffs/bull-put-spread.png)

**`covered_call`**：假設持有現貨成本 `S0=100`、short call strike `K=110`、收到權利金 `P=2`。權利金提供一點下跌緩衝，但上漲超過 `K` 後總收益被封頂。

![Covered call payoff](strategy-payoffs/covered-call.png)

## 策略參數

各策略骨架位於 [`config/shared/strategies/`](../config/shared/strategies/)；delta / APR / IM 等 tier 參數在 [`config/shared/strategies/tiers/`](../config/shared/strategies/tiers/)。投資人視角對照見 [風險分級與 APR 說明](investor-risk-tiers-apr-zh-TW.md)；載入順序見 [設定與環境變數](configuration-zh-TW.md#策略-profile-與-tier)。

### Wave 1–2 動態 knobs（骨架預設）

| 變數 | 引擎預設 | covered_call 骨架 | naked_short 骨架 | 作用 |
|------|----------|-------------------|------------------|------|
| `ENABLE_DYNAMIC_TARGET_DELTA` | `false` | **`true`** | **`true`** | 用 VRP（IV−RV）在**偏好 delta 帶**內移動排序目標；**不改**硬 `*_DELTA_MIN/MAX` |
| `DYNAMIC_TARGET_DELTA_STRENGTH` | `0.5` | **`0.3`** | **`0.3`** | 滿訊號時移動偏好半帶的比例（保守） |
| `NAKED_DYNAMIC_DELTA_ALLOW_CLOSER` | `false` | n/a | **`false`** | `false` 時 naked 薄 VRP **不**往 ATM 拉；只允許富 VRP 往更 OTM |
| `ENABLE_DYNAMIC_MIN_NET_APR` | `false` | **`true`** | **`true`** | IVR 高則略收緊 `MIN_NET_APR`；CC 低 IVR 可略放寬，naked 預設不放寬 |
| `DYNAMIC_MIN_NET_APR_MAX_SHIFT` | `0.005` | 繼承 | 繼承 | 絕對 APR 位移上限 |
| `NAKED_DYNAMIC_MIN_NET_APR_ALLOW_LOOSEN` | `false` | n/a | **`false`** | `true` 才允許 naked 因低 IVR 降低 `MIN_NET_APR` |
| `DYNAMIC_MIN_NET_APR_FLOOR` | 空 → `MIN_NET_APR × 0.7` | 繼承 | 繼承 | 放寬下限（naked 預設用不到） |
| `ELEVATED_DELTA_MAX_TIGHTEN` | `0.02` | `0.02` | 僅 CC 路徑 | elevated 時 CC 有效 delta_max 減量 |
| `NAKED_ELEVATED_DELTA_MAX_TIGHTEN` | `0.04` | n/a | `0.04` | 僅在 naked 選擇 elevated 仍可賣時使用 |
| `ELEVATED_MAX_GROUPS_TIGHTEN` | `0`（關） | `0` | `0` | elevated 時 `MAX_GROUPS_PER_CURRENCY` 減 N；0 = 本波不啟用 |
| `NAKED_ALLOW_ELEVATED_ENTRY` | **`false`** | n/a | **`false`** | `true` 時 naked elevated 收緊後仍可賣 |

`naked_short` **不是** covered_call 的複製品：同樣開動態 target delta 與 MIN_NET_APR scaler，但只往更安全的方向動（OTM-only、tighten-only），elevated／down-streak 預設停開。目前 jack／youming 的 naked 仍是 tracking-only，不會因此自動 live 下單。`bull_put` **未啟用**。

### CSP DTE 窗

`COVERED_CALL_CSP_DTE_MIN` / `COVERED_CALL_CSP_DTE_MAX` 引擎預設 **2–10**。範例見 [`config/investors/_example/.env.investor.example`](../config/investors/_example/.env.investor.example)。**不要**為了本波去改 live eugene / youming / jack 的 CSP DTE。到期 OTM 後再用剩餘 USDC 賣下一輪短天期 put（既有 wheel）≠ 主動提前買回 OTM CSP 再換新約。

### CSP 主動 roll（預設關）

`COVERED_CALL_CSP_ACTIVE_ROLL_ENABLED` 預設 **false**。打開後，**開倉中且 OTM** 的 cash-secured put 可在到期前買回，再賣一張仍在既有 CSP DTE／母倉履約價窗（`COVERED_CALL_CSP_STRIKE_FLOOR_PCT`）內、**換算日收益更高**的 USDC put。同一張或更早到期都可以，只要 `(新 bid − 換倉手續費) / 新 DTE > 平倉 ask / 剩餘 DTE`。ITM 仍走 self-assign／到期，不會跟主動 roll 同一 cycle 搶路徑。

閘門（任一失敗就持有）：剩餘 DTE 須 **> `COVERED_CALL_CSP_ACTIVE_ROLL_MIN_DTE`（2）且 ≤ MAX_DTE（10）**；剩餘時間價值／`max(原權利金, 內在價值+TV)` 須 ≥ `MIN_TV_RATIO`（0.25）；平倉盤口重用 self-assign 流動性（雙邊、`MAX_SPREAD_RATIO` 0.25、ask ≥ 平倉量）；替換約須通過與 `scan --cash-secured` 相同的 OI／名目，且價差不過寬；日收益沒有嚴格更高就 `daily_yield_not_higher`、抱到到期。Live 先 reduce_only 買回，成交後才 IOC 打 bid 開新約；新約失敗則 USDC 停泊、下個 cycle **只重試進場**、不再平第二次，且重試仍須日收益高於剛買回那張的剩餘 TV（不會把同一張用更差的 bid 再賣回去）。不打市價傾銷。

### 營運 playbook（觀察後再開，不要默默改 live 資金檔）

- **youming** covered_call 可在 `accounts.toml` 把 `risk_tier` 對齊 **low**（較深 OTM、較低 `MIN_NET_APR`）。本波**沒有**改 youming 的 live env。
- **jack** 可在觀察 `manage` dry-run 後再開 `COVERED_CALL_CSP_SELF_ASSIGN_ENABLED=true`（ITM + 薄時間價值 + 流動性過關才買回 put 補 spot）。本波**沒有**替 jack 翻這個開關。
- **an / ma / pat** 維持 settlement spot-exit 路徑，不要開 self-assign。

### CSP 主動 roll（Wave 3，預設關）

程式已接上，但 **master switch 預設 false**，live 投資人檔不會被翻開。觀察 wheel／self-assign 穩定、並用 `manage` dry-run 看到 `cash_secured_active_roll` 的 `would_place`／reason 後，再逐帳考慮開啟。
