# Canopy 產品架構

品牌名 **Canopy**（樹冠）。v1 只做一件事：在使用者**自己的 Deribit 子帳**上，托管跑 **Covered Call（備兌賣 call）**。名稱來由是森林樹冠：罩在已有現貨上方，不是把資產搬進平台。

對齊的參考對象是 [FULY.AI](https://www.fuly.ai/) 這類「交易所 BYOK 機器人 SaaS」，不是基金後台、也不是把舊投資人 portal 包一層。

## 1. 為什麼要先定架構

先前 MVP 把登入、方案、金鑰、risk tier、Pause 塞在同一頁。FULY 能賣得動，是因為產品切成四層，而且**設定頁不是第一個畫面**：

| 層 | FULY.AI | Canopy |
|----|---------|--------|
| 品牌 | 放貸機器人，不是代操資金 | 備兌賣 call 工具，不是代操／基金 |
| 教育 | 學院教學：KYC → 入金 → 融資錢包 → API 權限 | Deribit 子帳 → 轉入現貨 → API 權限白名單 |
| 調查 | 開通前問幣種、保留資金、自動／手動、風險指數 | 開通前問經驗、現貨、幣、目的、回撤、風險勾選 |
| 控制台 | 即時報表 + 開關 | 倉位、dry-run／live、Pause／Panic |

沒有前三層，控制台看起來會像內部 ops 工具。

## 2. 品牌來由

**Canopy**（中文只作註解：**樹冠**，品牌名仍用英文）。

英語 canopy 是森林最上層的樹冠：罩在**已經長成的樹**上，不是把樹移進溫室，也不是屋頂。Covered Call 同一結構——你先持有 BTC／ETH，再在上方賣出備兌 call。產品因此叫 Canopy：覆蓋你已有的持倉，不保管、不代操。

對外一句話：**樹冠罩住你已經持有的現貨。**

必須並寫的限制（樹冠隱喻的另一半）：

- 樹冠是遮蔭，不是屋頂 → 現貨下跌風險仍在
- 不說保護本金、穩定被動收入、保證 APR、無風險權利金
- 不沿用舊 repo 的投資人／AUM 品牌

曾考慮 Overlay、Callkeep、Premia。不選 Aegis／Shield：聽起來像避險保證。

視覺：沿用引擎 dashboard 的 Obsidian Terminal（DM Sans、IBM Plex Mono、炭黑、teal）。Landing／問卷／控制台同一套。

## 3. 產品原則

1. **BYOK software-only**：金鑰在使用者 Deribit 帳戶，平台不碰錢包權限、不收 AUM。
2. **單一策略**：v1 = Covered Call。Naked short、bull put、訊號 webhook 見 `PHASE2.md`。
3. **先模擬再實單**：任何實單方案都要滿 `DRY_RUN_MIN_DAYS`（預設 7）天 dry-run。Scout 永不實單。
4. **先調查再設定**：沒交開通問卷，不能存策略參數、不能啟動 bot。
5. **Kill switch 是產品核心**：Pause／Panic 永遠在控制台第一屏，且文案必須寫「不保證成交」。

## 4. 資訊架構

```
公開
  Landing        品牌、機制、不是什麼、FAQ、方案、登入
  Legal          條款／隱私／風險揭露／行銷禁令
登入後
  Waitlist       問卷可先填；核准前不能綁 key、不能訂閱
  開通精靈
    1 調查       經驗／現貨／幣／資金帶／目的／回撤／五則勾選
    2 建議       映射 Scout/Trader/Pro/Desk + tier + coins（可改訂）
    3 清單       子帳、現貨、API 權限；然後才進控制台
  控制台         行情、倉位、金鑰、參數、訂閱、Pause／Panic
```

不要做的事：一登入就丟 risk tier 下拉；把舊 `investor.html` 整頁 fork 過來；在 landing 放歷史 APR 區間。

## 5. 開通調查 → 建議映射

問卷是**適合性調查**，不是 KYC、不是投資建議。答案只用來預填方案與參數，使用者仍可改訂。

| 題 | 選項 | 用途 |
|----|------|------|
| 經驗 | novice / options / bots | 新手＋只想學 → Scout |
| 現貨 | none / transferring / already_on_deribit | 沒現貨 → Scout；已在 Deribit 且要 overlay → Trader+ |
| 標的 | BTC / ETH / both | Scout 只能 BTC；雙幣 → Pro／Desk |
| 資金帶 | under_10k / 10_50k / over_50k | 給營運看規模，不收 AUM、不改成交 |
| 目的 | learn / overlay / desk | desk → Desk；learn → Scout |
| 回撤 | conservative / balanced / aggressive | 對應 low / medium / high（再被方案上限夾住） |
| Sweep | yes/no | 僅 Pro／Desk |
| 五則勾選 | 非投顧、無 APR 保證、現貨下跌風險、金鑰自管、Panic 不保證成交 | 全部勾才能送出 |

映射規則（實作在 `cc_saas/onboarding.py`）：

- `intent=desk` → Desk
- `coins=both` 且不是純學習 → Pro（雙幣）
- `intent=overlay` 且帳戶裡已有／將轉入現貨 → Trader
- 其他（新手、無現貨、只想學）→ Scout
- 幣別最後再被方案 `allowed_coins` / `coins_max` 夾住

## 6. 開通清單（對齊 FULY 的 API 教學，換成 Deribit）

1. Deribit 帳戶已 KYC。
2. **開子帳**給 bot，不要用主帳。
3. 把要備兌的 BTC／ETH **現貨**轉進該子帳（Covered Call 需要現貨，不是保證金空賣）。
4. 建 API：`account:read` + `trade:read_write`。**不要**開 wallet／withdrawal。
5. 能設 IP 白名單就設成平台出口 IP。
6. 關掉同一子帳上會搶單的其他自動策略。
7. 接受：call 被行使 = 現貨可能被賣出。

## 7. 控制台 UX

沿用原專案元件：sticky header、chips、BTC／ETH 現貨卡、section card、stat tile、open-position card。

第一屏只有：狀態、倉位、dry-run／live／Pause／Panic。金鑰與參數放第二層，避免再變「設定表單當首頁」。

## 8. 方案角色（不變）

| 方案 | 誰該看到建議 |
|------|----------------|
| Scout $49 | 想先看引擎怎麼選約、還沒有現貨或沒賣過 call |
| Trader $99 | 單一幣、已有現貨、要 overlay |
| Pro $179 | BTC+ETH 或要 high tier／sweep |
| Desk $299 | 多子帳／之後要 webhook |

年繳是 Stripe 設定，不是另一個產品。

## 9. 明確不做（v1）

- 代操、HWM、1%+10%、一投資人一前端
- 保證 APR、活利息推播當成交話術（FULY 主打這句，我們不能抄）
- 主帳 API、wallet 權限
- 問卷當信用審核或拒絕開戶的法律 KYC
- 手機 App（FULY 有；我們第一版 Web）

## 10. 成功標準（開通完成）

使用者同時滿足：核准、問卷已交、有效訂閱、金鑰 ping 成功、設定與方案一致、desired = dry_run。Live 是之後的門檻，不是開通完成。
