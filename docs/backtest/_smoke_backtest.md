# 回測與黑天鵝風險報告（Deribit 公開資料）

- 產出時間：`2026-04-27T12:59:33.013472+00:00`
- 回測區間：`2024-01-01T00:00:00+00:00` → `2024-01-05T00:00:00+00:00`（resolution=1D）
- 參考資金（USDC）：`6300`

## 重要假設（請務必閱讀）
- **資料來源限制**：使用 Deribit `public/get_tradingview_chart_data`（OHLCV）與 index/DVOL 公開資料；歷史 greeks / 完整 order book / OI 可能不足。
- **Delta 近似**：若無歷史 greeks，使用 **DVOL 當作波動率 proxy** 以 Black‑Scholes 估算 delta，誤差會在 skew/事件日變大。
- **流動性近似**：回測用 candle close 代表成交價；黑天鵝情境額外加上滑價（slippage）來做保守估算。

## 基準回測結果（historical replay）
- **總損益（USDC）**：`0`
- **期末資金（USDC）**：`6300`
- **最大回撤（peak-to-trough）**：`0`

## 黑天鵝損失估算（stress overlay）
- 指標為「**在持倉日內立刻發生 shock**」的保守估算（不含動態管理）。

## Risk / Profit balance 改進（參數掃描）
- 本次未執行參數掃描。

## 建議（可直接落地的調整）
- **優先降槓桿（尾端風險最大來源）**：把 `BOOK_IM_TARGET/BOOK_IM_HARD` 往下調，並同步收緊 `PER_LEG_IM_CAP_PUT` 與 `EXPIRY_IM_CAP`。
- **把收益門檻改成「先活著」**：適度提高 `MIN_NET_APR`，但搭配更低 IM cap，避免為了 APR 追逐更危險的近價 put。
- **黑天鵝時段處置**：在 `CRISIS` regime 下（index 大跌或 DVOL 飆升）停開新倉；若已開倉，優先把最差 expiry/最大 gamma 的部位降槓桿。
