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


async def test_card_renders(named_client, shop):
    await named_client.get(f"/scan/{shop.id}", follow_redirects=False)
    response = await named_client.get(f"/card/{shop.id}")
    assert response.status_code == 200
    assert shop.name in response.text


async def test_card_renders_celebration_with_stamped_flag(named_client, shop):
    """?stamped=1 triggers the one-shot scan celebration overlay."""
    response = await named_client.get(f"/card/{shop.id}?stamped=1")
    assert response.status_code == 200
    body = response.text
    assert 'class="scan-cel"' in body
    assert "scan-cel-badge" in body


async def test_card_no_celebration_without_stamped_flag(named_client, shop):
    """Plain card view (no ?stamped=1) does NOT include the overlay."""
    response = await named_client.get(f"/card/{shop.id}")
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
    # Step 3 social pills are in the markup (Alpine x-show toggles visibility).
    # The new onboard.link_account row is 4 options (LINE / Google /
    # Facebook / phone) each routing to its provider start endpoint.
    assert "/auth/line/customer/start" in body
    assert "/auth/google/customer/start" in body
    assert "/auth/facebook/customer/start" in body
    assert "/card/save" in body  # phone OTP path


async def test_full_card_gates_redemption_for_guests(named_client, db, shop):
    """Guest with a full card sees the signup gate, not the redeem form —
    revised C4: signup is required before redemption (anti-fraud + lets the
    shop contact the customer about the reward)."""
    from app.models import Customer, Point

    # Seed a full card via DB to skip the cooldown / scan-loop machinery.
    await named_client.get(f"/scan/{shop.id}", follow_redirects=False)
    customer = (await db.exec(select(Customer).where(Customer.display_name == "พี่เทส"))).first()
    for _ in range(shop.reward_threshold - 1):
        db.add(Point(shop_id=shop.id, customer_id=customer.id, issuance_method="customer_scan"))
    await db.commit()

    response = await named_client.get(f"/card/{shop.id}")
    assert response.status_code == 200
    body = response.text
    # Gate copy + signup-opening CTA, NOT the bare redeem form. Wording
    # updated per the May 1 design pass — "สมัครสมาชิก" → "ผูกบัญชี".
    assert "ผูกบัญชีก่อนรับรางวัล" in body
    assert "ผูกบัญชีรับรางวัล" in body
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
    assert "ผูกบัญชีก่อนรับรางวัล" in response.json()["detail"]


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
    # No claimed-only avatar shortcut in the page-head (guests don't have an
    # account page). The dock now also points at /card/account, which for
    # guests redirects to /card/save — that link is fine. We only check the
    # specific page-head avatar pattern is absent.
    assert 'class="avatar"' not in body


async def test_my_cards_renders_hero_for_ready_card(client, db, shop):
    """cards.list — single ready (≥ threshold) card renders as a
    .cl-hero gradient tile with the "รับรางวัลตอนนี้" CTA, not a row in
    the regular list."""
    from app.models import Customer, Point

    await client.get(f"/scan/{shop.id}", follow_redirects=True)
    customer = (await db.exec(select(Customer))).first()
    for _ in range(shop.reward_threshold - 1):  # already 1 from /scan
        db.add(Point(shop_id=shop.id, customer_id=customer.id, issuance_method="customer_scan"))
    await db.commit()

    body = (await client.get("/my-cards")).text
    assert "cl-hero" in body
    assert "รับรางวัลตอนนี้" in body
    assert "ครบแล้ว · พร้อมรับรางวัล" in body


async def test_my_cards_renders_carousel_for_near_complete_cards(client, db, shop):
    """Cards with ratio ≥ 0.5 land in the .cl-carousel ("ใกล้แล้ว")
    section, not the compact .cl-other list below it."""
    from app.models import Customer, Point

    await client.get(f"/scan/{shop.id}", follow_redirects=True)
    customer = (await db.exec(select(Customer))).first()
    # Ratio = 6/10 → 0.6 → near bucket.
    for _ in range(5):
        db.add(Point(shop_id=shop.id, customer_id=customer.id, issuance_method="customer_scan"))
    await db.commit()

    body = (await client.get("/my-cards")).text
    assert "cl-carousel" in body
    assert "cl-mini" in body
    assert "ใกล้แล้ว" in body


