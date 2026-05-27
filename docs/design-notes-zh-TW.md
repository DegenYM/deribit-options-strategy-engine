# 設計備註

- 認證為了試用簡化，HTTP private request 直接走 Basic Auth
- 掃描同時支援 `quote_currency=settlement_currency=USDC` 的線性 options，以及 `quote_currency=settlement_currency=BTC/ETH` 的 reversed options
- `portfolio APR` 用 `annualized net pnl / REFERENCE_CAPITAL_USDC`
- 已平倉表的 **`Annualized`**：`(realized_pnl / 該筆倉位抵押名目) × (365 / holding days)`。covered call / 逆線 naked 分母通常為 `quantity`（1 BTC/ETH 每張）；USDC put 為 `strike × quantity`；bull put spread 為 `estimated_im_collateral`（max loss）。`realized_pnl` 仍為 USDC 等價；BTC／ETH 本位優先用 `realized_pnl_collateral_native`
- **`Return / max-loss`** 仍為 `realized_pnl / max_loss`（與上列年化分母口徑不同時，兩欄數字不必一致）
- 所有 `credit / debit / max loss / report` 內部都統一換算成 `USDC equivalent`
- 本地狀態保存在 `STATE_FILE`；多子帳建議使用 `.state/investors/<id>/<slug>.json`
- `report` 讀本地 state 中已關閉 spread 的 realized 資料；若啟用 perp hedge，報表仍只統計 spread PnL，不含 perp hedge PnL
- `run` 會先做 `manage`，再在條件允許時嘗試 `enter-best`
