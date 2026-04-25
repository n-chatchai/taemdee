#!/usr/bin/env bash
set -euo pipefail

cd /srv/taemdee
git pull --ff-only
uv sync --frozen
uv run alembic upgrade head
sudo systemctl reload taemdee
