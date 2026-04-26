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
    for img_id in ("coffee_cup", "latte_art", "iced"):
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
            "reward_image": "latte_art",
            "reward_threshold": 20,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/onboard/theme"
    await db.refresh(shop)
    assert shop.reward_description == "กาแฟ Signature ฟรี 1 แก้ว"
    assert shop.reward_image == "latte_art"
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
