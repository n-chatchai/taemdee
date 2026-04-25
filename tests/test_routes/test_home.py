async def test_home_renders(client):
    response = await client.get("/")
    assert response.status_code == 200
    body = response.text
    assert 'href="/shop/register"' in body
    assert 'href="/my-cards"' in body
    assert "/static/css/home.css" in body
