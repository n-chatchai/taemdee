"""S-staff list / add / invite / Staff.join — Apr 30 design refresh."""

from sqlmodel import select

from app.models import StaffMember


async def test_team_page_renders_design_aligned_layout(auth_client):
    response = await auth_client.get("/shop/team")
    assert response.status_code == 200
    body = response.text
    assert "s-staff-page" in body
    assert "พนักงาน" in body
    # Design's "เพิ่มพนักงาน" CTA points at the new add page
    assert 'href="/shop/team/add"' in body
    # Owner row pinned (no remove form on owner)
    assert "เจ้าของร้าน (คุณ)" in body


async def test_team_add_form_renders(auth_client):
    response = await auth_client.get("/shop/team/add")
    assert response.status_code == 200
    body = response.text
    # Nickname field + perm section (4 toggles + 1 locked "ออกแต้ม" row).
    assert 'name="display_name"' in body
    assert "ssf-input" in body
    assert "ssf-perm-section" in body
    assert "สิทธิ์การใช้งาน" in body
    assert "เปิดเสมอ" in body
    for field in ("can_void", "can_deereach", "can_topup", "can_settings"):
        assert f'name="{field}"' in body
    # No phone/email inputs in the new flow
    assert 'name="phone"' not in body
    assert 'name="line_id"' not in body


async def test_team_add_post_persists_permission_flags(auth_client, db, shop):
    """Toggles on the add form must round-trip to the StaffMember row —
    can_void defaults on, the rest follow what the owner picked."""
    response = await auth_client.post(
        "/shop/team/add",
        data={
            "display_name": "น้องเอฟ",
            "can_void": "on",
            "can_deereach": "on",
            # can_topup + can_settings unchecked
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    staff = (await db.exec(select(StaffMember).where(StaffMember.shop_id == shop.id))).first()
    assert staff.can_void is True
    assert staff.can_deereach is True
    assert staff.can_topup is False
    assert staff.can_settings is False


async def test_team_add_post_creates_staff_and_redirects_to_invite(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/team/add",
        data={"display_name": "น้องบี"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/shop/team/")
    assert location.endswith("/invite")

    members = (await db.exec(select(StaffMember).where(StaffMember.shop_id == shop.id))).all()
    members = list(members)
    assert len(members) == 1
    assert members[0].display_name == "น้องบี"
    # Invite token minted on creation
    assert members[0].invite_token is not None
    assert members[0].invite_token_expires_at is not None


async def test_team_add_post_rejects_blank_name(auth_client):
    response = await auth_client.post(
        "/shop/team/add", data={"display_name": "  "}, follow_redirects=False,
    )
    assert response.status_code == 400


async def test_team_invite_page_renders_qr_and_share_buttons(auth_client, db, shop):
    await auth_client.post("/shop/team/add", data={"display_name": "น้องเอ"})
    staff = (await db.exec(select(StaffMember).where(StaffMember.shop_id == shop.id))).first()

    response = await auth_client.get(f"/shop/team/{staff.id}/invite")
    assert response.status_code == 200
    body = response.text
    # QR rendered server-side via segno (inline SVG)
    assert "<svg" in body
    # Join URL embedded for the share/copy buttons
    assert f"/staff/join?t={staff.invite_token}" in body
    # Share + Copy buttons wired
    assert "ssi-share-btn" in body
    assert "ส่งลิงก์" in body
    assert "คัดลอก" in body


async def test_team_invite_page_remints_expired_token(auth_client, db, shop):
    """Owner re-opening the invite page on an expired token should auto-mint
    a fresh one — they always see a valid QR."""
    from datetime import timedelta
    from app.models.util import utcnow

    await auth_client.post("/shop/team/add", data={"display_name": "น้องซี"})
    staff = (await db.exec(select(StaffMember).where(StaffMember.shop_id == shop.id))).first()
    old_token = staff.invite_token

    # Force-expire
    staff.invite_token_expires_at = utcnow() - timedelta(hours=1)
    db.add(staff)
    await db.commit()

    await auth_client.get(f"/shop/team/{staff.id}/invite")
    await db.refresh(staff)
    assert staff.invite_token != old_token
    assert staff.invite_token_expires_at > utcnow()


async def test_revoke_marks_staff_and_redirects(auth_client, db, shop):
    await auth_client.post("/shop/team/add", data={"display_name": "น้องดี"})
    staff = (await db.exec(select(StaffMember).where(StaffMember.shop_id == shop.id))).first()

    response = await auth_client.post(
        f"/shop/team/{staff.id}/revoke", follow_redirects=False,
    )
    assert response.status_code == 303
    await db.refresh(staff)
    assert staff.revoked_at is not None


async def test_staff_join_page_with_valid_token(client, db, shop):
    """Valid invite token → Staff.join page shows shop name + nickname +
    LINE/phone login buttons."""
    staff = StaffMember(shop_id=shop.id, display_name="น้องอี")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    from app.services.team import mint_invite_token
    token = await mint_invite_token(db, staff)

    response = await client.get(f"/staff/join?t={token}")
    assert response.status_code == 200
    body = response.text
    assert shop.name in body
    assert "น้องอี" in body
    assert "เข้าด้วย LINE" in body
    assert "เข้าด้วยเบอร์โทร" in body


async def test_staff_join_page_with_expired_token_shows_friendly_state(client, db, shop):
    """Expired token → 200 with 'invite หมดอายุ' copy (not a 404)."""
    from datetime import timedelta
    from app.models.util import utcnow

    staff = StaffMember(
        shop_id=shop.id, display_name="น้องเก่า",
        invite_token="EXPIRED_TOKEN",
        invite_token_expires_at=utcnow() - timedelta(hours=1),
    )
    db.add(staff)
    await db.commit()

    response = await client.get("/staff/join?t=EXPIRED_TOKEN")
    assert response.status_code == 200
    body = response.text
    assert "หมดอายุ" in body
    # Don't leak the shop name when the token is bad
    assert "เข้าด้วย LINE" not in body


async def test_staff_join_page_with_unknown_token(client):
    response = await client.get("/staff/join?t=NOT_A_REAL_TOKEN")
    assert response.status_code == 200
    assert "หมดอายุ" in response.text