async def test_shop_story_renders_thanks_and_story_when_set(client, db, shop):
    """shop.story — Robinhood-style page surfaces the cover headline
    (drawn from thanks_message) plus the story section when story_text
    is populated."""
    shop.thanks_message = "ดีใจที่กลับมาทุกครั้ง"
    shop.story_text = "เริ่มจากความหลงใหลในกาแฟดอยช้าง — เริ่มเปิดที่บ้านก่อนย้ายมานิมมาน"
    db.add(shop)
    await db.commit()

    r = await client.get(f"/story/{shop.id}")
    assert r.status_code == 200
    body = r.text
    assert "ดีใจที่กลับมาทุกครั้ง" in body  # cover headline
    assert "ความหลงใหลในกาแฟดอยช้าง" in body  # story body
    assert "ss-cover" in body
    assert "เรื่องราวร้าน" in body


async def test_shop_story_omits_story_section_when_empty(client, shop):
    """No story_text → the entire เรื่องราวร้าน block is hidden, the
    cover + shop card still render. Cover headline falls back to the
    shop name."""
    r = await client.get(f"/story/{shop.id}")
    assert r.status_code == 200
    body = r.text
    assert "ss-cover" in body
    assert shop.name in body
    # Section header must be absent without story content backing it.
    assert "เรื่องราวร้าน" not in body


async def test_shop_story_cover_eyebrow_uses_buddhist_year(client, shop):
    """Cover eyebrow reads 'ตั้งแต่ปี <BE>' — derived from shop.created_at,
    bumped by 543 to match Thai calendar convention."""
    body = (await client.get(f"/story/{shop.id}")).text
    expected_year = shop.created_at.year + 543
    assert f"ตั้งแต่ปี {expected_year}" in body


async def test_shop_story_unknown_shop_404s(client):
    bogus = uuid4()
    r = await client.get(f"/story/{bogus}")
    assert r.status_code == 404


async def test_card_shop_head_links_to_story(named_client, shop):
    """C1 daily card — wordmark/shop-head is now a link to /story/{id}
    so customers can tap to learn about the shop (per design)."""
    body = (await named_client.get(f"/card/{shop.id}")).text
    assert f'href="/story/{shop.id}"' in body


async def test_dock_inbox_badge_lights_up_on_every_dock_page_when_unread(client, db, shop):
    """The ข้อความ tab on the customer dock must show the gn-badge dot on
    every page that mounts the dock — not only on /my-cards. Customer
    needs to see "you have a message" no matter where in the app they are."""
    from app.models import Customer, Inbox
    customer = Customer(is_anonymous=False, display_name="พี่สมศรี", phone="0855555555")
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    db.add(Inbox(customer_id=customer.id, shop_id=shop.id, body="ข้อความใหม่"))
    await db.commit()

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(customer.id))

    # Each of these pages mounts c_dock — badge should appear on every one
    for url in [
        "/my-cards",
        f"/card/{shop.id}",
        "/my-inbox",
        f"/story/{shop.id}",
        "/card/account",
        "/card/account/notifications",
        "/my-id",
    ]:
        body = (await client.get(url)).text
        assert "gn-badge" in body, f"missing dock badge on {url}"


async def test_customer_dock_renders_on_main_pages(named_client, shop):
    """Customer 4-tab dock (c-glass-nav) is mounted on every main customer
    page per design. Onboarding screens (C2/C3) don't have it."""
    # Card view (C1)
    body = (await named_client.get(f"/card/{shop.id}")).text
    assert "c-glass-nav" in body
    assert 'href="/my-cards"' in body and 'href="/my-inbox"' in body
    # /my-cards (C7)
    assert "c-glass-nav" in (await named_client.get("/my-cards")).text
    # /my-inbox (Inbox)
    assert "c-glass-nav" in (await named_client.get("/my-inbox")).text
    # /story/{id} (C9)
    assert "c-glass-nav" in (await named_client.get(f"/story/{shop.id}")).text
    # Onboarding C3 (Soft Wall) does NOT have the dock
    assert "c-glass-nav" not in (await named_client.get("/card/save")).text


