# TaemDee — Handoff (end of Session 9 continuation, Apr 30)

## Project context
**TaemDee (แต้มดี)** — Thai SME stamp card SaaS · PWA-first
- Voice: น้องแต้ม character (ผม/พี่/ครับ)
- Monetization: DeeReach outbound messages with Credits (now called "ข้อความ" in UI · "แคมเปญ" intimidates SMEs)

## Files in /mnt/user-data/outputs/
- `taemdee-home.html` — landing (cleaned: no customer login · added "ตัวช่วยอัจฉริยะ" AI feature card)
- `taemdee-shop.html` — 30+ mockups · S0-S13.create
- `taemdee-customer.html` — 24 mockups (added: C-gifts, C-scan modal · font-size system)
- `taemdee-brand-sheet.html` — 11 sections (added Section 10: DeeCard themes · 5 looks)
- `taemdee-icons/` — full PWA asset set (apple-touch-icon-180 full-bleed for iOS no-black-ring)
- `manifest.json` · `PRODUCT.md`

## Major decisions (this session)

### Customer architecture · big shift
- **Dock now: บัตร · สแกน · ของขวัญ · ข้อความ** (4 tabs, no ตั้งค่า)
- **Settings (⚙)** moved to top-right of C7 (ph-icon-btn next to existing actions)
- **"สแกน"** = primary action in dock → opens fullscreen camera modal (`C-scan`) on tap, 1-tap UX
- **C-scan modal** has bottom toggle "ให้ร้านสแกน QR ของพี่" → switches to C8 QR display (20% case)
- **"วอชเชอร์" → "ของขวัญ"** (friendlier Thai term)
- **C-gifts page** added · พร้อมใช้ + ใช้แล้ว sections · gift-cards with butter/mint/accent icon variants
- **C5 redesigned** · "เก็บไว้ใช้ครั้งหน้า" framing + CTA "ดูในของขวัญ" (not "ใช้เลย") · realistic flow: ครบ 10 → กลับบ้าน → มาอีกที → ใช้

### S3 dashboard improvements
- Top bar: greeting "สวัสดี ลุงหมี." replaces taemdee. wordmark (Prompt 22px gray+ink+accent)
- Logo+icon sizing tuned to mobile-friendly · removed icon-clutter
- Metric chart: dual-bar (orange=สแกน + ดำ overlay=แลก) · legend added
- "รายการที่ต้องทำ" todo section above แต้มดีแนะนำ · first use case = welcome credits CTA
- ลูกค้าล่าสุด wrapped in surface card with header (consistent with white-bg pattern)
- S3.customers list now uses same surface card pattern

### S13 (DeeReach) overhaul
- **Term cleanup throughout:** แคมเปญ → ข้อความ / สร้างเอง / ส่งอีกครั้ง / ปรับข้อความ
- **S13.detail** segment badges: หายไป 14+ วัน · มีแต้ม 4+ · ลูกค้าใหม่ <7 วัน (accent/butter/mint)
- **Template badges** under "ข้อความ": ชวนกลับ ✓ · เกือบครบ · วันเกิด · เมนูใหม่ · ขอบคุณ · เปล่า
- **Channel chips** per customer row: Web Push 0.5 / LINE 1 / SMS 3 with active/available/strikethrough states
- **Search + ปลดทั้งหมด** moved INTO customer box with border-bottom separator
- **S13.create** new mockup ("สร้างเอง") with name search + template badges + textarea + var chips

### Other
- **Font-size system** added to customer file: rem-based 9 tiers (--text-2xs to --text-display) · hooked to C6 picker (fs-sm/md/lg toggles html class · scales all rem-based text)
- **Brand sheet Section 10**: DeeCard themes · 5 looks (แต้มดี / โมโน / กลางคืน / พาสเทล / สปอร์ต) with full mini DeeCard mockups
- **Apple touch icon** regenerated full-bleed (no rounded corners, no transparency) · fixes iOS black ring on home-screen
- **iOS install sheet** mockup (Install.iOS variant)
- **C2.4 done page** brand-foot match S8 print card

## Pending / future considerations

### Communication features (discussed, not built)
User asked about chat — pushback was scope-too-big · alternatives sketched but not implemented:
- ประกาศปิดวันนี้ (banner on DeeCard for all customers)
- Feedback 3-tap (❤️/🤔/👎)
- Request ซ่อมแต้ม (form 1 field + date)
- Reply to DeeReach message (mini-thread, auto-close)

### Known incomplete
- Voucher used state (after shop scans, voucher → "✓ ใช้แล้ว 14:32" + greyed)
- Mute confirm sheet (ปิดเสียง [ร้าน] ใช่ไหมครับ?)
- Welcome screen ก่อน S1 (shop onboarding)
- Tutorial overlay บน S3 first-time
- S2.4 done screen next-steps panel
- Font-size system port to shop file (currently customer-only)

### Dev/launch
- Service worker (sw.js) · cache strategy + Notification handler
- Recovery code spec · generation + verify flow
- PWA manifest validation
- Push subscription backend endpoint

## Design system notes (still applies)

### Color tokens
- cream = page bg
- surface = interactive cards (white-cream)
- butter-soft = น้องแต้ม voice
- accent-soft = warning/focus
- mint = positive/new

### Voice
- น้องแต้ม (system/AI): ผม / พี่ / ครับ
- Shop voice (DeeReach messages): generic — varies per shop

### Logo rule
- Only orange tile + butter features for primary
- Eyes r=9, smile stroke-width=7
- NO ดำ+ตาส้ม anywhere (eliminated globally session 9)

### Font-size system (customer file · ready to port to shop)
```
--text-2xs: 0.625rem  (10px) labels, ids
--text-xs:  0.75rem   (12px) caption, meta
--text-sm:  0.8125rem (13px) secondary body
--text-base: 0.875rem (14px) body, row name (DEFAULT)
--text-md:   1rem     (16px) emphasis
--text-lg:   1.125rem (18px) subtitle
--text-xl:   1.375rem (22px) page title
--text-2xl:  2.25rem  (36px) hero number
--text-display: 4rem  (64px) DeeCard hero

html.fs-sm { font-size: 14px; }  /* −12.5% */
html.fs-md { font-size: 16px; }  /* default */
html.fs-lg { font-size: 18px; }  /* +12.5% */
```

## User communication style (reminder)
- Thai-mostly, very short directives ("ok", "มา", "ลุย", "ได้", "จัดมา", "อืม")
- Visual-first feedback expected — sketches before implementation
- "ถามจริง" → push back honestly when overreach
- Decisive · chooses A/B/C/D when shown options
- Multiple revert cycles — "เอาออกก่อน" pattern
- Direct iteration: "เกือบดีหละ X → Y"
- Values: mobile-app feel, large fonts, color hierarchy, less branding overload, SME-friendly terminology
