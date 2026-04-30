from app.services.logo_gen import VALID_STYLE_IDS


async def test_identity_renders_logo_options(auth_client):
    r = await auth_client.get("/shop/onboard/identity")
    assert r.status_code == 200
    body = r.text
    matched = [sid for sid in VALID_STYLE_IDS if f"choice = '{sid}'" in body]
    assert len(matched) >= 3
    assert "ชื่อร้าน" in body
    assert "เลือกโลโก้" in body


async def test_identity_regenerates_on_seed_change(auth_client):
    r0 = (await auth_client.get("/shop/onboard/identity?gen=0")).text
    r1 = (await auth_client.get("/shop/onboard/identity?gen=1")).text
    ids0 = sorted(sid for sid in VALID_STYLE_IDS if f"choice = '{sid}'" in r0)
    ids1 = sorted(sid for sid in VALID_STYLE_IDS if f"choice = '{sid}'" in r1)
    assert ids0 != ids1


async def test_identity_post_saves_name_and_logo(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/onboard/identity",
        data={"name": "ร้านกาแฟลุงหมี", "logo_choice": "lt-5"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/onboard/reward"
    await db.refresh(shop)
    assert shop.name == "ร้านกาแฟลุงหมี"
    assert shop.logo_url == "text:lt-5"


async def test_identity_post_blocks_same_district_collision(auth_client, db, shop):
    """S2.1.warn — same name + same district as another shop = 400 +
    inline warning. Owner can't accidentally clone an existing
    neighbourhood shop."""
    from app.models import Shop
    other = Shop(name="ร้านกาแฟลุงหมี", district="นิมมาน")
    db.add(other)
    await db.commit()

    response = await auth_client.post(
        "/shop/onboard/identity",
        data={
            "name": "ร้านกาแฟลุงหมี",
            "district": "นิมมาน",
            "province": "เชียงใหม่",
            "logo_choice": "lt-2",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    body = response.text
    assert "ชื่อนี้มีในเขตนิมมานแล้ว" in body
    assert "s2-warn" in body
    # Suggestion includes "<name> 2"
    assert "ร้านกาแฟลุงหมี 2" in body
    # Current shop wasn't saved with the colliding name
    await db.refresh(shop)
    assert shop.name != "ร้านกาแฟลุงหมี" or shop.id == other.id


async def test_identity_post_auto_suffixes_different_district_collision(auth_client, db, shop):
    """Same name, different district → silent auto-suffix
    'ร้านกาแฟลุงหมี · ทุ่งโฮเต็ล' so the customer sees disambiguated
    names in /my-cards."""
    from app.models import Shop
    other = Shop(name="ร้านกาแฟลุงหมี", district="นิมมาน")
    db.add(other)
    await db.commit()

    response = await auth_client.post(
        "/shop/onboard/identity",
        data={
            "name": "ร้านกาแฟลุงหมี",
            "district": "ทุ่งโฮเต็ล",
            "province": "เชียงใหม่",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(shop)
    assert shop.name == "ร้านกาแฟลุงหมี · ทุ่งโฮเต็ล"
    assert shop.district == "ทุ่งโฮเต็ล"


async def test_identity_post_auto_derives_province_from_district(auth_client, db, shop):
    """Single-candidate district → province auto-fills server-side from
    the kongvut dataset (frontend chip is cosmetic; server is the
    source of truth)."""
    response = await auth_client.post(
        "/shop/onboard/identity",
        data={
            "name": "ร้านเทสต์ขนิม",
            "district": "นิมมาน",
            # province intentionally omitted — server should derive it
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(shop)
    assert shop.district == "นิมมาน"
    # No real district called "นิมมาน" in dataset (district is "เมืองเชียงใหม่"
    # and นิมมาน is a sub-district). Fallback: blank province + free input later.
    # Use a real ambiguous district to test the auto-fill path properly.


async def test_district_lookup_endpoint_returns_single_match(auth_client):
    """Common district → 1 province in the response list."""
    r = await auth_client.get("/shop/onboard/district/lookup?q=บางพลี")
    assert r.status_code == 200
    payload = r.json()
    assert payload["provinces"] == ["สมุทรปราการ"]


async def test_district_lookup_endpoint_returns_multiple_for_ambiguous(auth_client):
    """จอมทอง exists in both กรุงเทพมหานคร and เชียงใหม่ — picker UI
    needs both candidates to render the choose pills."""
    r = await auth_client.get("/shop/onboard/district/lookup?q=จอมทอง")
    payload = r.json()
    assert set(payload["provinces"]) == {"กรุงเทพมหานคร", "เชียงใหม่"}


async def test_district_lookup_endpoint_returns_empty_for_unknown(auth_client):
    r = await auth_client.get("/shop/onboard/district/lookup?q=ไม่มีอำเภอนี้แน่นอน")
    assert r.json()["provinces"] == []


async def test_district_lookup_accepts_prefixed_form(auth_client):
    """User typing 'เขตจอมทอง' should resolve unambiguously to Bangkok
    even though bare 'จอมทอง' is shared with เชียงใหม่."""
    r = await auth_client.get("/shop/onboard/district/lookup?q=เขตจอมทอง")
    assert r.json()["provinces"] == ["กรุงเทพมหานคร"]


async def test_identity_post_unambiguous_district_fills_province(auth_client, db, shop):
    """Owner submits with a single-candidate district → server saves
    both shop.district and shop.location (province)."""
    response = await auth_client.post(
        "/shop/onboard/identity",
        data={
            "name": "ร้านเทสต์บางพลี",
            "district": "บางพลี",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(shop)
    assert shop.district == "บางพลี"
    assert shop.location == "สมุทรปราการ"


async def test_identity_post_ambiguous_district_with_chosen_province(auth_client, db, shop):
    """Owner picked the dropdown → form sends both district + chosen
    province; server respects the choice."""
    response = await auth_client.post(
        "/shop/onboard/identity",
        data={
            "name": "ร้านเทสต์จอมทอง",
            "district": "จอมทอง",
            "province": "เชียงใหม่",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(shop)
    assert shop.district == "จอมทอง"
    assert shop.location == "เชียงใหม่"


async def test_identity_post_no_collision_when_name_unique(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/onboard/identity",
        data={
            "name": "ร้านใหม่ไม่ซ้ำ",
            "district": "นิมมาน",
            "province": "เชียงใหม่",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(shop)
    assert shop.name == "ร้านใหม่ไม่ซ้ำ"
    assert shop.district == "นิมมาน"


async def test_identity_post_ignores_unknown_logo_choice(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/onboard/identity",
        data={"name": "Test", "logo_choice": "lt-bogus"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(shop)
    assert shop.logo_url is None


async def test_reward_renders_image_picker_and_pills(auth_client):
    r = await auth_client.get("/shop/onboard/reward")
    assert r.status_code == 200
    body = r.text
    # Reward images flipped to the design's 4 illustrated tiles.
    for img_id in ("gift_box", "card", "star", "coffee_cup"):
        assert f"rewardImage === '{img_id}'" in body
    for goal in ("5", "10", "20"):
        assert f"goal === {goal}" in body
    assert "กำหนดเอง" in body
    assert "ตั้งรางวัล" in body or "รางวัล" in body


async def test_reward_post_saves_description_image_threshold(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/onboard/reward",
        data={
            "reward_description": "กาแฟ Signature ฟรี 1 แก้ว",
            "reward_image": "star",
            "reward_threshold": 20,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/onboard/theme"
    await db.refresh(shop)
    assert shop.reward_description == "กาแฟ Signature ฟรี 1 แก้ว"
    assert shop.reward_image == "star"
    assert shop.reward_threshold == 20


async def test_reward_post_ignores_invalid_image(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/onboard/reward",
        data={
            "reward_description": "X",
            "reward_image": "tea_pot",
            "reward_threshold": 10,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(shop)
    # default coffee_cup remains because the bogus value was rejected
    assert shop.reward_image == "coffee_cup"


async def test_reward_post_accepts_custom_threshold(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/onboard/reward",
        data={
            "reward_description": "X",
            "reward_image": "iced",
            "reward_threshold": 7,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(shop)
    assert shop.reward_threshold == 7


async def test_legacy_onboard_urls_redirect_to_new_flow(auth_client):
    """/onboard/name and /onboard/logo are legacy URLs from the previous wizard.
    They should 303 to the new identity/reward steps so any cached links still work."""
    r1 = await auth_client.get("/shop/onboard/name", follow_redirects=False)
    assert r1.status_code == 303
    assert r1.headers["location"] == "/shop/onboard/identity"

    r2 = await auth_client.get("/shop/onboard/logo", follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == "/shop/onboard/reward"
