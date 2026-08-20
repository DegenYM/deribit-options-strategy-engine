# Phase 2 — 訊號監控與下一條策略線

觸發：大約 30 位付費用戶、控制面與 worker 穩定之後。

## 訊號監控（加值，不是第二個策略）

在不開放 naked short / bull put 的前提下，把引擎已有的公開訊號產品化：

- Regime：`normal` / `elevated` / `crisis`
- IV rank（BTC／ETH DVOL）
- 「接近可賣 call」：通過 delta／OTM／MIN_NET_APR 但尚未進場的候選
- Per-tenant Telegram／email；Desk 方案再加 webhook

實作建議：`marketd` 寫入共享 snapshot，API 提供 `/api/signals`，worker 不重複打 public 端點。

## 不做（仍屬 v3）

- 在同一 app 內切換 bull put spread / naked short
- 使用者自助改 MAX_GROUPS、stop、delta 帶
- Kubernetes

若要上其他策略，優先做成**獨立產品線**或 Desk 加購，避免與 Covered Call 風控文案混在一起。
