# Telegram 告警設定

Live bot 在關鍵事件時可推送 Telegram 訊息（hard derisk、hard stop、panic close、程序 crash、API 連續失敗等）。

## 1. 建立 Bot

1. 在 Telegram 找 [@BotFather](https://t.me/BotFather)
2. 傳 `/newbot`，依指示取名
3. 複製 Bot **Token**（形如 `123456789:AAH...`）

## 2. 取得 Chat ID

**個人聊天（最簡單）**

1. 先對你的 bot 傳任意訊息（例如 `hi`）
2. 瀏覽器開啟（把 `<TOKEN>` 換成你的 token）：
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. 在 JSON 找 `"chat":{"id":123456789` — 這就是 **Chat ID**

也可用 [@userinfobot](https://t.me/userinfobot) 查自己的 id。

**群組**：把 bot 加進群，在群裡 @bot 發訊息，同樣用 `getUpdates` 看 `"chat":{"id":-100...`。

## 3. 寫入設定

建議放在 **`config/shared/.env.defaults`**（gitignore，全投資人共用）：

```dotenv
TELEGRAM_ALERTS_ENABLED=true
TELEGRAM_BOT_TOKEN=你的token
TELEGRAM_CHAT_ID=你的chat_id
TELEGRAM_ALERT_COOLDOWN_SECONDS=300
```

範本：[`config/shared/.env.defaults.example`](../config/shared/.env.defaults.example)（Telegram 區塊；實際金鑰寫入 gitignore 的 `.env.defaults`）。

## 4. 測試

```bash
./bot telegram-test
```

成功後 Telegram 會收到「Deribit bot test alert」。

## 5. 會收到哪些通知

| 事件 | 來源 |
|------|------|
| Hard derisk | live `manage` |
| Hard stop / soft stop 平倉 | live `_close_group` |
| Panic close | `./bot panic-close --live` |
| Bot run loop crash | live `run` 未捕捉例外 |
| Deribit API 連續失敗（≥5 次） | live `run` 429/暫時性錯誤 |
| Live 子程序異常退出 | `run_live_profiles.py --restart-failed` |
| 子程序自動重啟 | 同上 |

同一 `event_key` 在 `TELEGRAM_ALERT_COOLDOWN_SECONDS` 內只會推一次，避免洗版。

## 6. 安全提醒

- **不要**把 token 提交到 git
- `config/shared/.env.defaults` 已在 `.gitignore`
- 若 token 外洩，到 BotFather `/revoke` 重發
