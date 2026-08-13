# Viewing the workstation from outside the VPS

The dashboard binds to `127.0.0.1:8788` and the documented access method is an
SSH tunnel. This page covers the other case: reaching it from a browser that is
not tunnelled into the machine — for example the workstation hosted on Vercel.

## First: do you actually need this?

The SSH tunnel already gives you the complete workstation. It is the same
bundle, the same screens, the same live websocket:

```sh
ssh -L 8788:127.0.0.1:8788 root@<your-vps-ip>
# then open http://127.0.0.1:8788
```

Hosting the UI elsewhere buys exactly one thing: access without a tunnel. It
costs a publicly reachable dashboard. If the tunnel is acceptable, stop here —
nothing in this document is required, and `VITE_API_ORIGIN` can stay unset.

## How the pieces actually connect

Vercel and the VPS never talk to each other. Vercel serves the page; the
browser then calls the VPS directly.

```
Vercel  ──(html + js)──►  browser  ──(REST + websocket)──►  your VPS
                                     ▲
                                     this leg is what has to work
```

Consequences worth internalising:

- Vercel needs no credentials for the VPS and never contacts it.
- The VPS must be reachable **from the browser**, over the public internet.
- It must be **HTTPS**. A page served over https cannot call an http backend;
  browsers block it as mixed content. This is why a bare IP is not enough — you
  need a hostname you can get a certificate for.

## Step 1 — Give the dashboard a reachable HTTPS hostname

Pick one. All three terminate TLS and proxy websockets correctly.

**If you are not sure which, use Option C (Tailscale).** It needs no domain and
exposes nothing to the internet, and it makes steps 2 and 3 unnecessary.

### Option A — Caddy on the VPS (simplest if you own a domain)

Point a DNS `A` record (say `dash.example.com`) at your Hostinger VPS IP, then:

```sh
apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```
dash.example.com {
    reverse_proxy 127.0.0.1:8788
}
```

```sh
systemctl reload caddy
```

Caddy obtains and renews a Let's Encrypt certificate automatically, and proxies
websockets without extra configuration. Ports 80 and 443 must be open in the
Hostinger firewall.

Leave the dashboard bound to `127.0.0.1` — Caddy reaches it over loopback. Do
not change the systemd unit to `0.0.0.0`; that would expose the unencrypted port
directly.

### Option B — Cloudflare Tunnel (no open inbound ports)

Requires a domain on Cloudflare. The VPS makes an outbound connection, so
nothing inbound needs opening — a good fit if you would rather not expose ports
at all.

```sh
cloudflared tunnel login
cloudflared tunnel create alpha-spy
cloudflared tunnel route dns alpha-spy dash.example.com
cloudflared tunnel run --url http://127.0.0.1:8788 alpha-spy
```

Then install it as a service so it survives reboots (`cloudflared service
install`).

### Option C — Tailscale (no domain, nothing public, fewest steps)

The easiest route, and the one to pick if the others look like work. It needs no
domain, no certificate, and no open firewall ports, and the dashboard stays
invisible to the internet — only your own devices can reach it.

On the VPS:

```sh
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up          # prints a link; open it and sign in
tailscale serve --bg 8788
tailscale serve status # prints your https://<machine>.<tailnet>.ts.net URL
```

Then install the Tailscale app on your laptop or phone and sign in with the same
account. Open the URL it printed. That is the whole thing.

`tailscale serve` proxies to `127.0.0.1:8788` and terminates TLS itself, so the
dashboard keeps its loopback binding and needs no configuration change at all.

**This replaces the rest of this document.** Reached this way the workstation is
same-origin, so there is no CORS to configure, no `VITE_API_ORIGIN` to set, and
no reason to route through Vercel — steps 2 and 3 below do not apply.

If you specifically want the Vercel URL to work, `tailscale funnel 8788` makes
the same hostname public and steps 2 and 3 then apply. Be clear about the
trade: it puts the dashboard on the internet to gain a different bookmark.

## Step 2 — Allow the Vercel origin on the VPS

Cross-origin requests are refused unless the origin is listed. In
`/etc/alpha-spy/config.yaml`:

```yaml
dashboard:
  allowed_origins:
    - https://alpha-spy.vercel.app
```

```sh
systemctl restart alpha-spy-dashboard
```

Vercel gives every preview deployment its own hostname, so add those too if you
want previews to work. An empty list means no cross-origin access at all, which
is the correct default.

## Step 3 — Point the Vercel build at the VPS

In the Vercel project: **Settings → Environment Variables**

```
VITE_API_ORIGIN=https://dash.example.com
```

It is read at build time, so **redeploy** afterwards. Setting it without
redeploying changes nothing.

## Step 4 — Verify, in this order

Each check isolates one leg, so a failure tells you which step is wrong.

```sh
# 1. The dashboard is up locally on the VPS
curl -fsS http://127.0.0.1:8788/api/v1/health

# 2. TLS and proxying work from outside
curl -fsS https://dash.example.com/api/v1/health

# 3. The Vercel origin is allowed (expect an access-control-allow-origin header)
curl -sI -H 'Origin: https://alpha-spy.vercel.app' \
     https://dash.example.com/api/v1/auth/mode | grep -i access-control

# 4. The deployed bundle knows the origin (expect your hostname, not nothing)
curl -s https://alpha-spy.vercel.app/assets/index-*.js | grep -o 'https://dash[^"]*' | head -1
```

Then open the Vercel URL. It should ask for the dashboard token and, once
entered, show `LIVE STREAM` in the sidebar with an advancing frame counter.

## What is protecting it now

Once step 1 is done the dashboard is on the public internet. What stands in
front of it:

- **The view token.** Keep `require_view_token: true` and use a strong,
  distinct value from the admin token. This is the main control.
- **The admin token**, separately required for `PAUSE`, `RESUME`, `RELOAD` and
  `FLATTEN`. The workstation cannot place, modify or cancel a broker order under
  any token — it only queues those four commands for the engine.
- **CORS** is exact-origin with `allow_credentials=False`, so a hostile page
  cannot ride an ambient session even if it learns the hostname.
- **Production money stays locked** regardless: real-money execution requires
  the production sentinel plus an evidence-bound approval artifact on disk.
  Nothing reachable over HTTP can enable it.

Worth adding if you want more than a token in front of it: an IP allowlist in
Caddy (`@allowed remote_ip <your-ip>`), or Cloudflare Access on the tunnel.
