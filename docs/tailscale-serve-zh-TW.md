# 以 Tailscale Serve 從手機看 Dashboard

本機 dashboard 只綁 **`127.0.0.1`**。不要為了遠端把 `--host` 改成 `0.0.0.0`。  
用 **Tailscale Serve** 把 loopback HTTP 轉成 tailnet 上的 HTTPS；手機裝同一帳號的 Tailscale 即可連。

投資人對外固定網址仍走 **Cloudflare Tunnel + Access**（`*.debopt.com`）。Serve 是給你自己（手機 / 另一台 Mac）用的私有入口。

## 何時用哪一條路

| 需求 | 做法 |
|------|------|
| 自己用手機、另一台電腦，裝置都在同一 Tailscale 帳號 | **Serve**（本文件；已可重跑 `./scripts/tailscale_serve.sh start`） |
| 投資人書籤、不必裝 Tailscale | 既有 **Cloudflare Named Tunnel** |
| 任何瀏覽器、沒裝 Tailscale 也要開本機頁 | `tailscale funnel`（公開上網；**預設不要開**，等於繞過 Access） |

`./bot admin` 也可走 Serve（`:8750`），只給同一 tailnet。交易鈕在這一頁，**不要**對 admin 開 Funnel。Serve 反代後後端會看成 `127.0.0.1`，所以安全邊界是「誰在你的 Tailscale」，不是 loopback 檢查。

## 手機怎麼開

1. iPhone 裝 [Tailscale](https://apps.apple.com/app/tailscale/id1470499037)，登入與 Mac mini **同一帳號**（目前 `iphone-15` / `koyomi8126@` 已在 tailnet）。
2. 確認 Tailscale 是連線中（蜂巢或外面 Wi‑Fi 都可以）。
3. Safari 開下面網址（根路徑是 youming 全功能頁）：

| 投資人 | 網址 |
|--------|------|
| youming | `https://minim1.tailc25ed2.ts.net/` 或 `:8765`（admin iframe 用這埠） |
| jack | `https://minim1.tailc25ed2.ts.net:8766/` |
| pat | `https://minim1.tailc25ed2.ts.net:8767/` |
| an | `https://minim1.tailc25ed2.ts.net:8768/` |
| ma | `https://minim1.tailc25ed2.ts.net:8770/` |
| eugene | `https://minim1.tailc25ed2.ts.net:8771/` |
| **admin** | `https://minim1.tailc25ed2.ts.net:8750/` |

投資人摘要頁在同一主機加路徑，例如 `https://minim1.tailc25ed2.ts.net/investor.html`。

別的帳號裝置（例如 `774jynvt86@` 的 iPhone）進不來，除非你在 Tailscale admin 分享這台機器或把裝置加進同一個 tailnet。

## 另一台 Mac 顯示「拒絕連線」

這台 Mac mini 的 Tailscale Serve **IPv6 不聽埠**（`fd7a:…:443` / `:8750` 會被拒）。Safari / Chrome 常先走 IPv6，就會變成「拒絕連線」，即使 IPv4 其實是通的。

在 **15m4**（或任何連不上的 Mac）做這一步，強制只用 IPv4，然後重開瀏覽器：

```bash
sudo /bin/sh -c 'grep -q "minim1.tailc25ed2.ts.net" /etc/hosts || echo "100.65.76.83 minim1.tailc25ed2.ts.net" >> /etc/hosts'
```

然後開（必須 `https://`，admin 要帶埠）：

- youming：`https://minim1.tailc25ed2.ts.net/`
- admin：`https://minim1.tailc25ed2.ts.net:8750/`

另外確認：

1. 選單列 Tailscale 是 Connected，且「Use Tailscale DNS」有開。
2. 不要用 Chrome 的 Secure DNS / 安全 DNS（會繞過 Tailscale，打到不能路由的 `100.x`）。優先用 Safari。
3. 不要只貼主機名、不要用 `http://` 開 admin（admin 只有 HTTPS `:8750`）。
4. 本機 admin 必須在跑：`./bot investor admin status`。

## 重套／關掉

frontend 必須先在本機聽 `127.0.0.1:<port>`（`./bot investor frontend start`）。  
admin 必須在聽 `127.0.0.1:8750`（`./bot investor admin start`）；沒起來時另一台電腦會看到 Serve 502。

```bash
./scripts/tailscale_serve.sh start           # 依 registry.toml 掛上 Serve（根網址 = youming）
./scripts/tailscale_serve.sh start jack      # 根網址改掛 jack
./scripts/tailscale_serve.sh status
./scripts/tailscale_serve.sh stop            # tailscale serve reset
```

`tailscale serve --bg` 會寫進 Tailscale 設定；Mac 重開、`tailscale down` / `up` 後會自動再掛。frontend 沒起來時 Serve 會 502。

手動等效指令（youming 當根路徑）：

```bash
tailscale serve --bg --yes 8765
tailscale serve --bg --yes --https=8766 8766
# …其餘 frontend_port 同理
```

## 不要做的事

- 不要 `./bot frontend --host 0.0.0.0`：會把埠暴露到家用 LAN。
- 不要 `tailscale funnel --bg 8765` 或 `8750`：ops / admin 會出現在公網。
