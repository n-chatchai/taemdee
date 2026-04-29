from uuid import uuid4

from sqlmodel import select

from app.models import Point


async def test_scan_first_time_redirects_to_onboard(client, shop):
    """First-ever scan (display_name still NULL) → C2 onboard 3-step flow.
    Returners with display_name set fall through to the regular card view."""
    response = await client.get(f"/scan/{shop.id}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/onboard/{shop.id}"


async def test_scan_returner_redirects_to_card_with_celebration(client, shop):
    """After C2 onboard saves a nickname, subsequent scans go back to /card?stamped=1
    for the regular celebration overlay (no re-onboarding)."""
    # First scan + onboard nickname submission claims the customer name
    await client.get(f"/scan/{shop.id}", follow_redirects=False)
    await client.post("/card/nickname", data={"name": "พี่หมี"})
    response = await client.get(f"/scan/{shop.id}", follow_redirects=False)
    assert response.status_code == 303
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


async def test_scan_publishes_feed_row_event(client, db, shop, monkeypatch):
    """Scan publishes a feed-row event so the DeeBoard dock prepends a new row.
    Tapping that row in the dock opens the S3.detail bottom sheet, which
    fetches /shop/feed/point/<id> for full activity meta + the void button."""
    from app.routes import customer as customer_routes

    received = []

    def fake_publish(shop_id, event_name, html):
        received.append((event_name, html))

    monkeypatch.setattr(customer_routes, "publish", fake_publish)

    response = await client.get(f"/scan/{shop.id}", follow_redirects=False)
    assert response.status_code == 303

    event_names = [name for name, _ in received]
    assert "feed-row" in event_names
    row_html = next(html for name, html in received if name == "feed-row")
    assert 'class="feed-row"' in row_html
    assert "data-detail-url" in row_html


async def test_onboard_renders_for_first_time_guest(client, shop):
    """First-time scan lands on /onboard which renders the 3-step Alpine flow.
    Step 1 has the greeting + nickname input; steps 2/3 are x-show'd by the
    Alpine state machine."""
    response = await client.get(f"/scan/{shop.id}", follow_redirects=True)
    assert response.status_code == 200
    body = response.text
    # 3-step dots wired up
    assert "ob-step-dots" in body
    # Step 1 greeting + nickname prompt
    assert "ผมเรียกพี่ว่าอะไรดี" in body
    # Step 3 signup pills are in the markup (Alpine x-show toggles visibility)
    assert "สมัครด้วยไลน์" in body
    assert "สมัครด้วยเบอร์โทร" in body


async def test_full_card_gates_redemption_for_guests(client, db, shop):
    """Guest with a full card sees the signup gate, not the redeem form —
    revised C4: signup is required before redemption (anti-fraud + lets the
    shop contact the customer about the reward)."""
    from app.models import Customer, Point

    # Seed a full card via DB to skip the cooldown / scan-loop machinery.
    await client.get(f"/scan/{shop.id}", follow_redirects=False)
    customer = (await db.exec(select(Customer))).first()
    for _ in range(shop.reward_threshold - 1):
        db.add(Point(shop_id=shop.id, customer_id=customer.id, issuance_method="customer_scan"))
    await db.commit()

    response = await client.get(f"/card/{shop.id}")
    assert response.status_code == 200
    body = response.text
    # Gate copy + signup-opening CTA, NOT the bare redeem form
    assert "สมัครก่อนรับรางวัล" in body
    assert "สมัครรับรางวัล" in body
    assert 'data-open="signup-picker"' in body
    assert "/redeem" not in body  # no plain redeem form for guests


async def test_redeem_post_rejected_for_anonymous(client, db, shop):
    """Even if a guest POSTs /redeem directly (bypassing the gated UI), the
    server enforces the same membership rule — 403 with informative copy."""
    from app.models import Customer, Point

    await client.get(f"/scan/{shop.id}", follow_redirects=False)
    customer = (await db.exec(select(Customer))).first()
    for _ in range(shop.reward_threshold - 1):
        db.add(Point(shop_id=shop.id, customer_id=customer.id, issuance_method="customer_scan"))
    await db.commit()

    response = await client.post(f"/card/{shop.id}/redeem", follow_redirects=False)
    assert response.status_code == 403
    assert "สมัครก่อนรับรางวัล" in response.json()["detail"]


async def test_scan_unknown_shop_redirects_to_friendly_card_404(client):
    """Scanning a QR for a deleted shop forwards to /card/{id}, which renders
    the Thai "ไม่พบร้านนี้" page (404) — not a JSON dead-end."""
    bogus = uuid4()
    response = await client.get(f"/scan/{bogus}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/card/{bogus}"


async def test_my_cards_renders_for_guest_without_banner(client, shop):
    """Per the latest C7 design, the guest-only upgrade banner is gone
    (Option A — respect customer choice). The card list still renders and
    signup_picker stays mounted in case any CTA wants to open it."""
    # /scan creates an anonymous customer cookie + 1 point at this shop
    await client.get(f"/scan/{shop.id}", follow_redirects=True)

    response = await client.get("/my-cards", follow_redirects=False)
    assert response.status_code == 200
    body = response.text
    # The user's one card is rendered
    assert shop.name in body
    # Guest banner removed; picker still wired in for other CTAs
    assert "guest-banner-bottom" not in body
    assert 'id="signup-picker"' in body
    # No claimed-only avatar shortcut (guests don't have an account page)
    assert 'href="/card/account"' not in body


async def test_my_cards_shows_unread_dot_only_for_shops_with_pending_inbox(
    client, db, shop
):
    """C7 per-shop unread indicator: a c7-card-unread dot renders only on
    cards whose shop has at least one Inbox row with read_at IS NULL for
    this customer."""
    from app.models import Customer, Inbox, Shop

    # Customer needs cards at two shops to make the per-shop scoping testable.
    other = Shop(name="Other Shop", reward_threshold=10)
    db.add(other)
    await db.commit()
    await db.refresh(other)

    await client.get(f"/scan/{shop.id}", follow_redirects=True)
    await client.get(f"/scan/{other.id}", follow_redirects=True)

    customer = (await db.exec(select(Customer))).first()
    # Unread message at `shop`, no message at `other`.
    db.add(Inbox(customer_id=customer.id, shop_id=shop.id, body="ใหม่!"))
    await db.commit()

    body = (await client.get("/my-cards")).text
    assert "c7-card-unread" in body
    # And only once — `other` has no unread, so the dot should NOT appear twice.
    assert body.count("c7-card-unread") == 1


async def test_my_cards_no_dot_when_message_already_read(client, db, shop):
    from app.models import Customer, Inbox

    await client.get(f"/scan/{shop.id}", follow_redirects=True)
    customer = (await db.exec(select(Customer))).first()

    from app.models.util import utcnow
    db.add(Inbox(
        customer_id=customer.id, shop_id=shop.id,
        body="อ่านแล้ว", read_at=utcnow(),
    ))
    await db.commit()

    body = (await client.get("/my-cards")).text
    assert "c7-card-unread" not in body


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
    assert "บัญชีของพี่" in body
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


# ---------------------------------------------------------------------------
# C2.4 recovery code + /recover lookup
# ---------------------------------------------------------------------------


async def test_onboard_recovery_renders_and_persists_code(client, db, shop):
    """C2.4 — first GET issues a code on the customer row; second GET shows
    the SAME code (idempotent generator)."""
    from app.models import Customer

    r1 = await client.get(f"/onboard/{shop.id}/recovery")
    assert r1.status_code == 200
    body = r1.text
    assert "รหัสกู้คืน" in body or "ไม่สะดวกสมัคร" in body

    customer = (await db.exec(select(Customer))).first()
    assert customer.recovery_code is not None
    code = customer.recovery_code
    assert len(code) == 14 and code.count("-") == 2  # XXXX-XXXX-XXXX

    r2 = await client.get(f"/onboard/{shop.id}/recovery")
    assert code in r2.text


async def test_onboard_skip_link_targets_recovery(client, shop):
    """C2.3 'ขอบคุณแต่ยังก่อน' must point to /onboard/{shop}/recovery so
    the customer is offered a recovery code before they leave."""
    r = await client.get(f"/scan/{shop.id}", follow_redirects=True)
    assert r.status_code == 200
    assert f"/onboard/{shop.id}/recovery" in r.text


async def test_recover_swaps_cookie_to_owner_of_code(client, db, shop):
    from app.core.auth import CUSTOMER_COOKIE_NAME, decode_customer_token
    from app.models import Customer

    # Seed: customer A gets a recovery code by visiting the onboarding step.
    await client.get(f"/onboard/{shop.id}/recovery")
    a = (await db.exec(select(Customer))).first()
    code = a.recovery_code

    # Drop A's cookie — simulate "device lost / cleared cookies".
    client.cookies.clear()

    # POST /recover with the code → 303 to /my-cards + Set-Cookie pointing
    # back at customer A.
    r = await client.post("/recover", data={"code": code}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/my-cards"

    set_cookie = r.headers.get("set-cookie", "")
    assert CUSTOMER_COOKIE_NAME in set_cookie
    token = client.cookies.get(CUSTOMER_COOKIE_NAME)
    assert decode_customer_token(token) == a.id


async def test_recover_normalizes_lowercase_no_hyphens(client, db, shop):
    """User can paste 'k7mxq4p2h9rs' (lowercase, no hyphens) and we still
    look up the canonical form."""
    from app.models import Customer

    await client.get(f"/onboard/{shop.id}/recovery")
    a = (await db.exec(select(Customer))).first()
    raw = a.recovery_code.replace("-", "").lower()

    client.cookies.clear()
    r = await client.post("/recover", data={"code": raw}, follow_redirects=False)
    assert r.status_code == 303


async def test_recover_unknown_code_returns_400_with_error(client):
    r = await client.post("/recover", data={"code": "ZZZZ-ZZZZ-ZZZZ"}, follow_redirects=False)
    assert r.status_code == 400
    assert "ไม่พบรหัส" in r.text
