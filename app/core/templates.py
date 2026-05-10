"""Single Jinja2Templates instance shared by every route module.

Carries Jinja globals like `asset_version` so all stylesheet links get
cache-busted on each deploy.
"""

import re
import subprocess
from pathlib import Path
from typing import Optional
import unicodedata

from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.models.util import bkk_feed_time, bkk_feed_time_short, bkk_hms, bkk_relative_time, bkk_short_date
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
    """Render the typography style or custom image the shop owner picked.
    
    Format: 'text:style_id:custom_text' or 'url:https://...'
    """
    if not shop or not shop.logo_url:
        return None
        
    if shop.logo_url.startswith("url:"):
        return {"is_image": True, "url": shop.logo_url[4:]}
        
    if not shop.logo_url.startswith("text:"):
        return None
    
    parts = shop.logo_url.split(":", 2)
    style_id = parts[1]
    
    if style_id not in VALID_STYLE_IDS:
        return None
    
    rendered = render_style(shop.name, style_id)
    if len(parts) == 3 and parts[2].strip():
        rendered["text"] = parts[2].strip()
        
    rendered["is_image"] = False
    return rendered


def slugify(text: str) -> str:
    """Simple slugify for Thai/English filenames."""
    if not text:
        return "shop"
    # Keep Thai characters, alphanumeric, and spaces
    text = re.sub(r'[^\u0E00-\u0E7F\w\s-]', '', text).strip().lower()
    return re.sub(r'[-\s]+', '-', text)

def has_thai(text: str) -> bool:
    """True if any character is in the Thai unicode block (U+0E00-U+0E7F).

    Used by .shop-logo-mark to swap the latin-display font (Host Grotesk)
    for a Thai-friendly one (Prompt) — the latin font has no Thai glyphs
    and falls back to the OS default, which looks unintended.
    """
    return any("฀" <= c <= "๿" for c in (text or ""))


# Background color of each built-in reward illustration. Mirrors the
# `<rect width="100" height="100" fill="...">` baked into reward_svg.html
# so callers (e.g. .cl-voucher-image) can paint the surrounding strip
# in the same hue — keeps the box and the icon visually unified, even
# when the icon is a custom URL image (no internal rect).
_REWARD_BG = {
    "gift_box":   "#FFE6DF",
    "card":       "#FFF1B8",
    "star":       "#C8E8D0",
    "coffee_cup": "#FFF1B8",
}


def reward_bg(img_id: Optional[str]) -> str:
    """Return the background hex for the given reward illustration id.

    Custom URL images and unknown ids fall back to the cream surface
    so the strip still looks intentional rather than a dark overlay.
    """
    if img_id and img_id in _REWARD_BG:
        return _REWARD_BG[img_id]
    return "#F5F1E8"


def has_perm(staff, perm: str) -> bool:
    """Mirror of core.auth.require_permission for templates.

    Returns True when the staff is the owner (is_owner short-circuits
    every gate) OR the named flag is set on the StaffMember row.
    Pass `request.state.staff` directly. Returns False when staff is
    None — covers the no-session and pre-unification cases so the UI
    fails closed.
    """
    if staff is None:
        return False
    if getattr(staff, "is_owner", False):
        return True
    return bool(getattr(staff, perm, False))


templates = Jinja2Templates(directory="app/templates")
templates.env.globals["asset_version"] = ASSET_VERSION
templates.env.globals["settings"] = settings
templates.env.globals["shop_logo"] = shop_logo
templates.env.globals["has_thai"] = has_thai
templates.env.globals["has_perm"] = has_perm
templates.env.globals["reward_bg"] = reward_bg
from app.services.shop_swatch import (  # noqa: E402
    shop_swatch as _tmpl_shop_swatch,
    shop_theme_bg as _tmpl_shop_theme_bg,
    shop_theme_ink as _tmpl_shop_theme_ink,
)
templates.env.globals["shop_swatch"] = _tmpl_shop_swatch
templates.env.globals["shop_theme_bg"] = _tmpl_shop_theme_bg
templates.env.globals["shop_theme_ink"] = _tmpl_shop_theme_ink
templates.env.filters.update({
    "bkk_hms": bkk_hms,
    "bkk_feed_time": bkk_feed_time,
    "bkk_feed_time_short": bkk_feed_time_short,
    "bkk_short_date": bkk_short_date,
    "bkk_relative_time": bkk_relative_time,
    "slugify": slugify,
})
