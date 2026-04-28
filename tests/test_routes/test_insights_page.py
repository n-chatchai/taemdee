"""S3.insights — 'แต้มดีแนะนำ' hub at /shop/insights. Default view shows
the brief 30-day metrics card on top + suggestion cards below; the brief
card links to ?view=history (full performance page with funnel + campaign
list).
"""
from datetime import timedelta

from app.models import Customer, DeeReachCampaign, Point
from app.models.util import utcnow


async def test_insights_default_view_renders_brief_card_and_glass_nav(auth_client):
    response = await auth_client.get("/shop/insights")
    assert response.status_code == 200
    body = response.text
    # Brief 30-day metrics card with link to history view
    assert "s3-ins-brief" in body
    assert "ภาพรวม 30 วัน" in body
    assert 'href="/shop/insights?view=history"' in body
    # Section header for the suggestion list
    assert "แต้มดีแนะนำ" in body
    # Glass nav at the bottom — insights tab highlighted
    assert "s3-glass-nav" in body
    assert 'href="/shop/customers"' in body


async def test_insights_history_view_with_no_campaigns_shows_empty_state(auth_client):
    response = await auth_client.get("/shop/insights?view=history")
    assert response.status_code == 200
    body = response.text
    assert "ภาพรวม 30 วัน" in body
    assert "ยังไม่ได้ส่งแคมเปญในช่วง 30 วัน" in body


async def test_insights_history_lists_campaigns_and_funnel(auth_client, db, shop):
    """A campaign sent within 7 days lands in 'กำลังดำเนินการ' and contributes
    to the 30-day funnel ส่ง count."""
    db.add(DeeReachCampaign(
        shop_id=shop.id,
        kind="win_back",
        audience_count=12,
        credits_spent=12,
        sent_at=utcnow() - timedelta(days=2),
    ))
    db.add(DeeReachCampaign(
        shop_id=shop.id,
        kind="almost_there",
        audience_count=8,
        credits_spent=8,
        sent_at=utcnow() - timedelta(days=20),
    ))
    await db.commit()

    response = await auth_client.get("/shop/insights?view=history")
    body = response.text
    # Funnel sums 12+8 = 20
    assert ">20<" in body
    # 7-day campaign in active section, older one in done section
    assert "กำลังดำเนินการ" in body
    assert "เสร็จแล้ว" in body
    assert "ชวนลูกค้าหายไปกลับมา" in body
    assert "กระตุ้นคนใกล้รับ" in body


async def test_insights_history_skips_old_campaigns_outside_30day_window(auth_client, db, shop):
    db.add(DeeReachCampaign(
        shop_id=shop.id,
        kind="win_back",
        audience_count=99,
        credits_spent=99,
        sent_at=utcnow() - timedelta(days=60),
    ))
    await db.commit()

    response = await auth_client.get("/shop/insights?view=history")
    body = response.text
    # Funnel total 0 — old campaign is excluded
    assert ">0<" in body
    assert "ยังไม่ได้ส่งแคมเปญในช่วง 30 วัน" in body


async def test_insights_suggestions_view_renders_compute_suggestions(auth_client, db, shop):
    """When there are eligible audiences, suggestions cards render."""
    c = Customer(is_anonymous=False, line_id="U_lapsed", display_name="หาย")
    db.add(c)
    await db.commit()
    await db.refresh(c)
    db.add(Point(
        shop_id=shop.id, customer_id=c.id, issuance_method="customer_scan",
        created_at=utcnow() - timedelta(days=60),
    ))
    await db.commit()

    response = await auth_client.get("/shop/insights")
    body = response.text
    # win_back suggestion fired — head copy shows up
    assert "ชวน" in body
    assert "คนที่หายไป" in body
