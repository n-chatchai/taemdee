# Release Plan

**Goal:** ship TaemDee to a VPS, get a live URL we can test, and set up the auto-deploy loop so every push to `main` updates production.

The action ([.github/workflows/deploy.yml](.github/workflows/deploy.yml)) and the deploy script ([scripts/deploy.sh](scripts/deploy.sh)) are already in place. This doc is what to do **once** before they start working.

---

## 1. Decisions to lock before you SSH

| Decision | Recommendation | Why |
|---|---|---|
| **VPS provider** | DigitalOcean (Singapore) or Vultr (BKK) | Low Thai latency |
| **Plan** | Cheapest 1 GB / 1 vCPU / 25 GB SSD (~$6/mo) | Plenty for v1 |
| **OS** | Ubuntu 24.04 LTS | Familiar, recent |
| **Domain** | `taemdee.app` (or whatever you own) | Need a real domain for TLS |
| **Subdomain layout** | `taemdee.app` → prod (later: `staging.taemdee.app`) | One subdomain for now; staging when needed |
| **Postgres** | On the same VPS for v1 | Managed PG can wait until you outgrow it |
| **TLS** | Let's Encrypt via Certbot + Nginx | Free, auto-renews |

---

## 2. VPS setup (one-time, ~90 minutes)

### 2.1 Spin up + DNS

1. Create the droplet/instance (Ubuntu 24.04, SG region).
2. Note the public IP.
3. Point your domain's A record at it: `taemdee.app  →  <IP>`.

### 2.2 Initial SSH as root

```bash
ssh root@<IP>
apt update && apt upgrade -y
apt install -y postgresql nginx ufw curl git
ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw --force enable
```

### 2.3 Create deploy user + Postgres role

```bash
# Create system user
useradd --system --home /srv/taemdee --shell /bin/bash --create-home taemdee

# Postgres user + DB
sudo -u postgres createuser taemdee --pwprompt   # set a strong password
sudo -u postgres createdb -O taemdee taemdee
```

### 2.4 Install `uv` for the deploy user

```bash
sudo -u taemdee bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
# uv lands at /srv/taemdee/.local/bin/uv  (matches scripts/deploy.sh paths)
```

### 2.5 Clone the repo

```bash
sudo -u taemdee git clone https://github.com/<your-handle>/taemdee.git /srv/taemdee
cd /srv/taemdee
sudo -u taemdee /srv/taemdee/.local/bin/uv sync --frozen
```

### 2.6 Production `.env`

```bash
sudo -u taemdee cp .env.example .env
sudo -u taemdee nano .env
```

Fill in:
```
DATABASE_URL=postgresql+asyncpg://taemdee:<password>@localhost:5432/taemdee
JWT_SECRET=<output of: python3 -c "import secrets; print(secrets.token_urlsafe(64))">
ENVIRONMENT=production
LINE_CHANNEL_ID=<from LINE Developers Console>
LINE_CHANNEL_SECRET=<same>
LINE_REDIRECT_URI=https://taemdee.app/auth/line/callback
```

In LINE Developers Console: add `https://taemdee.app/auth/line/callback` to the channel's allowed callback URLs.

### 2.7 First migration

```bash
sudo -u taemdee bash -c 'cd /srv/taemdee && /srv/taemdee/.local/bin/uv run alembic upgrade head'
```

### 2.8 Sudoers entry for graceful reload

```bash
echo 'taemdee ALL=(root) NOPASSWD: /bin/systemctl reload taemdee' \
  > /etc/sudoers.d/taemdee
chmod 440 /etc/sudoers.d/taemdee
```

### 2.9 Systemd unit

