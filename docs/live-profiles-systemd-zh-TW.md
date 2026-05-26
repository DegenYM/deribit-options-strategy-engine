# Live bot / Dashboard 常駐（Linux systemd）

在 Linux VPS 上用 systemd 背景跑 `run_live_profiles.py` 與 dashboard frontend，行為對標 macOS launchd 範本。

## 架構

```
systemd（每位投資人各一 live + 一 frontend unit）
  ├── com.deribit.live.<id>.service
  │     └── run_live_profiles.py --investor <id> --restart-failed
  └── com.deribit.frontend.<id>.service
        └── bot --investor <id> frontend --port <port>
```

macOS 對照文件：[`live-profiles-launchd-zh-TW.md`](live-profiles-launchd-zh-TW.md)。

## 範本位置

| 用途 | 範本 |
|------|------|
| Live bot | [`config/systemd/com.deribit.live.service.template`](../config/systemd/com.deribit.live.service.template) |
| Frontend | [`config/systemd/com.deribit.frontend.service.template`](../config/systemd/com.deribit.frontend.service.template) |

**建議**：以 `./bot investor init` 或 `./bot investor render-systemd <id>` 產生已填入路徑的 unit：

```text
config/platform/generated/systemd/com.deribit.live.<id>.service
config/platform/generated/systemd/com.deribit.frontend.<id>.service
```

手動從範本產生時，佔位符如下：

| 佔位符 | 說明 | 範例 |
|--------|------|------|
| `__REPO_ROOT__` | 本 repo 絕對路徑 | `/opt/deribit-options-strategy-engine` |
| `__PYTHON_BIN__` | 已裝依賴的 Python | `/opt/venv/bin/python` |
| `__INVESTOR_ID__` | 投資人 id（小寫） | `jack` |
| `__FRONTEND_PORT__` | dashboard 埠 | `8766`（須與 `registry.toml` 一致） |

確認 Python：

```bash
__PYTHON_BIN__ --version
__PYTHON_BIN__ -c "import deribit_engine; print('ok')"
```

## 安裝（以 jack 為例）

### 1. 先手動跑通

```bash
cd __REPO_ROOT__
__PYTHON_BIN__ scripts/run_live_profiles.py --investor jack --restart-failed
```

另開 terminal 測 frontend：

```bash
__PYTHON_BIN__ bot --investor jack frontend --port 8766
```

確認 `logs/live/jack/`、`logs/frontend/jack/` 有寫入後 Ctrl+C 停掉。

### 2. 產生 unit 檔

```bash
cd __REPO_ROOT__
./bot investor render-systemd jack
```

或 `./bot investor init jack ...` 時會一併產生 launchd + systemd 檔。

### 3. 安裝到 systemd（system 層）

```bash
REPO_ROOT="/opt/deribit-options-strategy-engine"
INVESTOR="jack"

sudo cp "$REPO_ROOT/config/platform/generated/systemd/com.deribit.live.${INVESTOR}.service" \
  /etc/systemd/system/
sudo cp "$REPO_ROOT/config/platform/generated/systemd/com.deribit.frontend.${INVESTOR}.service" \
  /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now com.deribit.live.${INVESTOR}.service
sudo systemctl enable --now com.deribit.frontend.${INVESTOR}.service
```

**權限**：unit 預設以 root 執行。若 repo 屬於一般使用者 `trader`，建議在 unit 加 `User=trader` / `Group=trader`（可手改 generated 檔或建立 drop-in）。

### 3b. 使用者層 unit（無 sudo 時）

```bash
mkdir -p ~/.config/systemd/user
cp "$REPO_ROOT/config/platform/generated/systemd/"*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now com.deribit.live.jack.service
systemctl --user enable --now com.deribit.frontend.jack.service
loginctl enable-linger "$USER"   # 登出後仍常駐
```

### 4. 確認

```bash
systemctl status com.deribit.live.jack.service
systemctl status com.deribit.frontend.jack.service
journalctl -u com.deribit.live.jack.service -n 50 --no-pager
tail -f logs/live/jack/supervisor.log
curl -sS "http://127.0.0.1:8766/api/health"
```

重啟（改 code 後）：

```bash
sudo systemctl restart com.deribit.live.jack.service
sudo systemctl restart com.deribit.frontend.jack.service
```

停機：

```bash
sudo systemctl stop com.deribit.live.jack.service com.deribit.frontend.jack.service
```

## Heartbeat watchdog（cron / timer）

Live bot 每 cycle 寫入 `.state/investors/<id>/<slug>.heartbeat.json`。Linux 可用 cron：

```cron
*/5 * * * * cd /opt/deribit-options-strategy-engine && /opt/venv/bin/python scripts/check_live_heartbeat.py
```

詳見 [`live-profiles-launchd-zh-TW.md`](live-profiles-launchd-zh-TW.md) 的 watchdog 一節與 [`runbooks/README-zh-TW.md`](runbooks/README-zh-TW.md)。

## 與 launchd 對照

| launchd | systemd |
|---------|---------|
| `KeepAlive` | `Restart=always` |
| `RunAtLoad` | `systemctl enable --now` |
| `StandardOutPath` | `StandardOutput=append:...` |
| `launchctl kickstart -k` | `systemctl restart` |
| `~/Library/LaunchAgents/` | `/etc/systemd/system/` 或 `~/.config/systemd/user/` |

## 相關文件

- 管理方 onboarding：[`operator-onboarding-zh-TW.md`](operator-onboarding-zh-TW.md)
- 目錄規範：[`repo-layout-zh-TW.md`](repo-layout-zh-TW.md)
- Cloudflare Tunnel：[`cloudflare-tunnel-investor.md`](cloudflare-tunnel-investor.md)
