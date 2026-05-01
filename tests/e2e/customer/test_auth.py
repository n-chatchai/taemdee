import pytest
from playwright.sync_api import Page, expect
from app.models import Shop

def test_phone_link_otp(page: Page, server_url: str, sync_db):
    """TC-C03: Test guest account conversion to permanent phone account via OTP."""
    
    # 1. Setup a test shop
    shop = Shop(
        name="Auth Shop", 
        slug="auth-shop", 
        phone="0822222222", 
        reward_threshold=10, 
        reward_description="Free Cake"
    )
    sync_db.add(shop)
    sync_db.commit()
    sync_db.refresh(shop)

    # 2. Start onboarding via /scan/{shop_id}
    page.goto(f"{server_url}/scan/{shop.id}")
    page.wait_for_url("**/onboard/*")
    
    # Step 1: Greeting
    page.fill("input.ob-input", "Auth User")
    page.click(".ob-bottom[x-show='step === 1'] button.ob-next")

    # Step 2: First stamp
    page.locator(".ob-canvas[x-show='step === 2']").wait_for(state="visible")
    page.click(".ob-bottom[x-show='step === 2'] button.ob-next")

    # Step 3: Soft wall - Click "สมัครด้วยเบอร์โทร"
    page.locator(".ob-canvas[x-show='step === 3']").wait_for(state="visible")
    page.click("text=สมัครด้วยเบอร์โทร")

    # 3. OTP Phone Entry
    page.wait_for_url("**/card/save")
    expect(page.locator(".headline:visible")).to_contain_text("ใส่เบอร์ของพี่")
    
    page.fill("input[type='tel']", "0899999999")
    page.click("button:has-text('ส่ง OTP')")

    # 4. OTP Verification
    otp_msg = page.locator("text=OTP ทดสอบ:").first
    otp_msg.wait_for(state="visible")
    otp_code = otp_msg.locator("strong").text_content()
    print(f"Captured OTP: {otp_code}")

    # Enter the code
    page.fill("input.otp-input", otp_code)
    page.click("button:has-text('ยืนยัน')")

    # 5. Verify successful conversion
    # Should redirect to /my-cards
    page.wait_for_url("**/my-cards")
    expect(page.locator(".ph-text h1")).to_contain_text("สวัสดีครับพี่Auth User")
    
    # Go to card settings and verify phone
    page.click("a[href='/card/account']")
    page.wait_for_url("**/card/account")
    # Verify name and phone in profile card
    expect(page.locator(".c6-profile-card .info .name")).to_contain_text("Auth User")
    # Actual value includes country code: +66 89 ••• 9999
    expect(page.locator(".c6-profile-card .info .meta span")).to_contain_text("89 ••• 9999")
