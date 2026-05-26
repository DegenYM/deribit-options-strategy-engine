# Live bot 常駐（macOS launchd）

用 launchd 在背景跑 `run_live_profiles.py`，讓每位投資人的實單 bot：

- 登入 macOS 後自動啟動
- 關 Terminal 仍繼續跑
- 監督腳本 crash 後由 launchd 重拉
- 單一子帳 crash 由 `--restart-failed` 重啟（不連坐其他子帳）

## 架構

```
launchd（每位投資人一個 LaunchAgent）
  └── run_live_profiles.py --investor <id> --restart-failed
        ├── accounts.toml 內 enabled + live_enabled 子帳 bot
        └── ...
```

與 **frontend**、**cloudflared** 的 launchd 可並存；`Label` 不可重複（live：`com.deribit.live.<id>`；frontend：`com.deribit.frontend.<id>`）。Dashboard 常駐範本見 [`cloudflare-tunnel-investor.md`](cloudflare-tunnel-investor.md) 第六節。

## 範本位置

| 用途 | 範本 |
|------|------|
| Live bot | [`config/launchd/com.deribit.live.plist.template`](../config/launchd/com.deribit.live.plist.template) |
| Frontend | [`config/launchd/com.deribit.frontend.plist.template`](../config/launchd/com.deribit.frontend.plist.template) |

**建議**：以 `./bot investor init` 建立投資人後，直接使用已產生的 plist（路徑含實際 `repo_root` / `python_bin`，無需手動 `sed`）：

```bash
cp "$REPO_ROOT/config/platform/generated/launchd/com.deribit.live.${INVESTOR}.plist" \
   ~/Library/LaunchAgents/
```

或一鍵：`./bot investor live start`（讀 `config/platform/registry.toml` 的 `live_enabled`）。

詳見 [`operator-onboarding-zh-TW.md`](operator-onboarding-zh-TW.md) 第四節。

手動從範本產生時，佔位符如下：

| 佔位符 | 說明 | 範例 |
|--------|------|------|
| `__LABEL__` | LaunchAgent label | `com.deribit.live.jack` |
| `__REPO_ROOT__` | 本 repo 絕對路徑 | `/Users/youming/Desktop/deribit-options-strategy-engine` |
| `__PYTHON_BIN__` | 已裝依賴的 Python | `/Users/youming/miniforge3/envs/crypto/bin/python` |
| `__INVESTOR_ID__` | 投資人 id（小寫） | `jack` |

確認 Python：

```bash
__PYTHON_BIN__ --version
__PYTHON_BIN__ -c "import deribit_demo; print('ok')"
```

## 安裝（以 jack 為例）

### 1. 先手動跑通

```bash
cd __REPO_ROOT__
__PYTHON_BIN__ scripts/run_live_profiles.py --investor jack --restart-failed
```

看到 `started .env....` 且 `logs/live/jack/*.log` 有寫入後，Ctrl+C 停掉。

### 2. 產生 plist 並放到 LaunchAgents

```bash
REPO_ROOT="/Users/youming/Desktop/deribit-options-strategy-engine"
PYTHON_BIN="/Users/youming/miniforge3/envs/crypto/bin/python"
INVESTOR="jack"   # 或 youming、an、pat

mkdir -p "$REPO_ROOT/logs/live/$INVESTOR"
mkdir -p ~/Library/LaunchAgents

sed \
  -e "s|__LABEL__|com.deribit.live.${INVESTOR}|g" \
  -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
  -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
  -e "s|__INVESTOR_ID__|$INVESTOR|g" \
  "$REPO_ROOT/config/launchd/com.deribit.live.plist.template" \
  > ~/Library/LaunchAgents/com.deribit.live.${INVESTOR}.plist
```

### 3. 載入

macOS Ventura 以前（或仍支援 `load` 時）：

```bash
launchctl load ~/Library/LaunchAgents/com.deribit.live.jack.plist
```

macOS Ventura+ 建議：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.deribit.live.jack.plist
```

若出現 `Bootstrap failed: 5: Input/output error`，**多半代表該 Label 已載入過**，不是 plist 壞掉。先查：

```bash
launchctl list | grep deribit.live
launchctl print gui/$(id -u)/com.deribit.live.jack
```

已有 `state = running` 就不必再 bootstrap。要重載請先卸載再載入：

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.deribit.live.jack.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.deribit.live.jack.plist
```

