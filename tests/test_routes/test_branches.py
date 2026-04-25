"""Branches page renders HTML; mutations 303-redirect back."""

from sqlmodel import select

from app.models import Branch, Shop


async def test_create_first_branch_redirects(auth_client, db, shop):
    response = await auth_client.post(
        "/shop/branches", data={"name": "Main"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/shop/branches"

    result = await db.exec(select(Branch).where(Branch.shop_id == shop.id))
    assert len(list(result.all())) == 1


async def test_second_branch_requires_mode(auth_client):
    await auth_client.post("/shop/branches", data={"name": "Main"}, follow_redirects=False)
    response = await auth_client.post(
        "/shop/branches", data={"name": "Branch 2"}, follow_redirects=False
    )
    assert response.status_code == 400


async def test_second_branch_locks_mode(auth_client, db, shop):
    await auth_client.post("/shop/branches", data={"name": "Main"}, follow_redirects=False)
    response = await auth_client.post(
        "/shop/branches",
        data={"name": "Branch 2", "reward_mode": "separate"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    await db.refresh(shop)
    assert shop.reward_mode == "separate"


async def test_branches_page_renders(auth_client):
    await auth_client.post("/shop/branches", data={"name": "Nimman"}, follow_redirects=False)
    response = await auth_client.get("/shop/branches")
    assert response.status_code == 200
    assert "Nimman" in response.text
    assert "เพิ่มสาขาใหม่" in response.text


async def test_branches_unauthenticated_401(client):
    response = await client.get("/shop/branches")
    assert response.status_code == 401
