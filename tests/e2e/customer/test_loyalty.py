import pytest
from playwright.sync_api import Page, expect
from app.models import Shop, Customer

def test_existing_user_scan_loyalty(page: Page, server_url: str, sync_db):
    """TC-C05: Test existing user scanning to get a point."""
    
    # 1. Setup a test shop and an existing customer
    shop = Shop(
        name="Loyalty Cafe", 
        slug="loyalty-cafe", 
        phone="0833333333", 
        reward_threshold=10, 
        reward_description="Free Latte"
    )
    sync_db.add(shop)
    
    customer = Customer(
        phone="0877777777",
        display_name="Loyalty Fan",
        is_anonymous=False
    )
    sync_db.add(customer)
    sync_db.commit()
    sync_db.refresh(shop)
    sync_db.refresh(customer)

    # 2. Login as the existing customer via UI
    page.goto(f"{server_url}/customer/login")
    page.click("button:has-text('ใช้เบอร์โทรศัพท์')")
    page.fill("#phone-input", "0877777777")
    page.click("button:has-text('รับรหัส OTP')")
    
    otp_msg = page.locator("text=OTP ทดสอบ:").first
    otp_msg.wait_for(state="visible")
    otp_code = otp_msg.locator("strong").text_content()
    page.fill("input[placeholder='1234']", otp_code)
    page.click("button:has-text('ยืนยันและเข้าสู่ระบบ')")
    
    page.wait_for_url("**/my-cards")

    # 3. Scan the shop (QR simulation)
    page.goto(f"{server_url}/scan/{shop.id}", wait_until="domcontentloaded")
    
    # Wait for the card shop name to be visible
    page.locator(".shop-sub").wait_for(state="visible")
    expect(page.locator(".shop-sub")).to_contain_text("Loyalty Cafe")
    
    # 4. Verify point increase
    stamps_on = page.locator(".stamps .stamp.on")
    expect(stamps_on).to_have_count(1)
    
    # 5. Scan again to get second point
    page.goto(f"{server_url}/scan/{shop.id}", wait_until="domcontentloaded")
    page.locator(".shop-sub").wait_for(state="visible")
    
    expect(page.locator(".stamps .stamp.on")).to_have_count(2)
