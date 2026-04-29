"""Single Jinja2Templates instance shared by every route module.

Carries Jinja globals like `asset_version` so all stylesheet links get
cache-busted on each deploy.
"""

import subprocess
from pathlib import Path
from typing import Optional

from fastapi.templating import Jinja2Templates

from app.models.util import bkk_feed_time, bkk_hms, bkk_short_date
from app.services.logo_gen import VALID_STYLE_IDS, render_style


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


def shop_logo(shop) -> Optional[dict]:
    """Render the typography style the shop owner picked in S2.2.

    Returns a dict {css_class, text, show_dot} ready for templates, or None
    if the shop hasn't picked one — callers should fall back to a plain
    name + dot rendering in that case.
    """
    if not shop or not shop.logo_url or not shop.logo_url.startswith("text:"):
        return None
    
    parts = shop.logo_url.split(":", 2)
    style_id = parts[1]
    
    if style_id not in VALID_STYLE_IDS:
        return None
    
    rendered = render_style(shop.name, style_id)
    if len(parts) == 3 and parts[2].strip():
        rendered["text"] = parts[2].strip()
        
    return rendered


templates = Jinja2Templates(directory="app/templates")
templates.env.globals["asset_version"] = ASSET_VERSION
templates.env.globals["shop_logo"] = shop_logo
templates.env.filters["bkk_hms"] = bkk_hms
templates.env.filters["bkk_feed_time"] = bkk_feed_time
templates.env.filters["bkk_short_date"] = bkk_short_date
