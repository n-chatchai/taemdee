async def test_invite_staff(auth_client):
    response = await auth_client.post(
        "/shop/team", data={"phone": "0899999999", "display_name": "Nok"}
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Nok"
    assert response.json()["can_void"] is True
    assert response.json()["can_deereach"] is False


async def test_invite_requires_identity(auth_client):
    response = await auth_client.post("/shop/team", data={})
    assert response.status_code == 400


async def test_list_team(auth_client):
    await auth_client.post("/shop/team", data={"phone": "0811", "display_name": "Mai"})
    response = await auth_client.get("/shop/team")
    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_update_permissions(auth_client):
    invited = await auth_client.post("/shop/team", data={"phone": "0811"})
    staff_id = invited.json()["id"]

    response = await auth_client.patch(
        f"/shop/team/{staff_id}/permissions", data={"can_deereach": True}
    )
    assert response.status_code == 200
    assert response.json()["can_deereach"] is True


async def test_revoke_staff(auth_client):
    invited = await auth_client.post("/shop/team", data={"phone": "0811"})
    staff_id = invited.json()["id"]

    response = await auth_client.post(f"/shop/team/{staff_id}/revoke")
    assert response.status_code == 200
    assert response.json()["revoked_at"] is not None
