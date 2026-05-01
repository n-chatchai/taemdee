import pytest
from playwright.sync_api import Page, expect
from app.models import Shop


def test_guest_onboarding_and_soft_wall(page: Page, server_url: str, sync_db):
    """TC-C01 and TC-C02: Test the frictionless guest onboarding and skip signup."""

    # 1. Setup a test shop
    shop = Shop(
        name="E2E Cafe",
        slug="e2e-cafe",
        phone="0811111111",
        reward_threshold=10,
        reward_description="Free Coffee",
    )
    sync_db.add(shop)
    sync_db.commit()
    sync_db.refresh(shop)
    print(f"\nCreated shop: {shop.id}")

    # 2. Start onboarding via /scan/{shop_id} (simulating QR scan)
    url = f"{server_url}/scan/{shop.id}"
    print(f"Navigating to: {url}")
    page.goto(url)

    print(f"Current URL after scan: {page.url}")
    # Save a screenshot for debugging
    page.screenshot(path="after_scan.png")

    # Step 1: Greeting (C2.1)
    # Using locator for the bubble to avoid text splitting issues
    bubble = page.locator(".ob-bubble")
    expect(bubble).to_contain_text("สวัสดีครับ ผมชื่อน้องแต้ม")

    # Fill nickname
    page.fill("input.ob-input", "Test Customer")
    page.click("button.ob-next")

    # Step 2: First stamp (C2.2)
    expect(page.locator(".ob-thanks-line")).to_contain_text("ขอบคุณครับTest Customer")
    expect(page.locator(".ob-num")).to_contain_text("1")
    page.click("button.ob-next")

    # Step 3: Soft wall (C2.3)
    expect(page.locator(".ob-warn-s")).to_contain_text("บัตรพี่จะถูกเก็บในเครื่องนี้นะครับ")

    # TC-C02: Click Skip (ขอบคุณแต่ยังก่อน)
    page.click("text=ขอบคุณแต่ยังก่อน")

    # Should land on /onboard/{shop_id}/recovery (C2.4)
    expect(page).to_have_url(f"{server_url}/onboard/{shop.id}/recovery")
    expect(page.locator(".ocr-code")).to_be_visible()

    # Continue to card (C1)
    page.click("text=ไปที่บัตรของผม")
    expect(page).to_have_url(f"{server_url}/card/{shop.id}")

    # Verify point is there (stamped during /scan)
    stamps_on = page.locator(".stamps .stamp.on")
    expect(stamps_on).to_have_count(1)

    # Verify Nickname is set
    expect(page.locator(".cgb-text")).to_contain_text("สวัสดีครับพี่Test Customer")
