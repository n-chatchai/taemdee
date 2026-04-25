#!/usr/bin/env bash
set -euo pipefail

# Find the project root directory (one level up from this script)
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "Deploying in $PROJECT_ROOT..."

git pull --ff-only
~/.local/bin/uv sync --frozen
~/.local/bin/uv run alembic upgrade head
sudo systemctl reload taemdee
