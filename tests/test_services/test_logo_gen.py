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
    """pythainlp recognises 'มัทฉะ' (matcha) so the first-word style returns
    that whole syllable instead of stranding mid-cluster."""
    rendered = render_style("มัทฉะคุณเจน", "lt-2")
    assert rendered["text"] == "มัทฉะ"


def test_thai_first_two_words():
    rendered = render_style("มัทฉะคุณเจน", "lt-3")
    assert rendered["text"] == "มัทฉะคุณ"


def test_thai_first_cluster_keeps_combining_mark():
    """First cluster of 'มัทฉะคุณเจน' is 'มั' (the leading consonant +
    its วรรณยุกต์ vowel) — never just 'ม' which would lose the vowel."""
    rendered = render_style("มัทฉะคุณเจน", "lt-1")
    assert rendered["text"] == "มั"
    assert rendered["show_dot"] is True


def test_thai_category_prefix_stripped_then_word_split():
    """ร้านกาแฟลุงหมี → strip ร้านกาแฟ → ลุงหมี → first word 'ลุง'."""
    rendered = render_style("ร้านกาแฟลุงหมี", "lt-2")
    assert rendered["text"] == "ลุง"


def test_thai_last_word_surfaces_brand_suffix():
    """For honourific+name brands ('คุณเจน', 'ลุงหมี') the trailing word
    is the actual proper-noun. lt-10 (last word) and lt-9 (last 2)
    surface that so the picker isn't stuck on the generic prefix."""
    assert render_style("มัทฉะคุณเจน", "lt-10")["text"] == "เจน"
    assert render_style("มัทฉะคุณเจน", "lt-9")["text"] == "คุณเจน"
    assert render_style("ร้านกาแฟลุงหมี", "lt-10")["text"] == "หมี"


def test_latin_word_split_via_whitespace():
    """pythainlp's newmm mangles accented Latin (Café → Caf+é); pure-Latin
    input must route through whitespace split to keep words intact."""
    rendered = render_style("Hello World", "lt-2")
    assert rendered["text"] == "Hello"
    rendered2 = render_style("Hello World", "lt-10")
    assert rendered2["text"] == "World"
