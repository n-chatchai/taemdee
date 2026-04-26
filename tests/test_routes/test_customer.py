from uuid import uuid4

from sqlmodel import select

from app.models import Point


async def test_scan_creates_stamp_and_redirects(client, shop):
    response = await client.get(f"/scan/{shop.id}", follow_redirects=False)
    assert response.status_code == 303
    # ?stamped=1 triggers the celebration overlay on the card view
    assert response.headers["location"] == f"/card/{shop.id}?stamped=1"


async def test_scan_persists_stamp(client, db, shop):
    await client.get(f"/scan/{shop.id}", follow_redirects=False)
    result = await db.exec(select(Point).where(Point.shop_id == shop.id))
    stamps = list(result.all())
    assert len(stamps) == 1
    assert stamps[0].issuance_method == "customer_scan"


async def test_scan_twice_with_default_cooldown_creates_two_stamps(client, db, shop):
    """Default config (scan_cooldown_minutes=0) means every scan succeeds.
    Cooldown protection is opt-in per shop."""
    await client.get(f"/scan/{shop.id}", follow_redirects=False)
    response = await client.get(f"/scan/{shop.id}", follow_redirects=False)
    assert response.status_code == 303
    result = await db.exec(select(Point).where(Point.shop_id == shop.id))
    assert len(list(result.all())) == 2


async def test_scan_blocked_silently_when_cooldown_set(client, db, shop):
    """When the shop sets scan_cooldown_minutes>0, a re-scan within that window
    is silently swallowed — the route still 303s back to the card so the customer
    sees their existing count, no error message surfaces."""
    shop.scan_cooldown_minutes = 60
    db.add(shop)
    await db.commit()

    await client.get(f"/scan/{shop.id}", follow_redirects=False)
    response = await client.get(f"/scan/{shop.id}", follow_redirects=False)
    assert response.status_code == 303
    result = await db.exec(select(Point).where(Point.shop_id == shop.id))
    assert len(list(result.all())) == 1


async def test_card_renders(client, shop):
    await client.get(f"/scan/{shop.id}", follow_redirects=False)
    response = await client.get(f"/card/{shop.id}")
    assert response.status_code == 200
    assert shop.name in response.text


async def test_card_renders_celebration_with_stamped_flag(client, shop):
    """?stamped=1 triggers the one-shot scan celebration overlay."""
    response = await client.get(f"/card/{shop.id}?stamped=1")
    assert response.status_code == 200
    body = response.text
    assert 'class="scan-cel"' in body
    assert "scan-cel-badge" in body


async def test_card_no_celebration_without_stamped_flag(client, shop):
    """Plain card view (no ?stamped=1) does NOT include the overlay."""
    response = await client.get(f"/card/{shop.id}")
    assert response.status_code == 200
    assert 'class="scan-cel"' not in response.text


async def test_scan_publishes_stamp_toast_event(client, db, shop, monkeypatch):
    """Scan should fire BOTH the feed-row event (existing) and a stamp-toast
    event (new S6) so the DeeBoard can pop the live toast."""
    from app.routes import customer as customer_routes

    received = []

    def fake_publish(shop_id, event_name, html):
        received.append((event_name, html))

    monkeypatch.setattr(customer_routes, "publish", fake_publish)

    response = await client.get(f"/scan/{shop.id}", follow_redirects=False)
    assert response.status_code == 303

    event_names = [name for name, _ in received]
    assert "feed-row" in event_names
    assert "point-toast" in event_names
    toast_html = next(html for name, html in received if name == "point-toast")
    assert "s6-toast" in toast_html
    assert "ออกแต้มสำเร็จ" in toast_html
    assert "ms just-now" in toast_html


async def test_card_celebration_on_first_visit(client, shop):
    """First visit gets BOTH the C2 banner (contextual "your first stamp here")
    AND the scan-cel confetti overlay — the most special stamp deserves the
    same celebration as later ones, not less."""
    # Single scan ⇒ point_count==1 ⇒ is_first_visit==True
    response = await client.get(f"/scan/{shop.id}", follow_redirects=True)
    assert response.status_code == 200
    body = response.text
    assert "c2-celebration" in body
    assert 'class="scan-cel"' in body


async def test_scan_unknown_shop_redirects_to_friendly_card_404(client):
    """Scanning a QR for a deleted shop forwards to /card/{id}, which renders
    the Thai "ไม่พบร้านนี้" page (404) — not a JSON dead-end."""
    bogus = uuid4()
    response = await client.get(f"/scan/{bogus}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/card/{bogus}"


async def test_card_unknown_shop_renders_friendly_page(client):
    bogus = uuid4()
    response = await client.get(f"/card/{bogus}")
    assert response.status_code == 404
    body = response.text
    assert "ไม่พบ" in body  # Thai "not found" copy
    assert "shop_not_found" not in body or "ไม่พบร้านนี้" in body or "ร้านนี้" in body
    assert "/my-cards" in body  # CTA to my-cards is present
    assert str(bogus) in body  # debug shop_id rendered


async def test_claim_phone_with_valid_otp(client, db, shop):
    # Get an OTP issued
    await client.post("/auth/otp/request", data={"phone": "0833333333"})
    from app.models import OtpCode
    result = await db.exec(select(OtpCode).where(OtpCode.phone == "0833333333"))
    otp = result.first()

    # Visit card first so the customer cookie is set
    await client.get(f"/scan/{shop.id}", follow_redirects=False)

    response = await client.post(
        "/card/claim/phone",
        data={"phone": "0833333333", "code": otp.code, "display_name": "Ann"},
    )
    assert response.status_code == 200
    assert response.json()["claimed"] is True


async def test_account_anonymous_redirects_to_claim(client):
    response = await client.get("/card/account", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/card/save"


async def test_account_renders_for_claimed_customer(client, db, shop):
    await client.post("/auth/otp/request", data={"phone": "0844444444"})
    from app.models import OtpCode
    otp = (await db.exec(select(OtpCode).where(OtpCode.phone == "0844444444"))).first()
    await client.get(f"/scan/{shop.id}", follow_redirects=False)
    await client.post(
        "/card/claim/phone",
        data={"phone": "0844444444", "code": otp.code, "display_name": "Ploy"},
    )

    response = await client.get("/card/account")
    assert response.status_code == 200
    body = response.text
    assert "บัญชีของฉัน" in body
    assert "Ploy" in body
    # last 4 digits of phone shown, middle masked
    assert "4444" in body
    assert "ออกจากระบบ" in body


async def test_account_logout_clears_cookie(client, db, shop):
    await client.post("/auth/otp/request", data={"phone": "0855555555"})
    from app.models import OtpCode
    otp = (await db.exec(select(OtpCode).where(OtpCode.phone == "0855555555"))).first()
    await client.get(f"/scan/{shop.id}", follow_redirects=False)
    await client.post(
        "/card/claim/phone",
        data={"phone": "0855555555", "code": otp.code},
    )

    response = await client.post("/card/account/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    # Logout clears the cookie — next /card/account should redirect to /card/save again
    follow = await client.get("/card/account", follow_redirects=False)
    assert follow.status_code == 303
    assert follow.headers["location"] == "/card/save"
