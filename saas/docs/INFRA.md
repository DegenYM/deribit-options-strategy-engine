# 10 人內測基礎設施

目標：歐盟一台 **8 vCPU / 16 GB**（Hetzner CX43 或同等），月費約 USD 15–40。
Mac mini 只當 staging。

## 組成

- Docker Compose：`api`、`postgres`、`supervisor`、`marketd`（web 由 api 靜態提供）
- Cloudflare：DNS + 可選 Tunnel／WAF；單一 hostname，不要一人一 Access app
- 每日備份：`deploy/backup.sh` → 本地 tar + 可選 R2/S3
- 機密：`CC_SAAS_SECRET_KEY`、`CC_SAAS_CREDENTIAL_KEY`、Stripe、Postgres 密碼

## 產品閘門

- `WAITLIST_ONLY=true`：人工核准前 10 人
- `DRY_RUN_MIN_DAYS=7`：Trader／Pro／Desk 實單前強制模擬
- Scout 永不 `desired=live`

## 軟體月費量級（10 人）

- VPS 15–40
- Cloudflare 0–20
- 備份 5
- Email／Sentry 可先用免費方案
- Stripe 抽成約 2.9% + 0.30／筆

合計基礎設施約 **USD 40–80／月**（不含保險與你的工時）。

## 上線檢查

1. Postgres `DATABASE_URL`
2. 憑證加密金鑰不可用預設 `dev-secret-change-me`
3. Deribit IP allowlist 指向這台出口 IP
4. `legal/` 連結掛在登入頁
5. 備份腳本進 cron