async def test_dock_has_4_tabs_with_gifts_replacing_settings(client):
    """Apr 30 design refresh: settings tab is gone from the dock,
    replaced by ของขวัญ → /my-gifts. Settings is reachable via the gear
    icon in /my-cards page-head instead."""
    body = (await client.get("/my-cards")).text
    assert 'href="/my-gifts"' in body
    assert 'aria-label="ของขวัญ"' in body
    # Settings tab no longer exists in the dock
    assert 'aria-label="ตั้งค่า"' in body  # gear icon in page-head still uses this label
    # But it must NOT be a dock tab — check no gn-tab carries that label
    import re
    dock_tabs = re.findall(r'class="gn-tab[^"]*"\s+href="[^"]*"\s+aria-label="([^"]+)"', body)
    assert "ตั้งค่า" not in dock_tabs


async def test_my_cards_page_head_has_gear_icon_to_settings(client):
    body = (await client.get("/my-cards")).text
    # Gear icon in page-head ph-actions targets /card/account
    assert 'href="/card/account" class="ph-icon-btn"' in body


async def test_my_gifts_renders_empty_state(client):
    body = (await client.get("/my-gifts")).text
    assert "ของขวัญของพี่" in body
    # Empty state copy
    assert "ยังไม่มีของขวัญ" in body
    # Dock mounted with gifts tab active
    assert "c-glass-nav" in body


async def test_my_gifts_lists_active_voucher_with_use_form(client, db, shop):
    """An unserved Redemption is the customer's active voucher → it
    appears in 'พร้อมใช้' with the 'ใช้' button submitting to
    /voucher/<id>/use (May 1 trust-based activation flow)."""
    from app.models import Customer, Redemption
    c = Customer(is_anonymous=False, display_name="พี่ลูก", phone="0811111111")
    db.add(c)
    await db.commit()
    await db.refresh(c)
    r = Redemption(customer_id=c.id, shop_id=shop.id)  # served_at NULL = active
    db.add(r)
    await db.commit()
    await db.refresh(r)

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    body = (await client.get("/my-gifts")).text
    assert "พร้อมใช้" in body
    assert shop.reward_description in body
    assert shop.name in body
    assert f'/voucher/{r.id}/use' in body
    assert 'method="post"' in body


async def test_my_gifts_lists_used_voucher_in_used_section(client, db, shop):
    """A served Redemption shows up in 'ใช้แล้ว' (greyed) with no use link."""
    from app.models import Customer, Redemption
    from app.models.util import utcnow
    c = Customer(is_anonymous=False, display_name="พี่หมี", phone="0822222222")
    db.add(c)
    await db.commit()
    await db.refresh(c)
    r = Redemption(customer_id=c.id, shop_id=shop.id, served_at=utcnow())
    db.add(r)
    await db.commit()

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    body = (await client.get("/my-gifts")).text
    assert "ใช้แล้ว" in body
    assert "gift-card used" in body
    # No "use" CTA for used rows
    assert body.count('class="gc-cta"') == 0
    # Mint check tag instead of CTA
    assert "gc-used-tag" in body


async def test_voucher_use_post_marks_served_and_redirects(client, db, shop):
    """voucher.use trust-based: POST stamps served_at on the redemption
    immediately and 303s to GET /voucher/<id> for the fullscreen QR
    customer shows to staff."""
    from app.models import Customer, Redemption
    c = Customer(is_anonymous=False, display_name="พี่ส้ม", phone="0833333333")
    db.add(c)
    await db.commit()
    await db.refresh(c)
    r = Redemption(customer_id=c.id, shop_id=shop.id)
    db.add(r)
    await db.commit()
    await db.refresh(r)
    assert r.served_at is None

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    response = await client.post(f"/voucher/{r.id}/use", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/voucher/{r.id}"

    db.expire_all()
    await db.refresh(r)
    assert r.served_at is not None


async def test_voucher_use_post_is_idempotent(client, db, shop):
    """A second tap on 'ใช้' must not move served_at — once stamped, the
    voucher's used-at is the source of truth for the 5-min countdown."""
    from datetime import timedelta
    from app.models import Customer, Redemption
    from app.models.util import utcnow

    c = Customer(is_anonymous=False, display_name="พี่กล้อง", phone="0844444445")
    db.add(c)
    await db.commit()
    await db.refresh(c)
    earlier = utcnow() - timedelta(minutes=2)
    r = Redemption(customer_id=c.id, shop_id=shop.id, served_at=earlier)
    db.add(r)
    await db.commit()
    await db.refresh(r)

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    response = await client.post(f"/voucher/{r.id}/use", follow_redirects=False)
    assert response.status_code == 303

    db.expire_all()
    await db.refresh(r)
    assert abs((r.served_at - earlier).total_seconds()) < 1  # untouched


async def test_voucher_use_other_customers_404(client, db, shop):
    """Hand-crafted POST with someone else's redemption id must not
    activate it. Returns 404 — owner check stops the attack."""
    from app.models import Customer, Redemption
    owner = Customer(is_anonymous=False, display_name="เจ้าของ", phone="0855555556")
    attacker = Customer(is_anonymous=False, display_name="คนอื่น", phone="0866666667")
    db.add_all([owner, attacker])
    await db.commit()
    await db.refresh(owner)
    await db.refresh(attacker)
    r = Redemption(customer_id=owner.id, shop_id=shop.id)
    db.add(r)
    await db.commit()
    await db.refresh(r)

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(attacker.id))

    response = await client.post(f"/voucher/{r.id}/use", follow_redirects=False)
    assert response.status_code == 404

    db.expire_all()
    await db.refresh(r)
    assert r.served_at is None


