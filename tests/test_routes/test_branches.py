async def test_create_first_branch(auth_client):
    response = await auth_client.post("/shop/branches", data={"name": "Main"})
    assert response.status_code == 200
    assert response.json()["name"] == "Main"


async def test_second_branch_requires_mode(auth_client):
    await auth_client.post("/shop/branches", data={"name": "Main"})
    response = await auth_client.post("/shop/branches", data={"name": "Branch 2"})
    assert response.status_code == 400


async def test_second_branch_with_mode(auth_client):
    await auth_client.post("/shop/branches", data={"name": "Main"})
    response = await auth_client.post(
        "/shop/branches", data={"name": "Branch 2", "reward_mode": "separate"}
    )
    assert response.status_code == 200


async def test_list_branches(auth_client):
    await auth_client.post("/shop/branches", data={"name": "Main"})
    response = await auth_client.get("/shop/branches")
    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_branches_unauthenticated_401(client):
    response = await client.get("/shop/branches")
    assert response.status_code == 401
