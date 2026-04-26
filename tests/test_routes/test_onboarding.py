from app.services.logo_gen import VALID_STYLE_IDS


async def test_logo_picker_renders_three_options(auth_client):
    r = await auth_client.get("/shop/onboard/logo")
    assert r.status_code == 200
    body = r.text
    matched = [sid for sid in VALID_STYLE_IDS if f"choice = '{sid}'" in body]
    assert len(matched) >= 3


async def test_logo_picker_regenerates_on_seed_change(auth_client):
    r0 = (await auth_client.get("/shop/onboard/logo?gen=0")).text
    r1 = (await auth_client.get("/shop/onboard/logo?gen=1")).text
    ids0 = sorted(sid for sid in VALID_STYLE_IDS if f"choice = '{sid}'" in r0)
    ids1 = sorted(sid for sid in VALID_STYLE_IDS if f"choice = '{sid}'" in r1)
    assert ids0 != ids1


async def test_logo_post_saves_choice(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/onboard/logo",
        data={"logo_choice": "lt-5"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/onboard/theme"
    await db.refresh(shop)
    assert shop.logo_url == "text:lt-5"


async def test_logo_post_ignores_unknown_choice(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/onboard/logo",
        data={"logo_choice": "lt-bogus"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(shop)
    assert shop.logo_url is None
