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


# Generic category words shop owners pad their name with — strip these so
# the logo highlights the distinctive brand. Sorted longest-first so
# "ร้านกาแฟ" matches before "ร้าน" / "กาแฟ".
_CATEGORY_PREFIXES = (
    "ก๋วยเตี๋ยว",
    "ร้านกาแฟ",
    "ร้านอาหาร",
    "ร้าน",
    "กาแฟ",
    "บ้าน",
    "ครัว",
    "ขนม",
    "เบเกอรี่",
    "ซาลอน",
    "Café",
    "Cafe",
)


def _safe_slice(s: str, n: int) -> str:
    """Slice to n chars but never strand a Thai leading vowel at the end.

    Thai leading vowels (เ แ โ ใ ไ — Unicode 0E40–0E44) are written before the
    base consonant they belong to. If a hard slice lands right after one,
    it leaves the vowel hanging visually ('ชัดเ' instead of 'ชัด'). Trim
    those trailing leading-vowels back to the previous consonant.
    """
    if n >= len(s):
        return s
    end = n
    while end > 0 and 0x0E40 <= ord(s[end - 1]) <= 0x0E44:
        end -= 1
    return s[:end]


def _brand_part(name: str) -> str:
    """Drop a category prefix so 'กาแฟชัดเจน' → 'ชัดเจน'.

    Tries word-boundary split first ('กาแฟ ชัดเจน' → 'ชัดเจน'), then
    substring strip ('กาแฟชัดเจน' → 'ชัดเจน'). Returns the original name
    if nothing reasonable is left after stripping.
    """
    cleaned = (name or "").strip() or "ร้าน"
    parts = cleaned.split()
    if len(parts) > 1 and parts[0] in _CATEGORY_PREFIXES:
        return " ".join(parts[1:])
    for prefix in _CATEGORY_PREFIXES:
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
            rest = cleaned[len(prefix):].lstrip()
            if rest:
                return rest
    return cleaned


def _initial(name: str) -> str:
    return _brand_part(name)[0].upper()


def _two_initials(name: str) -> str:
    brand = _brand_part(name)
    parts = brand.split()
    if len(parts) >= 2:
        return (parts[0][:1] + parts[1][:1]).upper()
    return _safe_slice(brand, 2).upper()


def _first_n(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        return _safe_slice(_brand_part(name), n)

    return fn


def _bracket(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        return f"[{_safe_slice(_brand_part(name), n)}]"

    return fn


def _sparkle(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        return f"✦ {_safe_slice(_brand_part(name), n)}"

    return fn


def _all_caps(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        return _safe_slice(_brand_part(name), n).upper()

    return fn


def _dot_shop(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        return f"{_safe_slice(_brand_part(name), n)}.shop"

    return fn


def _lower(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        return _safe_slice(_brand_part(name), n).lower()

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
