"""Typography-based "AI" logo generator.

Per PRD §6.E the default onboarding path is "Create with AI": the system
generates 3 logo options from the shop name across different typography styles.

This is intentionally not a raster image-gen API call — for v1 we want it
free, instant, and on-brand with the design system. Each `LogoStyle` is a
named typography variant (CSS class + a derivation rule for the display text).
The `generate_logos` function picks 3 deterministically-seeded variants from
the pool so the same `(shop_name, seed)` always renders the same trio
(important so a page refresh doesn't reshuffle the picker).
"""

import random
from dataclasses import dataclass
from typing import Callable, List


@dataclass(frozen=True)
class LogoStyle:
    id: str
    css_class: str
    text_fn: Callable[[str], str]
    show_dot: bool = False  # render an accent-coloured trailing "." after the text


def _initial(name: str) -> str:
    name = name.strip() or "ร้าน"
    return name[0].upper()


def _two_initials(name: str) -> str:
    parts = (name.strip() or "ร้าน").split()
    if len(parts) >= 2:
        return (parts[0][:1] + parts[1][:1]).upper()
    return name.strip()[:2].upper()


def _first_n(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        clean = name.strip() or "ร้าน"
        return clean[:n]

    return fn


def _bracket(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        return f"[{(name.strip() or 'ร้าน')[:n]}]"

    return fn


def _sparkle(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        return f"✦ {(name.strip() or 'ร้าน')[:n]}"

    return fn


def _all_caps(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        return ((name.strip() or "ร้าน")[:n]).upper()

    return fn


def _dot_shop(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        return f"{(name.strip() or 'ร้าน')[:n]}.shop"

    return fn


def _lower(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        return ((name.strip() or "ร้าน")[:n]).lower()

    return fn


# Curated pool. Each id maps to a CSS class defined in static/css/app.css.
STYLES: List[LogoStyle] = [
    LogoStyle("lt-1", "lt-1", _initial, show_dot=True),
    LogoStyle("lt-2", "lt-2", _first_n(5)),
    LogoStyle("lt-3", "lt-3", _first_n(3)),
    LogoStyle("lt-4", "lt-4", _all_caps(6)),
    LogoStyle("lt-5", "lt-5", _bracket(4)),
    LogoStyle("lt-6", "lt-6", _dot_shop(4)),
    LogoStyle("lt-7", "lt-7", _lower(7)),
    LogoStyle("lt-8", "lt-8", _sparkle(5)),
    LogoStyle("lt-9", "lt-9", _two_initials, show_dot=True),
    LogoStyle("lt-10", "lt-10", _first_n(2)),
]

VALID_STYLE_IDS = {s.id for s in STYLES}
_BY_ID = {s.id: s for s in STYLES}


def generate_logos(shop_name: str, seed: int = 0, count: int = 3) -> List[dict]:
    """Pick `count` distinct styles deterministically from STYLES.

    Same (shop_name, seed) → same trio. Bumping seed reshuffles.
    Returns a list of {"id", "css_class", "text", "show_dot"} dicts ready
    to render in the picker template.
    """
    rng = random.Random(f"{shop_name}|{seed}")
    chosen = rng.sample(STYLES, k=min(count, len(STYLES)))
    return [
        {
            "id": s.id,
            "css_class": s.css_class,
            "text": s.text_fn(shop_name),
            "show_dot": s.show_dot,
        }
        for s in chosen
    ]


def render_style(shop_name: str, style_id: str) -> dict:
    """Render a single style by id — used to re-display a previously-saved pick."""
    s = _BY_ID[style_id]
    return {
        "id": s.id,
        "css_class": s.css_class,
        "text": s.text_fn(shop_name),
        "show_dot": s.show_dot,
    }
