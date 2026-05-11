"""HTTP integration tests for the super-user admin surface.

Covers PIN auth, signed-cookie gating, every protected list/detail
page, and the impersonation flow that mints a shop staff session.

The admin surface lives on admin.taemdee.com (admin.test in test
land) so requests need a Host of admin.test or the subdomain
middleware bounces /admin/* away to the main host."""

import hmac
import hashlib
import time
from typing import AsyncGenerator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import SESSION_COOKIE_NAME
from app.core.config import settings
from app.models import Shop, StaffMember, User
from app.routes.admin import ADMIN_COOKIE, ADMIN_SESSION_TTL


TEST_PIN = "999123"


def _signed_admin_cookie(issued_at: int | None = None) -> str:
    """Mint a real admin_session cookie value (issued_at.sig) using the
    same HMAC scheme the route does so we can short-circuit the PIN
    form in tests that aren't about login itself."""
    issued = str(int(time.time()) if issued_at is None else issued_at)
    sig = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        issued.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{issued}.{sig}"


@pytest.fixture(autouse=True)
def _admin_pin_enabled(monkeypatch):
    """Every admin test runs with a known PIN. Tests that need it
    disabled re-monkeypatch settings.admin_pin to empty."""
    monkeypatch.setattr(settings, "admin_pin", TEST_PIN)


@pytest.fixture
async def admin_client(app_for_test) -> AsyncGenerator[AsyncClient, None]:
    """Client targeted at admin.test so /admin/* lands directly instead
    of bouncing through the subdomain redirector."""
    transport = ASGITransport(app=app_for_test)
    async with AsyncClient(transport=transport, base_url="https://admin.test") as c:
        yield c


@pytest.fixture
async def auth_admin_client(admin_client) -> AsyncClient:
    """admin_client pre-loaded with a valid signed admin_session cookie."""
    admin_client.cookies.set(ADMIN_COOKIE, _signed_admin_cookie())
    return admin_client


# ── Login / logout ─────────────────────────────────────────────────────────


async def test_login_form_renders_when_pin_configured(admin_client):
    r = await admin_client.get("/admin/login")
    assert r.status_code == 200
    assert 'name="pin"' in r.text


async def test_login_form_503_when_pin_empty(admin_client, monkeypatch):
    monkeypatch.setattr(settings, "admin_pin", "")
    r = await admin_client.get("/admin/login")
    assert r.status_code == 503


