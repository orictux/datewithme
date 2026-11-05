# Deployment and hosting

DateWithMe supports Docker Compose, native Python on Linux, and ASGI-capable cloud application hosting. It requires a persistent app process, storage for both encrypted data and secret keys, and a single server worker. PHP-only shared hosting is not supported.

## Local Docker setup on port 8031

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec datewithme cat /secrets/setup.token
```

Open `http://localhost:8031/setup` or `http://127.0.0.1:8031/setup`. Complete setup without entering a public URL. An Internet domain is **not required** for local use.

The container listens on internal port `8080`. Docker publishes it on the host's port `8031` by default. Both loopback hostnames are authorized for browser write requests on the configured host port. The port is configurable with `PORT` in `.env`.

## Optional public HTTPS domain or Cloudflare Tunnel

You can add a public domain at any time after installation:

1. Configure an HTTPS reverse proxy, Cloudflare Tunnel, or other trusted ingress to reach DateWithMe on the host's local port `8031`.
2. Open the local administrator panel at `http://localhost:8031/panel`.
3. Open **Settings > Allowed remote website URL (optional)**, enter your full `https://` domain (for example `https://dates.example.com`), and save.
4. Open the public domain and test sign-in, invitations, uploads, and notifications.

The configured URL is used for invitation links and is added to the browser write-origin allowlist. Leaving it empty keeps local installation working and makes invitation links use the address in your current browser. Setting the URL **does not** configure DNS, Cloudflare, the reverse proxy, or networking automatically. If you need multiple authorized domains, advanced users can add comma-separated HTTPS origins in `ALLOWED_ORIGINS` in `.env` and restart the container.

Cloudflare Tunnel example: route the HTTPS hostname to `http://localhost:8031` when `cloudflared` runs on the Docker host. If it runs in a separate container, use an appropriate Docker network and the DateWithMe container's internal port `8080` instead.

## Public HTTPS deployment without a tunnel

For Internet exposure, use an HTTPS reverse proxy (for example Caddy or Nginx) and configure the app using an HTTPS administrator URL. Keep the app behind the proxy. Set `COOKIE_SECURE=1` and `ALLOW_INSECURE_HTTP=0` when you no longer need direct localhost HTTP access for administration. Do not expose the internal HTTP backend directly to the public Internet.

Typical Docker `.env` for a same-host proxy:

```env
BIND_ADDRESS=127.0.0.1
PORT=8031
COOKIE_SECURE=1
ALLOW_INSECURE_HTTP=0
ALLOWED_ORIGINS=
```

A basic Caddyfile:

```caddyfile
dates.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8031
}
```

After securing the domain, set `https://dates.example.com` in the app's administrator Settings. The proxy must preserve the original hostname, forward scheme correctly, apply request size limits and rate limit sensitive endpoints. Use a trusted proxy configuration for forwarding headers. Browser HTTPS is required for Web Push and most service worker functionality.

For first-time remote setup on an HTTPS domain, the same-origin installation request can pass after the owner supplies the locally generated one-time setup token. The HTTPS origin used for installation remains trusted until an explicit different public URL is configured. Unknown domains are never automatically trusted for subsequent authenticated writes.

## Updating an existing 0.9.0-beta test install

Do **not** delete the `datewithme-data` or `datewithme-secrets` volumes. Stop the container, back up both volumes, replace the project source with 0.9.1-beta, then edit your existing `.env` file, which is not overwritten by source updates:

```env
BIND_ADDRESS=127.0.0.1
PORT=8031
COOKIE_SECURE=0
ALLOW_INSECURE_HTTP=1
ALLOWED_ORIGINS=
```

Remove obsolete `PUBLIC_BASE_URL` entries from `.env` since domain configuration now belongs in the administrator Settings. Rebuild with `docker compose up -d --build`. Open `http://localhost:8031/setup` if setup was incomplete, otherwise use `/panel`. **Never run `docker compose down -v` while you need existing data.**

## Container volumes and backups

The `datewithme-data` volume holds encrypted application records. The `datewithme-secrets` volume holds the encryption key, setup token while onboarding, and Web Push private key. Back up both volumes together. Losing the encryption key permanently prevents recovery of encrypted data.

## Cloud hosting constraints

The app needs a continuously running Python ASGI process, writable persistent disk volumes and one application replica. Hosting platforms with ephemeral filesystems or auto-scaling replicas require a different storage architecture. Background reminders depend on the process staying awake.

## Data privacy

The app does not intentionally save visitor IP addresses in its SQLite database. Hosting providers, CDNs, proxies, DNS services and browsers may store metadata separately. Visitors who possess an invitation URL can access that invitation. Use long random codes and share them privately.
