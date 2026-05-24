# 投資人前置作業指南

**Deribit 期權策略組合**｜版本 1.0

> 本文件說明投資人需自行完成的 Deribit 帳戶設定步驟。正式權利義務以雙方簽署之投資管理協議為準。Deribit 為獨立交易所；能否開戶、入金、交易期權，以你所在地與 Deribit 當下規定為準。本指南不構成投資建議。

---

## 開始前：先搞懂三個名詞

| 名詞 | 白話說明 |
|------|----------|
| **主帳戶** | 你註冊 Deribit 時的總帳戶。入金通常先到這裡。 |
| **策略子帳** | 主帳底下的分戶，**一個策略一個**（如 `naked`）。自動交易只用這裡的 API。 |
| **費用專戶（Fee account）** | 另一個獨立子帳（建議名稱 `fee`）。季結算後你把**管理費／績效費**劃轉到這裡；與策略交易分開。 |
| **API Key** | 給程式或管理方用的鑰匙。**策略子帳**與 **Fee 專戶** 各用一把，權限不同（見下文）。 |

**建議順序：**

1. 註冊 Deribit 並完成身分驗證  
2. 入金到**主帳戶**（注意幣種與鏈，見第二節）  
3. 建立**策略子帳** + **Fee 專戶**，並將各帳保證金模式設為 **Segregated Portfolio Margin**  
4. 從主帳把資金劃到各**策略子帳**  
5. 在每個**策略子帳**建 API Key（**account:read + trade:read_write**；**不要**開 **wallet:read_write**）  
6. 在 **Fee 專戶**建 API Key（**account:read + wallet:read_write**；**不要**開 trade），交給管理方收取費用  
7. 提供**儀表板登入用 Email**（給 Cloudflare 白名單）  
8. 填寫**交接清單**（第八節）交給管理方  

---

## 一、註冊主帳戶與安全設定

### 1.1 註冊

