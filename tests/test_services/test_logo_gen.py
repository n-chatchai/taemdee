from app.services.logo_gen import STYLES, VALID_STYLE_IDS, generate_logos, render_style


def test_generate_returns_three_distinct():
    options = generate_logos("Café Tana", seed=0)
    assert len(options) == 3
    ids = {o["id"] for o in options}
    assert len(ids) == 3, "options should be distinct"
    assert ids.issubset(VALID_STYLE_IDS)


def test_seed_is_deterministic():
    a = generate_logos("Café Tana", seed=0)
    b = generate_logos("Café Tana", seed=0)
    assert [o["id"] for o in a] == [o["id"] for o in b]


def test_different_seeds_yield_different_sets():
    a = generate_logos("Café Tana", seed=0)
    b = generate_logos("Café Tana", seed=1)
    assert [o["id"] for o in a] != [o["id"] for o in b]


def test_text_derives_from_shop_name():
    options = generate_logos("Brewmaster", seed=0)
    for opt in options:
        assert opt["text"], f"style {opt['id']} produced empty text"
        # Should contain at least one character from the shop name (case-insensitive)
        assert any(ch.lower() in opt["text"].lower() for ch in "Brewmaster"), opt


def test_blank_name_falls_back_to_default():
    options = generate_logos("", seed=0)
    for opt in options:
        assert opt["text"]


def test_render_style_for_saved_pick():
    rendered = render_style("Café Tana", STYLES[0].id)
    assert rendered["id"] == STYLES[0].id
    assert rendered["text"]


def test_thai_word_aware_first_word():
    """Both category prefix ('มัทฉะ') and honorific ('คุณ') are stripped
    recursively by _brand_part — 'มัทฉะคุณเจน' collapses to just 'เจน',
    so first-word renders as 'เจน'."""
    rendered = render_style("มัทฉะคุณเจน", "lt-2")
    assert rendered["text"] == "เจน"


def test_thai_first_two_words():
    """Same recursive strip leaves only 'เจน', so first-two-words also = 'เจน'."""
    rendered = render_style("มัทฉะคุณเจน", "lt-3")
    assert rendered["text"] == "เจน"


def test_thai_category_prefix_stripped_then_word_split():
    """ร้านกาแฟลุงหมี → strip ร้าน + กาแฟ + ลุง → 'หมี' (recursive prefix
    strip now also includes honorifics like ลุง/ป้า/คุณ)."""
    rendered = render_style("ร้านกาแฟลุงหมี", "lt-2")
    assert rendered["text"] == "หมี"


def test_thai_last_word_surfaces_brand_suffix():
    """After recursive prefix strip, both 'มัทฉะคุณเจน' and 'ร้านกาแฟลุงหมี'
    collapse to a single word, so last-word + last-two-words land on the
    same proper-noun the picker is meant to surface."""
    assert render_style("มัทฉะคุณเจน", "lt-10")["text"] == "เจน"
    assert render_style("มัทฉะคุณเจน", "lt-9")["text"] == "เจน"
    assert render_style("ร้านกาแฟลุงหมี", "lt-10")["text"] == "หมี"


def test_latin_word_split_via_whitespace():
    """pythainlp's newmm mangles accented Latin (Café → Caf+é); pure-Latin
    input must route through whitespace split to keep words intact."""
    rendered = render_style("Hello World", "lt-2")
    assert rendered["text"] == "Hello"
    rendered2 = render_style("Hello World", "lt-10")
    assert rendered2["text"] == "World"