async def test_login_correct_pin_sets_cookie_and_redirects(admin_client):
    r = await admin_client.post(
        "/admin/login", data={"pin": TEST_PIN}, follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/dashboard"
    assert ADMIN_COOKIE in r.cookies


async def test_login_wrong_pin_redirects_back_with_error(admin_client):
    r = await admin_client.post(
        "/admin/login", data={"pin": "wrong"}, follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login?error=invalid"
    assert ADMIN_COOKIE not in r.cookies


async def test_login_503_when_pin_empty_even_with_form_submit(
    admin_client, monkeypatch
):
    monkeypatch.setattr(settings, "admin_pin", "")
    r = await admin_client.post(
        "/admin/login", data={"pin": "anything"}, follow_redirects=False,
    )
    assert r.status_code == 503


async def test_logout_clears_cookie_and_bounces_to_login(auth_admin_client):
    r = await auth_admin_client.post("/admin/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"
    # delete_cookie sends Set-Cookie with empty value + Max-Age=0 for the
    # admin_session key. httpx's cookie jar carries the original test
    # value because the deletion's domain may not match, so verify the
    # response's Set-Cookie directly.
    set_cookies = r.headers.get_list("set-cookie")
    assert any(
        c.startswith(f"{ADMIN_COOKIE}=") and ("Max-Age=0" in c or "max-age=0" in c)
        for c in set_cookies
    )


# ── Cookie gating ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/admin/dashboard",
        "/admin/shops",
        "/admin/customers",
        "/admin/topups",
        "/admin/deereach",
    ],
)
async def test_protected_route_redirects_to_login_without_cookie(
    admin_client, path
):
    r = await admin_client.get(path, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"


async def test_protected_route_redirects_when_cookie_signature_invalid(
    admin_client
):
    admin_client.cookies.set(ADMIN_COOKIE, "1700000000.deadbeef")
    r = await admin_client.get("/admin/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"


async def test_protected_route_redirects_when_cookie_expired(admin_client):
    expired_issued = int(time.time() - ADMIN_SESSION_TTL.total_seconds() - 60)
    admin_client.cookies.set(ADMIN_COOKIE, _signed_admin_cookie(expired_issued))
    r = await admin_client.get("/admin/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"


async def test_admin_root_redirects_to_dashboard(auth_admin_client):
    r = await auth_admin_client.get("/admin/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/dashboard"


# ── Dashboard ──────────────────────────────────────────────────────────────


async def test_dashboard_counts_shops_and_customers(
    auth_admin_client, db, shop, customer
):
    r = await auth_admin_client.get("/admin/dashboard")
    assert r.status_code == 200
    body = r.text
    # Stat labels come from the template; values are interpolated.
    assert "Shops" in body
    assert "Customers" in body
    assert "Pending topups" in body


# ── Shops list + detail ────────────────────────────────────────────────────


async def test_shops_list_includes_seeded_shop(auth_admin_client, db, shop):
    r = await auth_admin_client.get("/admin/shops")
    assert r.status_code == 200
    assert shop.name in r.text


async def test_shop_detail_renders_for_existing_shop(
    auth_admin_client, db, shop
):
    """Seed a single owner StaffMember so the detail page has a row to
    render in its team table."""
    user = User(display_name="เจ้าของร้าน")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    owner = StaffMember(shop_id=shop.id, user_id=user.id, is_owner=True)
    db.add(owner)
    await db.commit()

    r = await auth_admin_client.get(f"/admin/shops/{shop.id}")
    assert r.status_code == 200
    assert shop.name in r.text
    assert "เจ้าของร้าน" in r.text


async def test_shop_detail_404_for_missing_shop(auth_admin_client):
    from uuid import uuid4
    r = await auth_admin_client.get(f"/admin/shops/{uuid4()}")
    assert r.status_code == 404


# ── Customers / topups / deereach lists ────────────────────────────────────


async def test_customers_list_renders(auth_admin_client, db, customer):
    r = await auth_admin_client.get("/admin/customers")
    assert r.status_code == 200


async def test_topups_list_renders_with_pending_slip(
    auth_admin_client, db, shop
):
    from app.models import TopupSlip
    slip = TopupSlip(
        shop_id=shop.id,
        amount=100_000,  # 1000 THB → satang
        slip_image_url="https://example/x.png",
        slip_hash="hash-test-001",
        status="pending",
    )
    db.add(slip)
    await db.commit()

    r = await auth_admin_client.get("/admin/topups")
    assert r.status_code == 200
    assert shop.name in r.text


async def test_deereach_list_only_shows_sent_campaigns(
    auth_admin_client, db, shop
):
    """Draft campaigns (sent_at=NULL) must not appear in the admin
    deereach feed — the list intentionally filters on sent_at IS NOT NULL."""
    from datetime import datetime, timezone, timedelta
    from app.models import DeeReachCampaign
    sent = DeeReachCampaign(
        shop_id=shop.id,
        kind="manual",
        audience_count=1,
        message_text="ส่งแล้ว",
        sent_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
        final_credits_satang=50,
    )
    draft = DeeReachCampaign(
        shop_id=shop.id,
        kind="manual",
        audience_count=1,
        message_text="ร่างยังไม่ส่ง",
        sent_at=None,
    )
    db.add_all([sent, draft])
    await db.commit()

    r = await auth_admin_client.get("/admin/deereach")
    assert r.status_code == 200
    assert "ส่งแล้ว" in r.text
    assert "ร่างยังไม่ส่ง" not in r.text


# ── Impersonate ────────────────────────────────────────────────────────────


async def test_impersonate_mints_shop_session_and_redirects(
    auth_admin_client, db, shop
):
    """POST /admin/impersonate/shop/<id> issues a shop session cookie
    and 303s the operator to /shop/dashboard on the shop subdomain."""
    user = User(display_name="เจ้าของ")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    owner = StaffMember(shop_id=shop.id, user_id=user.id, is_owner=True)
    db.add(owner)
    await db.commit()

    r = await auth_admin_client.post(
        f"/admin/impersonate/shop/{shop.id}", follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/shop/dashboard" in r.headers["location"]
    # Staff session cookie surfaced on the response so the cross-host
    # redirect picks it up on the shop subdomain.
    set_cookies = r.headers.get_list("set-cookie")
    assert any(SESSION_COOKIE_NAME in c for c in set_cookies)


async def test_impersonate_404_when_shop_missing(auth_admin_client):
    from uuid import uuid4
    r = await auth_admin_client.post(
        f"/admin/impersonate/shop/{uuid4()}", follow_redirects=False,
    )
    assert r.status_code == 404


async def test_impersonate_404_when_shop_has_no_owner_staff(
    auth_admin_client, db, shop
):
    """Shop exists but has no owner StaffMember (orphaned data from an
    aborted signup) → 404 so the admin doesn't silently mint a session
    that won't resolve a staff member on the shop side."""
    r = await auth_admin_client.post(
        f"/admin/impersonate/shop/{shop.id}", follow_redirects=False,
    )
    assert r.status_code == 404


async def test_impersonate_requires_admin_cookie(admin_client, db, shop):
    """No admin cookie → 303 to /admin/login, NOT a 404 or session-mint."""
    r = await admin_client.post(
        f"/admin/impersonate/shop/{shop.id}", follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"