只改 code、不重裝 plist 時，重啟即可：

```bash
launchctl kickstart -k gui/$(id -u)/com.deribit.live.jack
```

### 4. 確認

```bash
launchctl list | grep deribit.live
tail -f __REPO_ROOT__/logs/live/jack/supervisor.log
```

預期看到各子帳 `started ... pid=...`。

## 一次安裝三位投資人

```bash
REPO_ROOT="/Users/youming/Desktop/deribit-options-strategy-engine"
PYTHON_BIN="/Users/youming/miniforge3/envs/crypto/bin/python"

for INVESTOR in jack youming an; do
  mkdir -p "$REPO_ROOT/logs/live/$INVESTOR"
  sed \
    -e "s|__LABEL__|com.deribit.live.${INVESTOR}|g" \
    -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
    -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
    -e "s|__INVESTOR_ID__|$INVESTOR|g" \
    "$REPO_ROOT/config/launchd/com.deribit.live.plist.template" \
    > ~/Library/LaunchAgents/com.deribit.live.${INVESTOR}.plist
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.deribit.live.${INVESTOR}.plist 2>/dev/null \
    || launchctl load ~/Library/LaunchAgents/com.deribit.live.${INVESTOR}.plist
done
```

## 日常操作

| 動作 | 指令 |
|------|------|
| 查狀態 | `launchctl list \| grep deribit.live` |
| **一鍵啟停全部** | `./bot investor live start` / `stop` / `restart` / `status`（或 `./scripts/live_launchd_all.sh start`） |
| 看監督 log | `tail -f logs/live/<id>/supervisor.log` |
| 看子帳 log | `tail -f logs/live/<id>/<slug>.log` |
| 重啟（改 code 後） | `launchctl kickstart -k gui/$(id -u)/com.deribit.live.jack` |
| 停止 | `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.deribit.live.jack.plist` |
| 停止（舊版） | `launchctl unload ~/Library/LaunchAgents/com.deribit.live.jack.plist` |

## Log 路徑

| 檔案 | 內容 |
|------|------|
| `logs/live/<id>/supervisor.log` | 監督腳本 stdout（started / exited / restarted） |
| `logs/live/<id>/supervisor.err.log` | 監督腳本 stderr / traceback |
| `logs/live/<id>/<slug>.log` | 各子帳 bot 的 cycle log |
| `.state/investors/<id>/<slug>.heartbeat.json` | live cycle 心跳（`ts_ms`、`regime`、`last_error`） |

## Heartbeat watchdog

Live bot 每完成一個 cycle（或 API 退避重試時）會更新 heartbeat。外部腳本可偵測 bot 卡住：

```bash
# 預設 10 分鐘無更新 → Telegram 告警（需 defaults.env 內 Telegram 設定）
python scripts/check_live_heartbeat.py

# 乾跑（只印 STALE、不發 TG）
python scripts/check_live_heartbeat.py --dry-run
```

建議用 **cron** 或 **launchd StartInterval** 每 5 分鐘跑一次。環境變數 `LIVE_HEARTBEAT_STALE_SECONDS=600` 可調門檻。故障處理見 [`docs/runbooks/README-zh-TW.md`](runbooks/README-zh-TW.md)。

## 注意事項

1. **Error 5 (Input/output error)**：`bootstrap` 或 `load` 時若 Label 已存在，macOS 常只報此錯而非「already loaded」。用 `launchctl list | grep deribit.live` 確認；已在跑就不要再 load。
2. **不要重複跑**：launchd 已載入某 investor 時，勿再在 Terminal 手動跑同一 `--investor` 的 live，否則 API 請求加倍、易 429。
3. **Mac 睡眠**：深度睡眠會中斷連線；Mac mini 長期跑請關閉睡眠（見 [`cloudflare-tunnel-investor.md`](cloudflare-tunnel-investor.md)）。
4. **憑證**：`.env` 含 API 金鑰；plist 本身不含 secret，但仍應限制 LaunchAgents 目錄權限。
5. **新增投資人**：執行 `./bot investor init <id>` 產生 plist，或從 [`com.deribit.live.plist.template`](../config/launchd/com.deribit.live.plist.template) 手動替換佔位符。