1. 開啟註冊連結（已帶管理方推薦碼）：[https://www.deribit.com/?reg=20929.3875](https://www.deribit.com/?reg=20929.3875)。  
2. 點 **Register**／**Create account**，用 Email 註冊並設定強密碼。  
3. 到信箱點驗證連結，完成 Email 驗證。

![Deribit 註冊頁](./img/onboarding/01-register.png)

### 1.2 身分驗證（KYC）

1. 登入後，依畫面提示完成 **Verify identity / KYC**（上傳證件、自拍等）。  
2. 審核通過前，可能無法入金或交易，請預留 **1～數個工作天**。  
3. 審核完成後，確認帳戶狀態為可交易（介面通常會顯示 Verified 或類似字樣）。

### 1.3 請務必開啟 2FA

1. 進入 **Account → Security**（或 **Profile → Security**）。  
2. 啟用 **Two-Factor Authentication（2FA）**，建議用 Google Authenticator 等 App。  
3. **保存好** 2FA 備用碼；遺失會很難登入。

**注意**

- 不要把登入密碼、2FA 備用碼交給任何人。  
- **不要**把「主帳戶」的 API Key 交給策略方；後面只在**子帳戶**裡建 Key。

---

## 二、入金（把資金轉進 Deribit）

資金會先進你的**主帳戶**。之後第三、四節才會把錢分到各**子帳戶**。

### 2.1 找到入金頁面

1. 登入 Deribit（確認右上角是**主帳戶**，不是某個子帳）。  
2. 點上方或側邊的 **Wallet**（錢包）。  
3. 選 **Deposit**（入金／充值）。

![Wallet → Deposit](./img/onboarding/03-wallet-deposit-menu.png)

### 2.2 選擇幣種與網路（很重要）

入金時請依下表選擇**幣種**與**鏈（網路）**；外部錢包／交易所提現時必須與 Deribit 顯示**完全一致**。

| 幣種 | Deribit 入金請選的鏈／網路 |
|------|---------------------------|
| **BTC** | **Bitcoin（BTC）** 鏈 |
| **ETH** | **Ethereum mainnet**（以太坊主網） |
| **USDC** | **Ethereum mainnet**（以太坊主網，ERC-20） |

**請務必確認：**

| 注意事項 | 說明 |
|----------|------|
| 地址與網路要一致 | 選錯鏈可能無法到帳或資產遺失。 |
| 先小額測試 | 第一次建議先轉**小額**（例如 10～50 USDC），到帳後再轉大額。 |
| 保留手續費 | 從 Ethereum mainnet 提現時，帳上要留一點 **ETH** 當 Gas。 |

### 2.3 複製地址並從外部轉帳

1. 在 Deposit 頁**複製** Deribit 給你的入金地址（或掃 QR Code）。  
2. 打開你平常用的交易所或錢包（例如 Binance、OKX、硬體錢包）。  
3. 發起**提現／轉帳**：貼上剛複製的地址、選**同一條鏈**、輸入金額、確認。  
4. 回到 Deribit → Wallet，查看該幣種餘額是否增加。鏈上轉帳通常需數分鐘到數十分鐘。

### 2.4 各策略需要什麼幣？（供你入金參考）

實際金額以你與管理方簽約為準；入金時建議**多留一點緩衝**（保證金、手續費、行情波動）。

| 策略名稱（管理方會跟你說開哪幾個） | 子帳建議名稱 | 你需要準備的資產 |
|----------------------------------|--------------|------------------|
| 裸賣期權（Naked short） | `naked` | **僅 USDC**（劃到子帳後作保證金） |
| 牛市看跌價差（Bull put spread） | `bull_put` | **僅 USDC** |
| 備兌賣 Call（Covered call） | `covered_call` | **僅 BTC 或 ETH 現貨**作備兌與保證金（**不需**劃 USDC）；程式**不會**幫你買現貨 |

**注意**

- 入金完成後，錢還在**主帳**；第四節才要劃到**策略子帳**（Fee 專戶通常等收到帳單再劃入）。

---

## 三、建立子帳戶（策略子帳 + Fee 專戶）

### 3.1 為什麼要開子帳？

- 不同策略**分開資金**，一個策略出狀況不會拖垮全部。  
- 每個子帳一把 **API Key**，程式只能動該子帳，**不能**從你的 Key 提領到外部。  
- 對帳、看績效比較清楚。

### 3.2 建立步驟

1. 確認目前登入的是**主帳戶**（介面通常會顯示 Main account 或你的主帳名稱）。  
2. 打開 **Account** → **Subaccounts**（有些版本在 **Portfolio** 底下）。  
3. 點 **Create subaccount**（或 **Add subaccount**）。  
4. 依管理方給你的清單建立子帳，**名稱建議**如下（方便雙方對照）：

   | 用途 | 建議子帳名稱 |
   |------|--------------|
   | 備兌賣 Call | `covered_call` |
   | 裸賣期權 | `naked` |
   | 牛市看跌價差 | `bull_put` |
   | **費用專戶（必建）** | `fee` |

   若你只開其中一、兩個策略，只建對應的**策略**子帳即可；**Fee 專戶仍建議一律建立**（即使尚未有應付費用，餘額可為 0）。

   **Fee 專戶**：不跑策略、不放交易保證金；季結算後才把帳單金額從主帳或策略子帳**劃轉**進來。詳見《投資人分潤與計費說明書》第五節。

5. 建立完成後，畫面上應能看到子帳列表（餘額一開始通常是 0）。

**注意**

- 子帳名稱建立後往往**不能隨意改名**，請第一次就填對。  
- 還沒劃轉資金前，子帳餘額為 0 是正常的。

### 3.3 保證金模式：一律設為 Segregated Portfolio Margin

管理方策略以 **Segregated Portfolio Margin** 運作。請對**主帳戶**以及**每一個**子帳（含 `fee`）完成設定：

1. 切換到要設定的帳戶（主帳或某一子帳）。  
2. 點選 **My Account** → **Portfolio Margin**。  
3. 點 **Change Margin**。  
4. 選擇 **Segregated Portfolio Margin**，確認儲存。  
5. 對下一個帳戶重複上述步驟，直到主帳與所有子帳皆為 Segregated Portfolio Margin。

![Change Margin → Segregated Portfolio Margin](./img/onboarding/15.margin-selection.png)

**注意**

- 若子帳仍為其他保證金模式，可能導致保證金計算與策略預期不符；啟用實單前請務必確認。  
- 不確定目前模式時，可在 **Portfolio Margin** 頁面查看，或截圖請管理方協助確認。

---

## 四、從主帳把資金劃到子帳

### 4.1 找到劃轉功能

1. 仍在 Deribit 後台，找到 **Transfer**（劃轉）或 **Internal transfer**。  
   常見路徑：**Wallet → Transfer**，或 **Subaccounts** 頁面裡的 **Transfer**。  
2. **From（來源）** 選：**Main account（主帳）**。  
3. **To（目標）** 選：某一個子帳，例如 `naked`。

### 4.2 依策略劃轉（重複直到分完）

對**每一個**要啟用的子帳各做一次：

1. 選幣種（**naked**／**bull_put**：USDC；**covered_call**：BTC 或 ETH 現貨，**不要**劃 USDC）。  
2. 輸入金額（依你與管理方約定的規模）。  
3. 確認劃轉。  
4. 切換到該**子帳**檢視餘額是否正確。

**切換子帳的方式（介面可能略有不同）：**

- 右上角帳戶選單 → 選擇子帳名稱；或  
- Subaccounts 列表 → 點 **Switch** / **Enter** 進入該子帳。

**注意**

- **劃轉是即時的**，在主帳與子帳之間，通常**沒有**鏈上手續費。  
- 劃錯子帳可以再用 Transfer 轉回主帳或轉到正確子帳（需你本人操作）。  
- **covered_call** 子帳：僅劃入約定數量的 **BTC／ETH 現貨**（現貨即保證金，**不需** USDC）。  
- **naked**、**bull_put**：請劃入 **USDC** 即可。

---

## 五、策略子帳的 API Key（每個策略子帳各一把）

以下僅適用 **`naked` / `covered_call` / `bull_put` 等策略子帳**，**不適用** Fee 專戶（Fee 專戶見第六節）。

### 5.1 一定要先「進入」該策略子帳

API Key 綁在**當前所在帳戶**上：

1. 切換到策略子帳（例如 `naked`），確認**不是** Main account、**不是** `fee`。  
2. 再建立 Key。

### 5.2 建立 API Key：畫面順序（請照這個做）

路徑：**Account → API** → **Add new key**（或 Create a new API key）。  
以下在**已切到策略子帳**的前提下操作（見 5.1）。

#### 步驟 A — 選金鑰類型（第一步，只做一次）

| 順序 | 畫面 | 你怎麼選 |
|------|------|----------|
| A1 | **Select key type** | 選 **Deribit-generated key**（由 Deribit 產生 Client ID / Secret） |
| A2 | Self-generated key | **不要選**（除非管理方明确要求） |

選 **Deribit-generated key** 並按繼續後，才會進入**權限設定頁**（六個下拉選單 + Name + IP）。

#### 步驟 B — 設定下拉選單（Deribit-generated key **之後**，由上到下）

預設六項都是 **none**。策略子帳請改成：

| 順序 | 畫面欄位 | 策略子帳請選 |
|------|----------|--------------|
| B1 | Block Trade | **none** |
| B2 | Block RFQ | **none** |
| B3 | Account | **read** ← 讀餘額／權益（bot「查詢」靠這項） |
| B4 | Trade | **read_write** ← 下單、平倉 |
| B5 | Wallet | **none** ← **不要**選 read 或 read_write |
| B6 | Custody | **none** |
| B7 | Name | 建議填 `naked-strategy`（可選） |
| B8 | Features | 留空／預設 |
| B9 | IP Whitelist | 依管理方指示或留空 |

![策略子帳 API 權限設定（Account=read、Trade=read_write、Wallet=none）](./img/onboarding/10-api-strategy-permissions.png)

#### 步驟 C — 建立並保存（最後）

| 順序 | 動作 |
|------|------|
| C1 | 按 **Create**／確認 |
| C2 | 複製 **Client ID**、**Client Secret**（Secret **只顯示一次**） |
| C3 | 安全交付管理方（見 5.3） |

![API Key 建立成功（請妥善保存 Secret）](./img/onboarding/11-api-key-created.png)

### 5.3 交付給管理方（策略用）

每個**策略子帳**一組，透過安全管道交付：

```text
類型：策略子帳
子帳名稱：naked
環境：mainnet
API Key：________________
API Secret：________________
```

每個策略子帳重複 5.1～5.3。

---

## 六、費用專戶（Fee account）與 API Key

管理費、績效費**不會**從策略子帳自動扣款。請另建 **`fee` 子帳**，並在季結算後把帳單金額**劃轉**到這裡。

### 6.1 建立 Fee 專戶

1. 與第三節相同路徑建立子帳，名稱建議 **`fee`**。  
2. 平常餘額可為 0；**不必**把策略保證金劃進 Fee 專戶。  
3. 收到管理方季結帳單後，用 **Transfer** 從主帳或策略子帳把 **USDC** 劃到 `fee` 子帳（金額以帳單為準）。

### 6.2 Fee 專戶 API Key：畫面順序（與策略子帳相同流程）

1. **切換到 `fee` 子帳**（確認不是策略子帳）。  
2. **Account → API** → **Add new key**。  
3. **步驟 A**：選 **Deribit-generated key** → 繼續。  
4. **步驟 B** — 下拉選單（與第五節同一頁，但選項不同）：

   | 順序 | 欄位 | Fee 專戶請選 |
   |------|------|--------------|
   | B1 | Block Trade | **none** |
   | B2 | Block RFQ | **none** |
   | B3 | Account | **read** |
   | B4 | Trade | **none**（此帳不跑策略） |
   | B5 | Wallet | **read_write**（管理方查餘額並自專戶轉帳） |
   | B6 | Custody | **none** |
   | B7 | Name | 建議 `fee-collection` |
   | B8 | Features | 預設 |
   | B9 | IP Whitelist | 依管理方指示 |

5. **步驟 C**：建立 → 保存 Client ID / Secret → **單獨**交給管理方（勿與策略 Key 混在同一則訊息）。

![Fee 專戶 API 權限（Account=read、Wallet=read_write、Trade=none）](./img/onboarding/13-api-fee-permissions.png)

#### 策略子帳 vs Fee 專戶（對照）

| 欄位 | 策略子帳 | Fee 專戶 |
|------|----------|----------|
| Block Trade / Block RFQ / Custody | none | none |
| Account | read | read |
| Trade | read_write | none |
| Wallet | **none** | **read_write** |

### 6.3 交付給管理方（費用專戶用）

```text
類型：費用專戶
子帳名稱：fee
環境：mainnet
API Key：________________
API Secret：________________
備註：已開啟 Wallet 讀寫，未開啟 Trade
```

### 6.4 你應知道的事

- Fee 專戶裡的錢 = **已結算、待管理方確認收取**的費用；策略交易仍在策略子帳進行。  
- 若 Fee 專戶 API 外洩，風險限於該專戶餘額；故**勿**把大額策略資金放在 Fee 專戶。  
- 外洩時：刪除 Fee 專戶 Key、通知管理方，必要時將 Fee 專戶餘額劃回主帳後再建新 Key。

---

## 七、儀表板登入用 Email（Zero Trust）

管理方會提供一個 **HTTPS 專屬網址** 讓你看持倉與績效摘要。為了只有你能開啟，需要你的 **Email** 加入白名單。

### 7.1 你需要提供

- 一個你**平常會用來收信、登入**的 Email，例如：  
  `your.name@gmail.com`  
- 若管理方說用 **Google 登入**，請提供該 Google 帳號的 Email。  
- 可事先告知管理方你慣用哪一種登入方式。

### 7.2 管理方設定好之後，你如何第一次登入

1. 用瀏覽器（建議 Chrome）開啟管理方給你的網址（例如 `https://你的名字.xxx.com/`）。  
2. 會先出現 **Cloudflare Access** 登入頁（不是你的 Deribit 密碼）。  
3. 輸入**已白名單的 Email**，或點 **Sign in with Google**。  
4. 通過後才會看到投資人績效頁（繁中頁路徑可能是 `/investor.zh.html`）。

![Cloudflare Access 登入](./img/onboarding/14-cloudflare-access-login.png)

**注意**

- **不要**把儀表板網址貼在公開場合；知道網址的人若未在白名單，理論上無法登入，但仍應保密。  
- 登入問題（收不到驗證信、Google 帳號不對）請聯絡管理方，不要自行改 Deribit 設定。

---

## 八、交接清單（填好交給管理方）

**建議**：向管理方索取 [`config/handoff/handoff.template.toml`](../config/handoff/handoff.template.toml)，填好後經**安全管道**交回（管理方執行 `./bot investor import-handoff`）。以下為同內容的文字版備用。

請複製以下內容填寫：

```text
【基本資料】
姓名／暱稱：________________
聯絡 Email：________________
Deribit 註冊 Email：________________

【儀表板】
用於登入白名單的 Email：________________
慣用登入方式：□ Email 驗證碼  □ Google

【環境】
正式環境 mainnet

【保證金模式】
□ 主帳與所有子帳（含 fee）均已設為 Segregated Portfolio Margin

【已建立的子帳與劃轉（USDC 策略填 USDC 等價約略即可）】
□ naked          子帳名稱：________  已劃轉 USDC：________
□ bull_put       子帳名稱：________  已劃轉 USDC：________

【covered_call 若啟用（僅現貨，無 USDC）】
子帳名稱：________
備兌 BTC 數量：________
備兌 ETH 數量：________

【Fee 費用專戶】
子帳名稱：________（建議 fee）
□ 已建立子帳
□ 已交付 Fee 專戶 API（Read + Wallet 讀寫，未開 Trade）

【API Key — 策略子帳】
□ 已透過安全管道交付（策略共 ____ 組）
□ 策略子帳已開 account:read + trade，且未開 wallet:read_write

【其他】
備註：________________
```

---

## 九、請勿做的事（總整理）

| 請勿 | 原因 |
|------|------|
| 把主帳 API Key 交給管理方 | 風險範圍過大 |
| 在**策略子帳** API 開啟 **wallet:read_write** | 金鑰外洩可能被提款或對外轉出 |
| 把**策略子帳** Key 當 Fee 專戶 Key 使用 | 權限設計不同，應分開兩把 |
| 在 Fee 專戶開啟 Trade 權限 | Fee 專戶不應下單 |
| 把大額資金長期放在 Fee 專戶 | 僅應放應付帳單金額 |
| 用聊天軟體明文傳 Secret | 容易被轉發或外洩 |
| 入金選錯鏈或填錯地址 | 可能永久遺失 |
| 子帳保證金模式未設為 Segregated Portfolio Margin | 與策略保證金計算不一致 |
| 在 covered_call 子帳劃入 USDC 當保證金 | 此策略僅用 BTC／ETH 現貨作保證金 |
| 策略子帳尚未劃資就要求實單 | 無保證金可運作 |
| 把儀表板網址公開分享 | 應僅限本人與管理方知道 |

---

## 十、常見問題

**Q：主帳和子帳的錢可以互轉嗎？**  
A：可以。在 Deribit 用 **Transfer** 在主帳與子帳之間即時劃轉，通常不收鏈上費。

**Q：我只做一個策略，還需要子帳嗎？**  
A：管理方通常仍會要求至少一個子帳 + 一把 Key，以便權限隔離與對帳。

**Q：管理費／績效費怎麼付？**  
A：季結算後管理方開帳單；你把 USDC **劃轉到 Fee 專戶**（`fee` 子帳）。管理方用 Fee 專戶的 API（Wallet 讀寫）確認並轉出。**不會**從策略子帳自動扣。詳見《投資人分潤與計費說明書》。

**Q：下拉選單只有 none / read / read_write 怎麼選？**  
A：策略子帳：**Account = read**、**Trade = read_write**、**Wallet = none**（其餘也 none）。Fee 專戶：**Account = read**、**Wallet = read_write**、**Trade = none**。

**Q：一定要先選 Deribit-generated key 嗎？**  
A：是。先選金鑰類型，**之後**才會出現六個權限下拉選單；不要跳過類型選擇就直接找權限頁。

**Q：策略子帳 Wallet 能不能選 read？**  
A：**建議維持 none。** 讀餘額靠 **Account = read** 即可；Wallet 開到 read 雖比 read_write 安全，但對跑策略沒有必要。

**Q：為什麼 Fee 專戶可以開 wallet:read_write，策略子帳卻不行？**  
A：Fee 專戶只放應付帳單金額，管理方在**該專戶餘額內**用 wallet 權限做內部轉帳；策略子帳放保證金，不應給任何「錢包寫入」權限。

**Q：程式會幫我從交易所提幣到銀行嗎？**  
A：不會。策略子帳不開 wallet:read_write；Fee 專戶僅供帳戶內收取已劃轉費用。

**Q：保證金模式要怎麼設？**  
A：主帳與所有子帳請一律設為 **Segregated Portfolio Margin**：**My Account** → **Portfolio Margin** → **Change Margin** → 選 **Segregated Portfolio Margin**。

**Q：covered_call 子帳需要 USDC 嗎？**  
A：**不需要。** 僅劃入約定數量的 **BTC 或 ETH 現貨**即可；現貨同時作備兌與保證金。

---

**產生 PDF**（在專案根目錄執行；使用 reportlab，不需 Pandoc／LaTeX）：

```bash
python3 scripts/generate_investor_onboarding_pdf.py
```

輸出檔：`output/pdf/Investor_Onboarding_zh-TW.pdf`