async def test_voucher_view_renders_qr_and_offer(client, db, shop):
    """GET /voucher/<id> renders the fullscreen voucher screen with the
    shop's reward_description and the audit-trail QR."""
    from app.models import Customer, Redemption
    from app.models.util import utcnow

    c = Customer(is_anonymous=False, display_name="พี่จี้", phone="0877777778")
    db.add(c)
    await db.commit()
    await db.refresh(c)
    r = Redemption(customer_id=c.id, shop_id=shop.id, served_at=utcnow())
    db.add(r)
    await db.commit()
    await db.refresh(r)

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    response = await client.get(f"/voucher/{r.id}")
    assert response.status_code == 200
    body = response.text
    assert "voucher-screen" in body
    assert shop.reward_description in body
    assert "vs-qr-frame" in body
    # 5-minute countdown anchors to served_at
    assert "vs-countdown" in body


async def test_scan_shop_renders_design_aligned_modal(client):
    """scan.camera modal — fullscreen scanner with butter-bracket
    viewfinder. Bottom 'ให้ร้านสแกน QR ของพี่' toggle was retired
    in the May 1 pass (cards.list now carries the QR icon top-right,
    so /my-id is reachable without crowding this surface)."""
    body = (await client.get("/scan-shop")).text
    assert "c-scan-modal" in body
    # Viewfinder corner markers + scanning line
    assert "vf-corners" in body
    assert "vf-line" in body
    # X close jumps back to /my-cards
    assert 'href="/my-cards"' in body
    # Toggle row is gone
    assert "ให้ร้านสแกน QR ของพี่" not in body
    assert "csm-bottom" not in body
    # No customer dock on the modal — it's fullscreen
    assert "c-glass-nav" not in body


async def test_my_gifts_voided_redemption_excluded(client, db, shop):
    """Voided redemptions don't appear anywhere on the gifts page —
    they aren't a usable voucher and they aren't 'used' either."""
    from app.models import Customer, Redemption
    from app.models.util import utcnow
    c = Customer(is_anonymous=False, display_name="พี่ป้อ", phone="0833333334")
    db.add(c)
    await db.commit()
    await db.refresh(c)
    r = Redemption(
        customer_id=c.id, shop_id=shop.id,
        is_voided=True, voided_at=utcnow(),
    )
    db.add(r)
    await db.commit()

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    body = (await client.get("/my-gifts")).text
    # Falls back to empty state
    assert "ยังไม่มีของขวัญ" in body


async def test_c5_voucher_renders_active_state_when_unserved(client, db, shop):
    """C5 — fresh redemption (served_at NULL) shows the celebration label
    and particles. No served indicator yet."""
    from app.models import Customer, Redemption
    c = Customer(is_anonymous=True)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    r = Redemption(customer_id=c.id, shop_id=shop.id)
    db.add(r)
    await db.commit()
    await db.refresh(r)

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    body = (await client.get(f"/card/{shop.id}/claimed?r={r.id}")).text
    assert "✦ คูปองของพี่ ✦" in body
    assert "ใช้แล้ว" not in body
    assert "v-particles" in body
    # No served class on the voucher container
    assert "voucher served" not in body and 'voucher\n' in body or 'class="voucher"' in body


