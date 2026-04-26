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


async def test_branch_qr_encodes_branch_id(auth_client, db, shop):
    await auth_client.post("/shop/branches", data={"name": "Nimman"}, follow_redirects=False)
    branch = (await db.exec(select(Branch).where(Branch.shop_id == shop.id))).first()

    response = await auth_client.get(f"/shop/branches/{branch.id}/qr")
    assert response.status_code == 200
    body = response.text
    assert f"/scan/{shop.id}?branch={branch.id}" in body
    assert "Nimman" in body  # branch context flowed into template


async def test_scan_with_branch_tags_stamp_and_redirects(client, db, shop):
    # Owner-controlled branch creation requires auth; bypass by inserting directly.
    branch = Branch(shop_id=shop.id, name="Nimman")
    db.add(branch)
    await db.commit()
    await db.refresh(branch)

    response = await client.get(
        f"/scan/{shop.id}?branch={branch.id}", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/card/{shop.id}?branch={branch.id}&stamped=1"
    )

    from app.models import Stamp
    stamps = (await db.exec(select(Stamp).where(Stamp.shop_id == shop.id))).all()
    assert len(list(stamps)) == 1
    assert list(stamps)[0].branch_id == branch.id


async def test_card_renders_branch_in_subtitle(client, db, shop):
    branch = Branch(shop_id=shop.id, name="ทองหล่อ")
    db.add(branch)
    await db.commit()
    await db.refresh(branch)

    await client.get(f"/scan/{shop.id}?branch={branch.id}", follow_redirects=False)
    response = await client.get(f"/card/{shop.id}?branch={branch.id}")
    assert response.status_code == 200
    assert "ทองหล่อ" in response.text


async def test_card_remembers_last_branch_without_query(client, db, shop):
    branch = Branch(shop_id=shop.id, name="อารีย์")
    db.add(branch)
    await db.commit()
    await db.refresh(branch)

    # First scan tags the stamp to the branch.
    await client.get(f"/scan/{shop.id}?branch={branch.id}", follow_redirects=False)
    # Second card view without ?branch — should still surface the branch from history.
    response = await client.get(f"/card/{shop.id}")
    assert response.status_code == 200
    assert "อารีย์" in response.text


async def test_branch_qr_404_for_other_shops_branch(auth_client, db, shop):
    # A branch under a different shop must not be QR-able by this owner.
    from app.models import Shop as ShopModel
    other = ShopModel(name="Other", phone="0822222222", reward_threshold=8)
    db.add(other)
    await db.commit()
    await db.refresh(other)

    other_branch = Branch(shop_id=other.id, name="Foreign")
    db.add(other_branch)
    await db.commit()
    await db.refresh(other_branch)

    response = await auth_client.get(f"/shop/branches/{other_branch.id}/qr")
    assert response.status_code == 404
