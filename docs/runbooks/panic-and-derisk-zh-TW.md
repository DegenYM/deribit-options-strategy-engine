# Hard derisk / Panic close

## 症狀

- Telegram：`Hard derisk`、`Hard stop`、`Soft stop 平倉`、`Panic close`
- Dashboard `hard_derisk=true` 或持倉被大量平掉
- `halt_new_entries` 長時間為 true

## 立即檢查

1. 看 regime：`status` 輸出中 `portfolio.regime` 是否為 `crisis` / `stress`
2. 看 manage log：哪個 group 被 close、原因為何
3. 確認是否為預期風控（非 bug）

## Panic close（人工）

```bash
./bot panic-close --env-file config/investors/<id>/accounts/.env.<slug> --live
```

**僅在**確認需全部平倉時使用；會送 Telegram 通知。

## Hard derisk 後

| 步驟 | 動作 |
|------|------|
| 1 | 確認 Deribit 持倉已清空或符合預期 |
| 2 | 看 `cooling_down` / `halt_new_entries` 是否仍擋 entry |
| 3 | 等 regime 回 `normal` 或依策略參數調整後再開 entry |
| 4 | 必要時 `./bot investor live restart` |

## 誤觸發

若判定為錯誤 derisk（例如 index feed 短暫失效）：

1. 停 live
2. 備份 state
3. 修正 env（例如放寬門檻 — 需審慎）
4. 重跑 dry-run `manage` + `scan` 確認

## 升級

連續 panic 或 derisk 循環 → 停 live、保留 log + state + heartbeat，再排查 regime 資料來源。
