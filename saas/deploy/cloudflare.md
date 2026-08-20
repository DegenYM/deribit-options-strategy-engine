# Cloudflare

Use a single hostname (for example `app.example.com`) pointing at the VPS or a Cloudflare Tunnel to `http://127.0.0.1:8080`.

Do **not** recreate the old one-Access-app-per-investor model. Application login (magic link) is the identity layer; Cloudflare is the edge.

Suggested:

1. DNS A/AAAA or CNAME to the EU VPS
2. SSL Full (strict) if origin has a cert, or Tunnel
3. Optional WAF / bot fight on `/api/auth` and `/api/billing/stripe-webhook`
4. Restrict `/api/admin` by Cloudflare Access email if you want a second lock
