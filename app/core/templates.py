"""Single Jinja2Templates instance shared by every route module.

Carries Jinja globals like `asset_version` so all stylesheet links get
cache-busted on each deploy.
"""

import subprocess
from pathlib import Path

from fastapi.templating import Jinja2Templates


def _compute_asset_version() -> str:
    """Stable per-deploy token used to cache-bust /static/css/*.css.

    Prefers the short git SHA (works in CI/VPS checkouts). Falls back to the
    largest CSS file mtime so a hand-edited CSS still invalidates browsers.
    """
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return sha.decode().strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        css_dir = Path("static/css")
        if css_dir.exists():
            latest = max((p.stat().st_mtime for p in css_dir.glob("*.css")), default=0)
            return str(int(latest))
        return "dev"


ASSET_VERSION = _compute_asset_version()

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["asset_version"] = ASSET_VERSION
