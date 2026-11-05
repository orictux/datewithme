# Native Linux installation

DateWithMe runs without Docker on Linux with Python 3.12+, persistent writable directories and a single Uvicorn worker. For public deployment, use an HTTPS reverse proxy.

Local setup example:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p data secrets
chmod 700 data secrets
export COOKIE_SECURE=0
export ALLOW_INSECURE_HTTP=1
export LOCAL_PORT=8031
uvicorn app.main:app --host 127.0.0.1 --port 8031 --workers 1 --no-access-log
```

Open `http://localhost:8031/setup`. The one-time token is stored in `secrets/setup.token`. A public domain is optional at initial setup and can be added later in administrator Settings.

For public hosting, use a dedicated unprivileged Linux user and a service manager such as systemd. Set `COOKIE_SECURE=1` and `ALLOW_INSECURE_HTTP=0` for HTTPS-only access. Bind the app to loopback and forward requests through a trusted HTTPS proxy. Protect the `data/` and `secrets/` directories with restrictive permissions, separate backups, and tested restores.

If you publish your own port rather than `8031`, set `LOCAL_PORT` to that externally accessed port. Avoid exposing Uvicorn directly to the Internet.