`/etc/systemd/system/taemdee.service` — copy verbatim from [README.md "Production deployment"](README.md#production-deployment-vps-zero-downtime). (You'll need to add `gunicorn` to deps: `cd /srv/taemdee && sudo -u taemdee /srv/taemdee/.local/bin/uv add gunicorn`.)

```bash
systemctl daemon-reload
systemctl enable --now taemdee
systemctl status taemdee   # should be "active (running)"
```

### 2.10 Nginx + TLS

`/etc/nginx/sites-available/taemdee` — copy from [README.md](README.md). Then:

```bash
ln -s /etc/nginx/sites-available/taemdee /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# TLS (replaces the listen 443 block with cert paths)
apt install -y certbot python3-certbot-nginx
certbot --nginx -d taemdee.app
```

Visit `https://taemdee.app` — landing page should render.

---

## 3. GitHub setup

### 3.1 Create the repo + push

```bash
# locally
gh repo create taemdee --private --source=. --remote=origin
git push -u origin main
```

### 3.2 Add deploy secrets

```bash
# locally — install gh if needed
gh secret set DEPLOY_HOST --body "<VPS IP>"
gh secret set DEPLOY_USER --body "taemdee"
gh secret set DEPLOY_PATH --body "/srv/taemdee"
gh secret set DEPLOY_PORT --body "22"

# Generate a deploy keypair
ssh-keygen -t ed25519 -f ~/.ssh/taemdee_deploy -N ""
gh secret set DEPLOY_SSH_KEY < ~/.ssh/taemdee_deploy

# Authorize the public half on the VPS
ssh-copy-id -i ~/.ssh/taemdee_deploy.pub taemdee@<VPS IP>
# OR manually: append cat ~/.ssh/taemdee_deploy.pub to /srv/taemdee/.ssh/authorized_keys
```

### 3.3 Trigger the first action run

```bash
git commit --allow-empty -m "trigger first deploy"
git push
```

Watch in `gh run watch` or the GitHub Actions tab. Should see the SSH step succeed and run `scripts/deploy.sh`.

---

## 4. First-deploy smoke checklist

After the action completes, hit `https://taemdee.app/...` and verify:

- [ ] `GET /` — landing page renders
- [ ] `GET /docs` — Swagger UI loads, shows ~19 routes
- [ ] `GET /shop/register` — login page renders
- [ ] `POST /auth/otp/request` with your phone — check VPS journal: `journalctl -u taemdee -f` should show the OTP printed (since `ENVIRONMENT=production` raises NotImplementedError on real SMS — see Phase 3 of TODO.md). For now, set ENVIRONMENT=staging in `.env` so OTP prints to the journal during testing.
- [ ] LINE Login flow → click "เชื่อมต่อ LINE" → consent on LINE → returns to `/shop/dashboard`
- [ ] `/scan/<shop_id>` (after creating a shop) → issues stamp → redirects to `/card/<shop_id>`

---

## 5. Ongoing release loop

```
1. local: write code, run tests
2. local: git commit + git push origin main
3. GH Actions: SSH → git pull → uv sync → alembic upgrade → systemctl reload
4. live in ~30 sec
```

That's it. Zero downtime per the README's reload mechanic. No staging step in v1.

### Tagging stable releases (optional)

```bash
git tag -a v0.1.0 -m "first beta — designer wired"
git push --tags
```

Tags don't trigger anything automatically — they're just markers for "I want to remember this state."

---

## 6. Rollback

If a deploy breaks production:

```bash
ssh taemdee@<VPS IP>
cd /srv/taemdee
git log --oneline -5             # find the last good commit
git reset --hard <good-sha>
~/.local/bin/uv sync --frozen
~/.local/bin/uv run alembic downgrade -1   # ONLY if the bad deploy ran a migration
sudo systemctl reload taemdee
```

Don't `git push --force` to "fix" the remote — instead, `git revert <bad-sha>` locally and push. The action redeploys the reverted state cleanly.

---

## 7. Gates before exposing to real shops

Don't share the URL widely until at least these are true:

- [ ] Real SMS OTP wired (Phase 3 in [TODO.md](TODO.md)) — currently `production` env raises `NotImplementedError`
- [ ] LINE Messaging API for DeeReach sends — otherwise no revenue path
- [ ] Slip2Go top-up — otherwise top-ups need manual admin work
- [ ] `pg_dump` nightly backup → off-VPS storage
- [ ] Sentry hooked up — otherwise errors are invisible

The list is also in TODO.md Phase 5.

---

## 8. Stage your way in

Suggested rollout sequence:

| When | Audience | Why |
|---|---|---|
| Day 1 (right after this setup) | **You only** — `taemdee.app` is reachable, you click through every flow | Find dev/prod-only bugs |
| Phase 1 done (designer wired) | **You + 1 friend** with a fake shop | First non-self UX feedback |
| Phase 3 done (real SMS, LINE, Slip2Go) | **3–5 friendly café owners** you know | Real-world stress test |
| Phase 5 done (TLS solid, backups, Sentry) | **Public soft launch** — open registrations | Scale carefully |

Every step gates the next. Don't skip Phase 3 to the public — broken top-ups will burn trust.
