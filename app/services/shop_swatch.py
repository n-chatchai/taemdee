"""Per-shop colors used by the customer UI.

Two flavors:

  · shop_swatch(shop)      — deterministic palette pick by hashing the
    shop UUID. Used historically by /my-cards card tints.

  · shop_theme_bg(shop)    — chip background tied to the shop's chosen
    theme (taemdee/mono/night/pastel/sport). Used by the shared
    .shop-avatar chip so the same shop reads the same theme color on
    every customer surface, even pages that aren't wrapped in the
    .theme-X class.
"""
from __future__ import annotations

import uuid


# Warm earthy hues that work on the cream surface — sized so consecutive
# UUIDs don't collide on common debug shop sets.
SHOP_SWATCHES = (
    "#E87A6A",  # coral
    "#3E6B5A",  # forest mint
    "#C49A4D",  # mustard
    "#7A4A8A",  # plum
    "#3A6B7A",  # teal
    "#B05F4A",  # brick
    "#5C7A8A",  # slate
)


# (chip-bg, chip-fg) per theme — chip bg uses each theme's --accent so
# the chip stands out against the page bg (themes' bg is light/cream
# for most variants; using accent keeps the chip readable everywhere
# including pages without .theme-X). Foreground picked for contrast on
# the accent — white on warm/dark accents, dark on the yellow night
# accent (#FFD952 + white would be unreadable).
THEME_CHIP = {
    "taemdee": ("#FF5E3A", "#FFFFFF"),
    "mono":    ("#111111", "#FFFFFF"),
    "night":   ("#FFD952", "#141414"),
    "pastel":  ("#E87A6A", "#FFFFFF"),
    "sport":   ("#15803D", "#FFFFFF"),
}


def shop_swatch(shop_or_id) -> str:
    """Return the hex swatch for a Shop row or a raw UUID. None → coral."""
    if shop_or_id is None:
        return SHOP_SWATCHES[0]
    sid = shop_or_id if isinstance(shop_or_id, uuid.UUID) else getattr(shop_or_id, "id", None)
    if sid is None:
        return SHOP_SWATCHES[0]
    return SHOP_SWATCHES[sid.int % len(SHOP_SWATCHES)]


def shop_theme_bg(shop) -> str:
    """Return the chip bg hex for the shop's chosen theme. None → taemdee."""
    name = (getattr(shop, "theme_name", None) or "taemdee")
    return THEME_CHIP.get(name, THEME_CHIP["taemdee"])[0]


def shop_theme_ink(shop) -> str:
    """Return the chip foreground (text) hex for the shop's theme."""
    name = (getattr(shop, "theme_name", None) or "taemdee")
    return THEME_CHIP.get(name, THEME_CHIP["taemdee"])[1]
