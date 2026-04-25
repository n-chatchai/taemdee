"""Team page now renders HTML; mutations 303-redirect back."""

from sqlmodel import select

from app.models import StaffMember


async def test_invite_redirects_and_persists(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/team",
        data={"phone": "0899999999", "display_name": "Nok"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/team"

    result = await db.exec(select(StaffMember).where(StaffMember.shop_id == shop.id))
    members = list(result.all())
    assert len(members) == 1
    assert members[0].display_name == "Nok"
    assert members[0].can_void is True


async def test_invite_requires_identity(auth_client):
    response = await auth_client.post("/shop/team", data={}, follow_redirects=False)
    assert response.status_code == 400


async def test_team_page_renders(auth_client):
    await auth_client.post("/shop/team", data={"phone": "0811", "display_name": "Mai"})
    response = await auth_client.get("/shop/team")
    assert response.status_code == 200
    assert "Mai" in response.text
    assert "เชิญทีม" in response.text


async def test_update_permissions(auth_client, db, shop):
    await auth_client.post("/shop/team", data={"phone": "0811"}, follow_redirects=False)
    result = await db.exec(select(StaffMember).where(StaffMember.shop_id == shop.id))
    staff = result.first()

    response = await auth_client.post(
        f"/shop/team/{staff.id}/permissions",
        data={"can_deereach": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    await db.refresh(staff)
    assert staff.can_deereach is True


async def test_revoke_redirects_and_marks(auth_client, db, shop):
    await auth_client.post("/shop/team", data={"phone": "0811"}, follow_redirects=False)
    result = await db.exec(select(StaffMember).where(StaffMember.shop_id == shop.id))
    staff = result.first()

    response = await auth_client.post(f"/shop/team/{staff.id}/revoke", follow_redirects=False)
    assert response.status_code == 303

    await db.refresh(staff)
    assert staff.revoked_at is not None