async def test_c5_active_voucher_offers_link_to_gifts(client, db, shop):
    """Apr 30 design refresh — active C5 voucher carries a 'ดูในของขวัญ'
    CTA that pins it to /my-gifts. Hidden once the voucher is served
    (it's already in the used pile by then)."""
    from app.models import Customer, Redemption
    c = Customer(is_anonymous=True)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    r = Redemption(customer_id=c.id, shop_id=shop.id)
    db.add(r)
    await db.commit()
    await db.refresh(r)

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    body = (await client.get(f"/card/{shop.id}/claimed?r={r.id}")).text
    assert "c5-cta-gifts" in body
    assert 'href="/my-gifts"' in body
    assert "ดูในของขวัญของพี่" in body


async def test_c5_served_voucher_hides_gifts_cta(client, db, shop):
    """Once served, the gifts CTA disappears — voucher is in the used
    pile and the link would loop the customer to its already-greyed row."""
    from app.models import Customer, Redemption
    from app.models.util import utcnow
    c = Customer(is_anonymous=True)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    r = Redemption(customer_id=c.id, shop_id=shop.id, served_at=utcnow())
    db.add(r)
    await db.commit()
    await db.refresh(r)

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    body = (await client.get(f"/card/{shop.id}/claimed?r={r.id}")).text
    assert "c5-cta-gifts" not in body


async def test_c5_voucher_swaps_to_served_state_when_served_at_set(client, db, shop):
    """C5 — once /issue/scan flips served_at, the voucher renders the
    'ใช้แล้ว' label, drops particles, and adds the .served container class
    so CSS can grey it out."""
    from app.models import Customer, Redemption
    from app.models.util import utcnow
    c = Customer(is_anonymous=True)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    r = Redemption(customer_id=c.id, shop_id=shop.id, served_at=utcnow())
    db.add(r)
    await db.commit()
    await db.refresh(r)

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    body = (await client.get(f"/card/{shop.id}/claimed?r={r.id}")).text
    assert "ใช้แล้ว" in body
    assert "voucher served" in body
    # Particles removed in served state
    assert "v-particles" not in body


async def test_c5_clears_push_prompt_cooldown_for_re_ask(client, db, shop):
    """C5 page emits a tiny inline script that clears the push-prompt
    cooldown so the prompt can re-fire on this high-intent moment per
    Push.prompt spec."""
    from app.models import Customer, Redemption
    c = Customer(is_anonymous=True)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    r = Redemption(customer_id=c.id, shop_id=shop.id)
    db.add(r)
    await db.commit()

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    body = (await client.get(f"/card/{shop.id}/claimed?r={r.id}")).text
    assert "td_push_prompt_until" in body
    assert "removeItem" in body
    # Push prompt partial is mounted via footer_mark, so the JS that reads
    # the cleared cooldown is also present.
    assert 'id="push-prompt"' in body


async def test_account_renders_text_size_picker_with_active_state(client, db, shop):
    """C6 — ขนาดตัวอักษร picker renders with the saved size marked active."""
    from app.models import Customer
    c = Customer(is_anonymous=False, display_name="พี่สมศรี", phone="0844444444",
                 text_size="lg")
    db.add(c)
    await db.commit()
    await db.refresh(c)

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    body = (await client.get("/card/account")).text
    assert "ขนาดตัวอักษร" in body
    # The lg button is active, others are not
    assert 'class="fs-opt lg active"' in body
    assert 'class="fs-opt sm"' in body and 'class="fs-opt md"' in body
    # Server→localStorage reconcile script is present
    assert "td_text_size" in body


async def test_text_size_post_persists_and_normalises_md_to_null(client, db, shop):
    from app.models import Customer
    c = Customer(is_anonymous=False, display_name="พี่สมศรี", phone="0833333333")
    db.add(c)
    await db.commit()
    await db.refresh(c)

    from app.core.auth import CUSTOMER_COOKIE_NAME
    from app.services.auth import issue_customer_token
    client.cookies.set(CUSTOMER_COOKIE_NAME, issue_customer_token(c.id))

    # Save lg
    r = await client.post("/card/account/text-size", data={"size": "lg"})
    assert r.status_code == 204
    await db.refresh(c)
    assert c.text_size == "lg"

    # Switch to md → server clears the column (md is the default, no need to store)
    r = await client.post("/card/account/text-size", data={"size": "md"})
    assert r.status_code == 204
    await db.refresh(c)
    assert c.text_size is None

    # Garbage size rejected
    r = await client.post("/card/account/text-size", data={"size": "huge"})
    assert r.status_code == 400


