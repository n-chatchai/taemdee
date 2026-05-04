"""Typography-based "AI" logo generator.

Per PRD §6.E the default onboarding path is "Create with AI": the system
generates 3 logo options from the shop name across different typography styles.

This is intentionally not a raster image-gen API call — for v1 we want it
free, instant, and on-brand with the design system. Each `LogoStyle` is a
named typography variant (CSS class + a derivation rule for the display text).
The `generate_logos` function picks 3 deterministically-seeded variants from
the pool so the same `(shop_name, seed)` always renders the same trio
(important so a page refresh doesn't reshuffle the picker).

Thai shop names tokenize via pythainlp:
  - word_tokenize(engine='newmm') for word-level styles ('มัทฉะคุณเจน' →
    ['มัทฉะ', 'คุณ', 'เจน'] — recognises real Thai words from a dict so
    a 'first word' logo lands on a complete unit)
  - subword_tokenize(engine='tcc') for cluster-level slicing where we need
    finer cuts ('มัทฉะคุณเจน' → ['มั', 'ท', 'ฉะ', …]) — every cluster is
    one visible character, no stranded combining marks
"""

import random
from dataclasses import dataclass
from typing import Callable, List

from pythainlp.tokenize import subword_tokenize, word_tokenize


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
    "มัทฉะ",
    "ชาไข่มุก",
    "ร้าน",
    "กาแฟ",
    "ชา",
    "คุณ",
    "พี่",
    "น้อง",
    "ลุง",
    "ป้า",
    "น้า",
    "อา",
    "ตา",
    "ยาย",
    "บ้าน",
    "ครัว",
    "ขนม",
    "เบเกอรี่",
    "สลัด",
    "ยำ",
    "ซาลอน",
    "Café",
    "Cafe",
)


def _clusters(s: str) -> List[str]:
    """TCC (Thai Character Cluster) subword tokenize — every cluster is
    one visible character, mixed Thai/Latin handled. Used for n-cluster
    slicing where we want finer granularity than whole words."""
    return [c for c in subword_tokenize(s, engine="tcc") if c]


def _has_thai(s: str) -> bool:
    return any(0x0E00 <= ord(ch) <= 0x0E7F for ch in s)


def _words(s: str) -> List[str]:
    """Word segmentation. pythainlp's newmm engine works great for Thai
    but mangles Latin text with accents ('Café Bleu' → ['Caf', 'é',
    'Bleu']). Route pure-Latin / non-Thai input through plain whitespace
    split to keep names like 'Café' intact."""
    if not _has_thai(s):
        return [w for w in s.split() if w.strip()]
    return [w for w in word_tokenize(s, engine="newmm", keep_whitespace=False) if w.strip()]


def _safe_slice(s: str, n: int) -> str:
    """Slice to the first `n` visible (Thai-aware) clusters.

    For names with combining marks ('มัทฉะคุณเจน') this returns clean
    visual cuts — n=3 → 'มัทฉะ' (3 clusters), not 'มัท' (3 codepoints).
    Pure-Latin names behave like a normal codepoint slice."""
    return "".join(_clusters(s)[:n])


def _brand_part(name: str) -> str:
    """Drop category and honorific prefixes sequentially.
    
    'มัทฉะคุณเจน' -> 'คุณเจน' -> 'เจน'.
    """
    cleaned = (name or "").strip() or "ร้าน"
    
    while True:
        changed = False
        # Try space-separated first
        parts = cleaned.split()
        if len(parts) > 1 and parts[0] in _CATEGORY_PREFIXES:
            cleaned = " ".join(parts[1:]).strip()
            changed = True
            continue
            
        # Try substring strip
        for prefix in _CATEGORY_PREFIXES:
            if cleaned.startswith(prefix) and len(cleaned) > len(prefix):
                rest = cleaned[len(prefix):].lstrip()
                if rest:
                    cleaned = rest
                    changed = True
                    break
        
        if not changed:
            break
            
    return cleaned or name or "ร้าน"


def _first_cluster(name: str) -> str:
    """First visible cluster of the brand part — 'มั' for 'มัทฉะคุณเจน'
    (better than just 'ม' which loses the vowel)."""
    clusters = _clusters(_brand_part(name))
    return clusters[0] if clusters else _brand_part(name)[:1]


