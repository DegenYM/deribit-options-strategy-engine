# Docker Compose 部署

用 Docker Compose 在同一台機器上啟動一位投資人的 live bot 與 dashboard frontend。這是 launchd / systemd 以外的備援部署方式，適合臨時搬機、VPS 驗證或需要固定 Python 版本的環境。

## 架構

```
docker compose
  ├── live
  │     └── python scripts/run_live_profiles.py --investor <id> --restart-failed
  └── frontend
        └── python -m deribit_engine --investor <id> frontend --host 0.0.0.0 --port <registry port>
```

`frontend` 啟動時會讀 `config/platform/registry.toml`，用 `INVESTOR` 找出該投資人的 `frontend_port`。Compose 目前映射 `8765-8799`，需與 registry 內分配的 dashboard port 範圍一致。

## 前置需求

- Docker Engine / Docker Desktop 已安裝。
- `config/platform/registry.toml` 已存在，且目標投資人有 `frontend_enabled = true` 與 `frontend_port`。
- `config/investors/<id>/accounts.toml` 與 `accounts/.env.*` 已填妥；live service 只會啟動 enabled + live_enabled 且有 API 金鑰的子帳。
- Telegram 與共用環境變數仍放在 `config/shared/.env.defaults`；投資人與子帳金鑰仍放在 `config/investors/<id>/accounts/.env.*`。

首次啟動前建議先建立 runtime 目錄，避免 Docker 以 root 建出 bind mount：

```bash
mkdir -p .state logs data
```

## 啟動

以 `jack` 為例：

```bash
INVESTOR=jack docker compose up --build
```

背景啟動：

```bash
INVESTOR=jack docker compose up -d --build
```

確認狀態：

```bash
docker compose ps
docker compose logs -f live
docker compose logs -f frontend
curl -sS "http://127.0.0.1:8766/api/health"
```

其中 `8766` 請換成 `config/platform/registry.toml` 內該投資人的 `frontend_port`。

## 停止與重啟

```bash
docker compose down
INVESTOR=jack docker compose up -d
docker compose restart frontend
```

更新程式碼或 requirements 後重建 image：

```bash
INVESTOR=jack docker compose up -d --build
```

## Volume

| Host 路徑 | Container 路徑 | 說明 |
|----------|----------------|------|
| `./config` | `/app/config` | 唯讀掛載；包含 registry、investor env、shared defaults |
| `./.state` | `/app/.state` | heartbeat、策略 state |
| `./logs` | `/app/logs` | live / frontend logs |
| `./data` | `/app/data` | SQLite、cache、匯入資料 |

## 限制

- Docker 內不會使用 macOS launchd 或 Linux systemd；重啟由 Compose `restart: unless-stopped` 與 `run_live_profiles.py --restart-failed` 處理。
- Compose 不會自動管理 Cloudflare Tunnel；若 dashboard 對外開放，仍需在 host 另外啟動 cloudflared 或用既有 tunnel 服務。
- API 金鑰與 Telegram 設定不會被寫入 image；仍由 bind-mounted `config/` 讀取，請勿把 `.env*` 加進版本控制。
- `Dockerfile` 預設建立 non-root 使用者。Linux bind mount 若遇到權限問題，可用 build args 對齊 host UID/GID：

```bash
docker compose build --build-arg APP_UID="$(id -u)" --build-arg APP_GID="$(id -g)"
INVESTOR=jack docker compose up -d
```

## 語法檢查

```bash
INVESTOR=jack docker compose config
```

此指令只檢查 Compose 展開後的設定，不會 build image。