async def test_text_size_bootstrap_script_in_pwa_head(named_client, shop):
    """The pwa_head bootstrap (sets fs-* on <html> from localStorage) must
    ship on every customer page for instant first-paint sizing — drives
    root font-size via html.fs-sm/fs-lg so any rule using --text-* rem
    tokens scales accordingly."""
    body = (await named_client.get(f"/card/{shop.id}")).text
    assert "td_text_size" in body
    assert "classList.add('fs-'" in body


async def test_scan_live_token_invalid_shows_expired_page(client, shop):
    """S3.qr anti-fraud — /scan/{shop_id}?t=<bad_jwt> returns 410 with
    the friendly 'QR หมดอายุ' page (so screenshots of stale on-screen
    QRs don't issue stamps)."""
    response = await client.get(
        f"/scan/{shop.id}?t=not_a_real_jwt", follow_redirects=False,
    )
    assert response.status_code == 410
    assert "หมดอายุ" in response.text


async def test_scan_live_token_valid_proceeds_normally(client, shop):
    """A freshly-issued live-QR token is accepted; scan flow proceeds
    normally (303 redirect to either /card/{id} or /onboard/{id}
    depending on whether the customer is brand new)."""
    from app.services.auth import issue_live_qr_token
    token = issue_live_qr_token(shop.id)
    response = await client.get(
        f"/scan/{shop.id}?t={token}", follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert str(shop.id) in location
    assert location.startswith(("/card/", "/onboard/"))


async def test_scan_no_token_still_works_for_printed_sticker(client, shop):
    """Bare /scan/{shop_id} (no t= param) is the printed-sticker path —
    must keep working untouched (no token validation gate)."""
    response = await client.get(f"/scan/{shop.id}", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert str(shop.id) in location
    assert location.startswith(("/card/", "/onboard/"))


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


async def test_account_renders_for_anonymous_guest(client):
    """C6 used to bounce anonymous customers straight to /card/save, which
    made the gear icon useless for guests — the controls they DO need
    (text size, notifications) live on this screen regardless of claim
    status. The page now renders for everyone, falling back to the
    'ลูกค้าแต้มดี' placeholder when display_name is empty."""
    response = await client.get("/card/account", follow_redirects=False)
    assert response.status_code == 200
    body = response.text
    assert "บัญชีของพี่" in body
    assert "ลูกค้าแต้มดี" in body  # the anon fallback display name


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
    # Logout clears the cookie — the next /card/account spawns a fresh
    # anon (no Customer row found for the cleared cookie) and renders
    # the guest version of the page rather than redirecting elsewhere.
    follow = await client.get("/card/account", follow_redirects=False)
    assert follow.status_code == 200
    assert "ลูกค้าแต้มดี" in follow.text


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
    """onboard.link_account 'ไว้ก่อนครับ' skip must point to
    /onboard/{shop}/recovery so the customer is offered a recovery code
    before they leave."""
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


# ---------------------------------------------------------------------------
# link.prompt — soft sheet shown to ≥3-stamp anonymous customers
# ---------------------------------------------------------------------------


async def test_link_prompt_shows_when_anon_customer_has_three_stamps(client, db, shop):
    """Anonymous customer with 3 active stamps and no snooze record sees
    the link.prompt overlay on shop.daily."""
    from app.models import Customer, Point

    # Set display_name so /card doesn't bounce to /onboard.
    await client.post("/card/nickname", data={"name": "พี่ทดสอบ"})
    customer = (await db.exec(select(Customer))).first()
    for _ in range(3):
        db.add(Point(shop_id=shop.id, customer_id=customer.id, issuance_method="customer_scan"))
    await db.commit()

    body = (await client.get(f"/card/{shop.id}")).text
    assert 'id="link-prompt"' in body
    assert "ผูกบัญชีแต้มดีไหมครับ" in body
    assert "/auth/google/customer/start" in body  # social pills wired through


async def test_link_prompt_hidden_when_under_three_stamps(client, db, shop):
    """Same setup with only 2 stamps — sheet should NOT render."""
    from app.models import Customer, Point

    await client.post("/card/nickname", data={"name": "พี่ทดสอบ"})
    customer = (await db.exec(select(Customer))).first()
    for _ in range(2):
        db.add(Point(shop_id=shop.id, customer_id=customer.id, issuance_method="customer_scan"))
    await db.commit()

    body = (await client.get(f"/card/{shop.id}")).text
    assert 'id="link-prompt"' not in body


async def test_link_prompt_hidden_when_recently_snoozed(client, db, shop):
    """If the customer hit the snooze endpoint within 14 days, suppress."""
    from datetime import timedelta
    from app.models import Customer, Point
    from app.models.util import utcnow

    await client.post("/card/nickname", data={"name": "พี่ทดสอบ"})
    customer = (await db.exec(select(Customer))).first()
    for _ in range(3):
        db.add(Point(shop_id=shop.id, customer_id=customer.id, issuance_method="customer_scan"))
    customer.last_link_prompt_snoozed_at = utcnow() - timedelta(days=3)
    db.add(customer)
    await db.commit()

    body = (await client.get(f"/card/{shop.id}")).text
    assert 'id="link-prompt"' not in body


async def test_link_prompt_shows_after_14_day_cooldown(client, db, shop):
    """Snoozes older than 14 days no longer suppress the sheet."""
    from datetime import timedelta
    from app.models import Customer, Point
    from app.models.util import utcnow

    await client.post("/card/nickname", data={"name": "พี่ทดสอบ"})
    customer = (await db.exec(select(Customer))).first()
    for _ in range(3):
        db.add(Point(shop_id=shop.id, customer_id=customer.id, issuance_method="customer_scan"))
    customer.last_link_prompt_snoozed_at = utcnow() - timedelta(days=15)
    db.add(customer)
    await db.commit()

    body = (await client.get(f"/card/{shop.id}")).text
    assert 'id="link-prompt"' in body


async def test_link_prompt_hidden_for_claimed_customer(client, db, shop):
    """Customer with a phone/line_id is no longer anonymous — never show."""
    from app.models import Customer, Point

    await client.post("/card/nickname", data={"name": "พี่ทดสอบ"})
    customer = (await db.exec(select(Customer))).first()
    customer.is_anonymous = False
    customer.phone = "0855512345"
    db.add(customer)
    for _ in range(3):
        db.add(Point(shop_id=shop.id, customer_id=customer.id, issuance_method="customer_scan"))
    await db.commit()

    body = (await client.get(f"/card/{shop.id}")).text
    assert 'id="link-prompt"' not in body


async def test_link_snooze_endpoint_stamps_timestamp(client, db, shop):
    from app.models import Customer

    await client.post("/card/nickname", data={"name": "พี่ทดสอบ"})

    r = await client.post("/link/snooze", follow_redirects=False)
    assert r.status_code == 303

    db.expire_all()
    customer = (await db.exec(select(Customer))).first()
    assert customer.last_link_prompt_snoozed_at is not None


# ---------------------------------------------------------------------------
# shop.story menu items — render the .ss-menu-grid when ShopMenuItem rows
# exist for the shop.
# ---------------------------------------------------------------------------


async def test_shop_story_renders_menu_grid_when_items_exist(client, db, shop):
    from app.models import ShopMenuItem

    db.add_all([
        ShopMenuItem(
            shop_id=shop.id, name="ลาเต้ดอยช้าง", price=65,
            emoji="☕", is_signature=True, sort_order=0,
        ),
        ShopMenuItem(
            shop_id=shop.id, name="ครัวซองต์", price=55,
            emoji="🥐", sort_order=1,
        ),
    ])
    await db.commit()

    body = (await client.get(f"/story/{shop.id}")).text
    assert "เมนูเด็ด" in body
    assert "ss-menu-grid" in body
    assert "ลาเต้ดอยช้าง" in body
    assert "ครัวซองต์" in body
    # Price renders inside a .ss-menu-price block — ฿ and 65 are
    # separated by a <span class="baht"> wrapper.
    assert "฿" in body
    assert ">65<" in body
    # Signature flag → mint tag overlay
    assert "ss-menu-tag" in body
    assert "ขายดีที่สุด" in body


async def test_shop_story_omits_menu_grid_when_empty(client, shop):
    body = (await client.get(f"/story/{shop.id}")).text
    assert "ss-menu-grid" not in body
    # Section header only renders alongside the grid
    assert "เมนูเด็ด" not in body
