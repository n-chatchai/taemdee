# TaemDee (แต้มดี)

Digital stamp-card platform for Thai SME shops. See [PRODUCT.md](PRODUCT.md) for the full product spec.

## Local development

Requires **Python 3.13** and **PostgreSQL**.

```bash
# 1. Copy environment template and edit DATABASE_URL
cp .env.example .env

# 2. Create the Postgres database
createdb taemdee

# 3. Install dependencies (uv)
uv sync

# 4. Generate the initial migration and apply it
uv run alembic revision --autogenerate -m "initial schema"
uv run alembic upgrade head

# 5. Run the dev server
uv run uvicorn app.main:app --reload
```

The app will be at `http://localhost:8000`.

## Migrations

Schema is managed by [Alembic](https://alembic.sqlalchemy.org). `DATABASE_URL` is loaded from `.env` via `app.config.settings`.

```bash
# Create a new migration from model changes
uv run alembic revision --autogenerate -m "<description>"

# Apply pending migrations
uv run alembic upgrade head

# Roll back one migration
uv run alembic downgrade -1

# See current revision
uv run alembic current
```

## Project structure

- `app/` — FastAPI application
  - `main.py` — entry point (async lifespan)
  - `config.py` — `pydantic-settings` loads `.env`
  - `database.py` — async SQLAlchemy engine + `AsyncSession`
  - `models/` — SQLModel schema, one aggregate per file (`shop`, `customer`, `stamp`, `credit`)
  - `routes/` — HTTP handlers
  - `services/` — business logic (stamp issuance, redemption, DeeReach, topups)
  - `schemas/` — Pydantic I/O types (request/response), distinct from DB models
  - `templates/` — Jinja2 templates
- `alembic/` — migrations
- `static/` — CSS + JS assets
- `PRODUCT.md` — Product requirements document (v1)

## Production deployment (VPS, zero-downtime)

**Stack:** Nginx (TLS + reverse proxy) → Gunicorn + uvicorn workers → FastAPI. Systemd supervises Gunicorn and drives graceful reloads via `SIGHUP`.

### One-time setup on the VPS

```bash
sudo useradd --system --home /srv/taemdee --shell /bin/bash taemdee
sudo -u taemdee git clone <repo> /srv/taemdee
cd /srv/taemdee
uv sync
uv add gunicorn                       # production-only dep
sudo -u postgres createuser taemdee
sudo -u postgres createdb -O taemdee taemdee
cp .env.example .env                  # edit DATABASE_URL
uv run alembic upgrade head
```

### systemd unit — `/etc/systemd/system/taemdee.service`

```ini
[Unit]
Description=TaemDee FastAPI
After=network.target postgresql.service

[Service]
Type=notify
User=taemdee
WorkingDirectory=/srv/taemdee
EnvironmentFile=/srv/taemdee/.env
ExecStart=/srv/taemdee/.venv/bin/gunicorn app.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 127.0.0.1:8000 \
  --timeout 30 \
  --graceful-timeout 30
ExecReload=/bin/kill -HUP $MAINPID
KillMode=mixed
TimeoutStopSec=30
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable with `sudo systemctl enable --now taemdee`.

### Nginx — `/etc/nginx/sites-available/taemdee`

```nginx
server {
    listen 443 ssl http2;
    server_name taemdee.app;
    # ssl_certificate ... ssl_certificate_key ...

    location /static/ {
        alias /srv/taemdee/static/;
        expires 30d;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Deploy — [scripts/deploy.sh](scripts/deploy.sh)

```bash
#!/usr/bin/env bash
set -euo pipefail

cd /srv/taemdee
git pull --ff-only
uv sync --frozen
uv run alembic upgrade head
sudo systemctl reload taemdee         # SIGHUP → Gunicorn graceful worker rotation
```

`reload` needs passwordless sudo for the taemdee user on that one command. Add to `/etc/sudoers.d/taemdee`:

```
taemdee ALL=(root) NOPASSWD: /bin/systemctl reload taemdee
```

### Push-to-deploy via GitHub Actions

[.github/workflows/deploy.yml](.github/workflows/deploy.yml) runs on every push to `main` (or manual `workflow_dispatch`). It SSHes into the VPS and executes `scripts/deploy.sh`.

**Required GitHub repo secrets:**

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | VPS hostname or IP |
| `DEPLOY_USER` | `taemdee` |
| `DEPLOY_SSH_KEY` | Private key (PEM) whose public half is in `~taemdee/.ssh/authorized_keys` |
| `DEPLOY_PORT` | Optional — defaults to 22 |

Deploys are serialized (GitHub Actions `concurrency: deploy`) so two pushes in quick succession won't race.

### Why this is zero-downtime

`systemctl reload` sends `SIGHUP`. Gunicorn's master:
1. Spawns new workers with the new code
2. Stops routing new requests to old workers
3. Lets old workers finish in-flight requests (up to `graceful-timeout`)
4. Kills old workers

No request drops. Clients never see a 502.

### The two things that break zero-downtime

1. **Backward-incompatible migrations.** A migration that removes a column, renames a table, or adds a `NOT NULL` column without a default will break old workers still serving requests. Use the **expand-contract pattern** — expand schema first (add nullable column, dual-write), release new code, contract in a follow-up release.
2. **Long-lived connections** (SSE, WebSockets) drop on reload and have to reconnect. Clients should auto-reconnect. Strictly speaking this is "zero request drops," not "zero interruption."
