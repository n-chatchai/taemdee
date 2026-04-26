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
