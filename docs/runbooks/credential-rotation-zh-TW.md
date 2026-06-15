# API 憑證輪替

## 何時做

- Deribit 後台 rotate key
- Token 可能外洩（含 Telegram token — 見 [telegram-alerts-zh-TW.md](../telegram-alerts-zh-TW.md)）
- 子帳權限變更

## 步驟（Deribit API）

| 步驟 | 動作 |
|------|------|
| 1 | 在 Deribit 建立新 key（先不要刪舊 key） |
| 2 | 更新 `config/investors/<id>/accounts/.env.<slug>` 的 `DERIBIT_CLIENT_ID` / `DERIBIT_CLIENT_SECRET` |
| 3 | `./bot ping --env-file ...` 確認 private API |
| 4 | `./bot investor live restart --investor <id>`（或等監督腳本重啟） |
| 5 | 確認 heartbeat / dashboard 正常後，在 Deribit 停用舊 key |

## Telegram token

1. BotFather `/revoke`
2. 更新 `config/shared/.env.defaults` 的 `TELEGRAM_BOT_TOKEN`
3. `./bot telegram-test`

## 注意

- **勿 commit** 含 secret 的 env
- Fee 專戶、多 slug 需逐一更新
- 輪替期間避免同時跑 dry-run 與 live 用不同 key 寫同一 state
