# Running Altius on a server

This page is for putting the self-hosted edition on a VPS or home server that other devices
reach over the internet: HTTPS through a reverse proxy, a firewall, and a checklist for the
sign-in. For installing it on your own computer, see [Installation](install.md). For what the
app itself does to protect you, see [Security](security.md).

If only you need to reach it, the simplest safe setup is not to expose it at all: keep it on
`127.0.0.1` and reach it over a private network such as Tailscale or WireGuard, or an SSH tunnel
(`ssh -L 8321:127.0.0.1:8321 you@server`, then open `http://localhost:8321`).

## How the pieces fit

```
your browser ──HTTPS (443)──> reverse proxy (Caddy or nginx) ──HTTP──> Altius on 127.0.0.1:8321
```

- Altius is only reachable on `127.0.0.1` (the Docker setup publishes its port there), so
  nothing on the internet reaches it except through the proxy.
- The proxy holds the HTTPS certificate and passes requests on.
- **Always set `TRADEALERTS_PASSWORD` behind a proxy**, even though Altius on `127.0.0.1`
  would start without one. A copy with no password refuses any request that arrives through a
  proxy (it can tell from the `X-Forwarded-For` / `X-Real-IP` / `Forwarded` headers proxies
  add) and says why. Only pass `--no-auth` if the proxy itself checks who signs in (for
  example Caddy's `basic_auth`, or an identity-aware proxy).

## Option A: Docker (recommended on a VPS)

1. Copy the release to the server and run `./setup.sh --docker`. It writes a random
   `TRADEALERTS_PASSWORD` to `.env` next to `docker-compose.yml` (readable only by you) and
   starts the container.
   Or copy `.env.example` to `.env`, set the password, and run `docker compose up -d --build`.
2. Keep the `ports:` line as `"127.0.0.1:8321:8321"`. Docker opens published ports in the
   firewall itself, ignoring ufw, so `"8321:8321"` would put plain HTTP on the internet.
3. Inside the container every visitor seems to come from Docker's own network address, so all
   sign-in attempts share one slow-down count. To count each visitor separately, trust the
   proxy's `X-Forwarded-For` from Docker's network by adding this line to `.env` and restarting:

   ```
   FORWARDED_ALLOW_IPS=172.16.0.0/12
   ```

   (Use your Docker network's range if you changed it: `docker network inspect bridge`.)

## Option B: without Docker

Run it as the background service from `setup.sh`, or by hand:

```
TRADEALERTS_PASSWORD='a long random password' tradealerts serve --host 127.0.0.1 --port 8321
```

Leave `--host` at `127.0.0.1`. On `127.0.0.1`, Altius only answers requests addressed to
`127.0.0.1`, `localhost` or `[::1]` (this blocks DNS-rebinding attacks), so the proxy must
pass that address on as the `Host` header: Caddy needs one extra line (below); nginx already
does it unless you set `Host` yourself. All visitors share one sign-in slow-down count in this
setup, which is safe but means someone guessing passwords can make you wait too.

## HTTPS with Caddy

Caddy gets and renews a Let's Encrypt certificate by itself. Point the domain's DNS at the
server, open ports 80 and 443, and add to `/etc/caddy/Caddyfile`:

```
alerts.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8321 {
        # Without Docker (Option B) only: Altius on 127.0.0.1 checks the Host header.
        # header_up Host {upstream_hostport}
    }
    request_body {
        max_size 210MB   # backups and CSV uploads
    }
    header Strict-Transport-Security "max-age=31536000"
}
```

Then `sudo systemctl reload caddy`. Caddy replaces any `X-Forwarded-For` a visitor sends with
the address it really saw, so the sign-in slow-down can't be dodged by faking it.

## HTTPS with nginx

Get a certificate with certbot (`sudo certbot --nginx -d alerts.example.com`), then:

```
server {
    listen 443 ssl;
    server_name alerts.example.com;
    ssl_certificate     /etc/letsencrypt/live/alerts.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/alerts.example.com/privkey.pem;
    add_header Strict-Transport-Security "max-age=31536000" always;
    client_max_body_size 210m;                # backups and CSV uploads

    location / {
        proxy_pass http://127.0.0.1:8321;     # nginx sends Host: 127.0.0.1:8321 by default; keep it
        proxy_set_header X-Forwarded-For $remote_addr;   # the visitor's address, not one they made up
        proxy_set_header X-Forwarded-Proto $scheme;     # so the sign-in cookie is marked Secure
        proxy_read_timeout 300s;              # long backtests
    }
}

server {
    listen 80;
    server_name alerts.example.com;
    return 301 https://$host$request_uri;
}
```

`sudo nginx -t && sudo systemctl reload nginx`. Use `$remote_addr` rather than
`$proxy_add_x_forwarded_for`, which keeps whatever the visitor sent.

## Firewall

Only SSH and the web ports should be open. With ufw (Ubuntu, Debian):

```
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable
sudo ufw status verbose
```

Port 8321 stays closed: the proxy reaches Altius on `127.0.0.1`. If your provider has a
cloud firewall (Hetzner, DigitalOcean, AWS security groups), set the same rules there too.
Alerts need outgoing connections (ntfy, Telegram, email, webhooks) and price data needs
outgoing HTTPS, so leave outgoing traffic open.

## Sign-in hardening checklist

- [ ] `TRADEALERTS_PASSWORD` is long and random (the setup script makes a 24-character one).
      It lives in `.env`; keep that file readable only by you (`chmod 600 .env`).
- [ ] The app listens on `127.0.0.1` and is reached only through the HTTPS proxy.
- [ ] `--no-auth` is **not** used, unless the proxy itself makes people sign in.
- [ ] HTTPS everywhere: port 80 only redirects to 443. The sign-in cookie is then marked
      `Secure` (plus `HttpOnly` and `SameSite=Strict`, always).
- [ ] The firewall allows only SSH, 80 and 443 in.
- [ ] SSH uses keys, not passwords (`PasswordAuthentication no` in `/etc/ssh/sshd_config`),
      and the server installs security updates automatically (`unattended-upgrades`).
- [ ] With Docker, `FORWARDED_ALLOW_IPS` is set (see Option A), so each visitor's wrong
      passwords are counted separately.
- [ ] To sign out every browser (for example after losing a device), change
      `TRADEALERTS_PASSWORD` and restart. Sessions otherwise last 7 days.
- [ ] Optional second layer: Caddy `basic_auth`, an IP allow-list for your own addresses, or
      reach it only over Tailscale or WireGuard.

## Secrets on a server

A server has no password store (Windows Credential Manager, macOS Keychain, Secret Service),
so alert tokens and email passwords are saved in `config.yaml`. Altius writes that file, the
sign-in secret (`data/.session_secret`) and the safety copies made before a restore
(`data/backups/`) readable only by the account that runs it. Keep the whole workspace folder
private, and remember that a full backup (*Settings → Backup*) contains the secrets: the
*shareable* backup blanks them. Power users can keep a secret out of `config.yaml` entirely by
writing `${NAME}` in its place and setting `NAME` in the environment (with Docker, in `.env`).

## Updating and backups

- **Update:** download the new release, copy it over, then `docker compose up -d --build`
  (Docker) or run `setup.sh` again. Your data folder is left as it is.
- **Back up** the workspace folder (Docker: `./workspace`) or download *Settings → Backup*
  regularly, and keep copies somewhere other than the server. Restoring checks every part
  first and keeps a safety copy of what it replaces.

## Alerts to your own network

The self-hosted edition may send alerts to addresses on your own network (for example a
home ntfy server at `http://192.168.1.10`), because only you can change its settings. The
hosted edition refuses private and internal addresses.