def _two_initials(name: str) -> str:
    """First cluster of each of the first two words. Falls back to the
    first two clusters when the brand is a single word."""
    words = _words(_brand_part(name))
    if len(words) >= 2:
        a = _clusters(words[0])
        b = _clusters(words[1])
        return ((a[0] if a else "") + (b[0] if b else "")).upper()
    return _safe_slice(_brand_part(name), 2).upper()


def _first_word(name: str) -> str:
    """Whole first word ('มัทฉะ' for 'มัทฉะคุณเจน'). Falls back to first
    cluster if tokenization returns nothing."""
    words = _words(_brand_part(name))
    return words[0] if words else _first_cluster(name)


def _first_two_words(name: str) -> str:
    """Up to the first two words joined ('มัทฉะคุณ' for 'มัทฉะคุณเจน').
    Single-word brands collapse to that one word."""
    words = _words(_brand_part(name))
    return "".join(words[:2]) if words else _brand_part(name)


def _all_words(name: str) -> str:
    """All words joined — same characters as `_brand_part` but routed
    through tokenize so any tokenizer-specific cleanup applies."""
    words = _words(_brand_part(name))
    return "".join(words) if words else _brand_part(name)


def _last_word(name: str) -> str:
    """The trailing word of the brand part. Useful when the *brand* is
    the proper-noun suffix and the prefix is generic ('มัทฉะคุณเจน' →
    'เจน', 'ลุงหมี' → 'หมี'). Single-word brands fall back to the whole
    word."""
    words = _words(_brand_part(name))
    return words[-1] if words else _brand_part(name)


def _last_two_words(name: str) -> str:
    """Last two words joined — captures honourific + name patterns like
    'คุณเจน', 'ลุงหมี', 'พี่นิด'. The user shouldn't have to think about
    where the brand split is, the variety in the picker covers it."""
    words = _words(_brand_part(name))
    return "".join(words[-2:]) if words else _brand_part(name)


def _first_n_clusters(n: int) -> Callable[[str], str]:
    def fn(name: str) -> str:
        return _safe_slice(_brand_part(name), n)
    return fn


def _bracket_word(name: str) -> str:
    return f"[{_first_word(name)}]"


def _sparkle_last(name: str) -> str:
    """✦ + last word — surfaces the brand suffix ('✦ เจน', '✦ หมี')."""
    return f"✦ {_last_word(name)}"


def _all_caps_full(name: str) -> str:
    return _all_words(name).upper()


def _dot_shop_word(name: str) -> str:
    return f"{_first_word(name)}.shop"


def _lower_full(name: str) -> str:
    return _all_words(name).lower()


# Curated pool. Each id maps to a CSS class defined in static/css/app.css.
# Mix word-aware (whole-syllable) with cluster slicing — Thai brands come
# out readable across the picker. Keep variety: first-word + last-word +
# full-name styles all in rotation so a 3-pick covers different framings.
STYLES: List[LogoStyle] = [
    # lt-1 was missing from the registry but its CSS class exists and
    # the shop_settings flow lets owners save it as the chosen style.
    # That left some pre-existing rows with logo_url='text:lt-1' that
    # shop_logo() rejected (returned None), so customer pages fell
    # through to a bare-initial fallback. Restored with _all_words so
    # the full shop name renders as the wordmark.
    LogoStyle("lt-1", "lt-1", _all_words, show_dot=True),
    LogoStyle("lt-2", "lt-2", _first_word),
    LogoStyle("lt-3", "lt-3", _first_two_words),
    LogoStyle("lt-4", "lt-4", _all_caps_full),
    LogoStyle("lt-5", "lt-5", _bracket_word),
    LogoStyle("lt-6", "lt-6", _dot_shop_word),
    LogoStyle("lt-7", "lt-7", _lower_full),
    LogoStyle("lt-8", "lt-8", _sparkle_last),
    LogoStyle("lt-9", "lt-9", _last_two_words, show_dot=True),
    LogoStyle("lt-10", "lt-10", _last_word),
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
