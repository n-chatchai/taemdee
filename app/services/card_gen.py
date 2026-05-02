import io
from PIL import Image, ImageDraw, ImageFont
import segno
import httpx
import urllib.parse
from typing import Optional
from app.models import Shop
from app.models.branch import Branch

# Standard fonts on macOS for Thai and English
FONT_SANS_BOLD = "/System/Library/Fonts/Supplemental/SukhumvitSet.ttc"
FONT_SANS_REG = "/System/Library/Fonts/Supplemental/Thonburi.ttc"
FONT_DISPLAY = "/System/Library/Fonts/Supplemental/Avenir.ttc"

async def generate_shop_card_png(shop: Shop, scan_url: str, branch: Optional[Branch] = None) -> bytes:
    """Generates a high-resolution PNG card for the shop.
    
    Includes:
    - Shop Name & Location
    - High-DPI QR code with center accent
    - Reward description (if any)
    - Trust strip and brand footer
    """
    # Canvas size: 1200x1400 (allows room for the brand footer)
    canvas_w = 1200
    canvas_h = 1450
    bg_color = (255, 255, 255)  # White for printing
    ink_color = (17, 17, 17)
    accent_color = (255, 94, 58)  # --accent: #FF5E3A
    ink_soft_color = (102, 102, 102)
    line_color = (17, 17, 17, 46) # 0.18 opacity

    img = Image.new("RGB", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(img)

    # 1. Background & Border (Rounded)
    radius = 60
    draw.rounded_rectangle(
        [40, 40, canvas_w - 40, canvas_h - 40],
        radius=radius,
        outline=(220, 220, 220),
        width=2
    )

    # Load fonts
    try:
        font_title = ImageFont.truetype(FONT_SANS_BOLD, 110)
        font_sub = ImageFont.truetype(FONT_SANS_REG, 44)
        font_trust = ImageFont.truetype(FONT_SANS_REG, 30)
        font_brand = ImageFont.truetype(FONT_DISPLAY, 45)
    except:
        font_title = ImageFont.load_default()
        font_sub = ImageFont.load_default()
        font_trust = ImageFont.load_default()
        font_brand = ImageFont.load_default()

    # 2. Shop Head
    # Logo
    logo_img = None
    if shop.logo_url and shop.logo_url.startswith("url:"):
        logo_url = shop.logo_url[4:]
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(logo_url)
                if resp.status_code == 200:
                    logo_img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        except:
            pass

    if logo_img:
        l_size = 140
        logo_img = logo_img.resize((l_size, l_size), Image.Resampling.LANCZOS)
        img.paste(logo_img, ((canvas_w - l_size) // 2, 80), logo_img)
        title_y = 240
    else:
        # Check for typography logo archetypes in logo_url (e.g. text:lt1:mhee)
        logo_text = (shop.name or "m")[0].lower()
        if shop.logo_url and shop.logo_url.startswith("text:"):
            parts = shop.logo_url.split(":", 2)
            if len(parts) == 3 and parts[2].strip():
                logo_text = parts[2].strip()
        
        logo_text += "."
        title_bbox = draw.textbbox((0, 0), logo_text, font=font_title)
        title_w = title_bbox[2] - title_bbox[0]
        draw.text(((canvas_w - title_w) // 2, 110), logo_text, font=font_title, fill=ink_color)
        title_y = 260

    sub_parts = [shop.name]
    if branch:
        sub_parts.append(branch.name)
    if shop.location:
        sub_parts.append(shop.location)
    
    sub_text = " ● ".join(sub_parts)
    sub_bbox = draw.textbbox((0, 0), sub_text, font=font_sub)
    sub_w = sub_bbox[2] - sub_bbox[0]
    draw.text(((canvas_w - sub_w) // 2, title_y), sub_text, font=font_sub, fill=ink_soft_color)

    # 3. QR Code
    qr_buf = io.BytesIO()
    segno.make(scan_url, error="h").save(qr_buf, kind="png", scale=25, border=1)
    qr_img = Image.open(qr_buf).convert("RGBA")
    
    # Scale QR to fixed size
    qr_display_size = 700
    qr_img = qr_img.resize((qr_display_size, qr_display_size), Image.Resampling.LANCZOS)
    
    # QR Border
    qr_x = (canvas_w - qr_display_size) // 2
    qr_y = 320
    draw.rectangle([qr_x-2, qr_y-2, qr_x+qr_display_size+2, qr_y+qr_display_size+2], outline=(200, 200, 200), width=1)
    img.paste(qr_img, (qr_x, qr_y), qr_img)

    # Center Badge (Accent)
    badge_size = 140
    badge_x = (canvas_w - badge_size) // 2
    badge_y = qr_y + (qr_display_size - badge_size) // 2
    
    badge = Image.new("RGBA", (badge_size, badge_size), (0,0,0,0))
    bdraw = ImageDraw.Draw(badge)
    bdraw.rounded_rectangle([0, 0, badge_size, badge_size], radius=25, fill=accent_color)
    
    # Badge Initial
    initial = (shop.name or "m")[0].lower() + "."
    try:
        font_badge = ImageFont.truetype(FONT_DISPLAY, 60)
        ibox = bdraw.textbbox((0, 0), initial, font=font_badge)
        iw = ibox[2] - ibox[0]
        ih = ibox[3] - ibox[1]
        bdraw.text(((badge_size - iw) // 2, (badge_size - ih) // 2 - 5), initial, font=font_badge, fill=(255, 255, 255))
    except:
        pass
        
    img.paste(badge, (badge_x, badge_y), badge)

    curr_y = qr_y + qr_display_size + 80


    # 5. Trust Strip
    trust_text = "ไม่ต้องโหลดแอป ● ไม่บังคับสมัคร ● ติดตามร้านโปรด"
    tbox = draw.textbbox((0, 0), trust_text, font=font_trust)
    tw = tbox[2] - tbox[0]
    draw.text(((canvas_w - tw) // 2, curr_y), trust_text, font=font_trust, fill=ink_color)

    # 6. Brand Footer
    brand_main = "taemdee"
    brand_dot = "."
    
    bbox_main = draw.textbbox((0, 0), brand_main, font=font_brand)
    bbox_dot = draw.textbbox((0, 0), brand_dot, font=font_brand)
    
    bw_main = bbox_main[2] - bbox_main[0]
    bw_dot = bbox_dot[2] - bbox_dot[0]
    smile_w = 40
    gap = 10
    
    total_w = bw_main + bw_dot + gap + smile_w
    footer_x = (canvas_w - total_w) // 2
    footer_y = canvas_h - 150
    
    # Draw 'taemdee' (Black)
    draw.text((footer_x, footer_y), brand_main, font=font_brand, fill=ink_color)
    # Draw '.' (Orange)
    draw.text((footer_x + bw_main, footer_y), brand_dot, font=font_brand, fill=accent_color)
    
    # Draw Smile (Orange)
    smile_x = footer_x + bw_main + bw_dot + gap
    smile_y = footer_y + 15
    
    # Eyes
    draw.ellipse([smile_x + 8, smile_y + 5, smile_x + 13, smile_y + 10], fill=accent_color)
    draw.ellipse([smile_x + 27, smile_y + 5, smile_x + 32, smile_y + 10], fill=accent_color)
    # Mouth
    draw.arc([smile_x + 5, smile_y + 8, smile_x + 35, smile_y + 22], start=0, end=180, fill=accent_color, width=4)


    # Save to buffer
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
