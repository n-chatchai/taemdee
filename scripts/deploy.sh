#!/usr/bin/env bash
# Pull latest, run migrations, restart the systemd service, ping Slack.
#
# Usage (on prod):
#   bash scripts/deploy.sh
#
# Env (all optional — set in .env on prod alongside the rest):
#   SLACK_DEPLOY_WEBHOOK_URL   Slack incoming-webhook URL — silent if unset
#   SERVICE_NAME               systemd unit name (default: taemdee)
#   SLACK_USERNAME             bot name shown in Slack (default: taemdee-deploy)
#   PROD_URL                   base URL the script polls /version on after
#                              restart to confirm the live process picked up
#                              the new code (default: https://taemdee.com)
#
# Exit codes:
#   0  success — Slack gets ✅ with commit SHA + subject + elapsed seconds
#   1+ any phase failed — Slack gets ❌ with the failing phase name

set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-taemdee}"
WORKER_SERVICE_NAME="${WORKER_SERVICE_NAME:-taemdee-worker}"
SLACK_USERNAME="${SLACK_USERNAME:-taemdee-deploy}"
UV_BIN="${UV_BIN:-$HOME/.local/bin/uv}"
PROD_URL="${PROD_URL:-https://taemdee.com}"
START_TS="$(date +%s)"
PHASE="setup"   # mutated by each phase below; ERR trap reads it

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# Pull SLACK_DEPLOY_WEBHOOK_URL out of .env if set (so the script
# matches the same registry app/core/config.py reads from). Already-set
# environment values win, so a one-off `SLACK_DEPLOY_WEBHOOK_URL=... bash
# scripts/deploy.sh` overrides without editing the file. Tolerates an
# optional `export ` prefix and surrounding quotes.
if [ -z "${SLACK_DEPLOY_WEBHOOK_URL:-}" ] && [ -f .env ]; then
  SLACK_DEPLOY_WEBHOOK_URL=$(grep -E '^(export[[:space:]]+)?SLACK_DEPLOY_WEBHOOK_URL=' .env | head -1 | sed -E 's/^(export[[:space:]]+)?SLACK_DEPLOY_WEBHOOK_URL=//' | tr -d '"' | tr -d "'")
fi

if [ -n "${SLACK_DEPLOY_WEBHOOK_URL:-}" ]; then
  echo "[deploy] Slack notify: enabled"
else
  echo "[deploy] Slack notify: disabled (set SLACK_DEPLOY_WEBHOOK_URL in .env)"
fi

# ─── Slack helper ────────────────────────────────────────────────────────
post_slack() {
  local emoji="$1" text="$2"
  [ -z "${SLACK_DEPLOY_WEBHOOK_URL:-}" ] && return 0
  # JSON-escape via python so commit subjects with quotes/newlines don't break it.
  local payload
  payload=$(python3 -c '
import json, sys
print(json.dumps({
  "username": sys.argv[1],
  "icon_emoji": sys.argv[2],
  "text": sys.argv[3],
}))' "$SLACK_USERNAME" "$emoji" "$text")
  curl -fsS -X POST -H 'Content-Type: application/json' \
    --data "$payload" "$SLACK_DEPLOY_WEBHOOK_URL" > /dev/null || true
}

on_error() {
  local code=$?
  local elapsed=$(( $(date +%s) - START_TS ))
  local sha
  sha=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
  post_slack ":x:" "❌ Deploy failed at *${PHASE}* (exit ${code}) · \`${sha}\` · ${elapsed}s"
  exit "$code"
}
trap on_error ERR

echo "Deploying in $PROJECT_ROOT..."

# ─── Phases ──────────────────────────────────────────────────────────────
PHASE="git pull"
git pull --ff-only

PHASE="uv sync"
"$UV_BIN" sync --frozen

PHASE="alembic upgrade head"
"$UV_BIN" run alembic upgrade head

# restart (NOT reload) so asyncpg drops its connection pool and picks
# up the new schema — see memory/reference_prod_deploy_restart.md.
PHASE="systemctl restart ${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

# Restart the DeeReach RQ worker too — same reason: it loads
# app/services/deereach.py + app/tasks/deereach.py at startup, so without
# this restart it keeps running the previous deploy's code. Skip silently
# when the unit is missing (older boxes that haven't installed it yet).
if systemctl list-unit-files | grep -q "^${WORKER_SERVICE_NAME}\.service"; then
  PHASE="systemctl restart ${WORKER_SERVICE_NAME}"
  sudo systemctl restart "${WORKER_SERVICE_NAME}"
else
  echo "[deploy] ${WORKER_SERVICE_NAME}.service not installed — skipping worker restart"
fi

# ─── Verify the live process is now serving the new SHA ─────────────────
# /version returns {"version": "<short-sha>"}. We poll briefly so the
# script doesn't beat the new uvicorn process to the punch — up to 10
# attempts (~10s). If the live SHA still doesn't match after that, we
# notify with a ⚠️ instead of ✅ so it's obvious the systemctl restart
# didn't actually pick up the new code.
SHA=$(git rev-parse --short HEAD)
SUBJECT=$(git log -1 --pretty=%s)

LIVE_SHA="?"
for _ in {1..10}; do
  LIVE_SHA=$(curl -fsS --max-time 2 "${PROD_URL}/version" 2>/dev/null | python3 -c '
import json, sys
try: print(json.load(sys.stdin).get("version") or "?")
except Exception: print("?")
' || echo "?")
  [ "$LIVE_SHA" = "$SHA" ] && break
  sleep 1
done

ELAPSED=$(( $(date +%s) - START_TS ))
if [ "$LIVE_SHA" = "$SHA" ]; then
  echo "✅ Deployed ${SHA} (${ELAPSED}s) — live SHA matches"
  post_slack ":white_check_mark:" "✅ Deployed \`${SHA}\` · ${SUBJECT} · ${ELAPSED}s · live: \`${LIVE_SHA}\` ✓"
else
  echo "⚠️  Deployed ${SHA} but live still serves ${LIVE_SHA} (${ELAPSED}s)"
  post_slack ":warning:" "⚠️ Deployed \`${SHA}\` · ${SUBJECT} · ${ELAPSED}s · live: \`${LIVE_SHA}\` ✗ (process didn't pick up new code)"
fi
