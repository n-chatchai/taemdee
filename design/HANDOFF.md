# TaemDee Design — Handoff to Next Session

## Project context
**TaemDee (แต้มดี)** — Thai SME stamp card SaaS. Brand voice = "น้องแต้ม" helper character (ผม/พี่/ครับ). PWA-first. Monetization = DeeReach outbound messages.

## Files in /mnt/user-data/outputs/
- `taemdee-shop.html` — shop owner mockups (Sections 01-04)
- `taemdee-customer.html` — customer mockups · **uses semantic naming**
- `taemdee-home.html` — landing page
- `taemdee-brand-sheet.html` — design system
- `PRODUCT.md` — PRD
- `HANDOFF.md` — this file

## Session 9 cont 4 (Apr 30 - May 1) — Massive IA cleanup

### A. Customer · auto-receive voucher
- `inbox.message` removed "รับของฝาก" button → auto-receive
- Check icon "ของฝากจากร้าน · เก็บไว้ให้แล้ว" (mint-ink)
- Link "ดูใน ของขวัญของพี่ →" (dark + butter pill)
- Removed "ปิดเสียงร้านนี้" link from both variants

### B. Voucher activation flow (`voucher.use`)
- Trust-based: ลูกค้ากด "ใช้" = mark used ทันที (no shop confirm needed)
- ลูกค้าโชว์หน้าจอให้ร้านดู · QR optional (audit trail)
- C-gifts used state: timestamp "ใช้เมื่อ 14 ก.พ. 14:32" + mint check tag

### C. Account linking (onboard.link_account)
- "สมัครสมาชิก" → "ผูกบัญชี" (customer already exists)
- 4 social pills: LINE / Google / Facebook / เบอร์โทร (vertical stack 100% width)
- Removed phone icon · pushed warn-s text down
- Added `link.prompt` mockup (similar to push.prompt · sheet overlay)
  - Trigger: หลังเก็บแต้ม 3-5 ครั้ง · ยังไม่ผูก
  - Re-ask 14 days cooldown
  - Same 4 social pills inside sheet

### D. Shop · offer creation
- `S-offer.create` simplified · type tabs (ของฟรี default · ลดราคา)
  - **ของฟรี:** text input + 4 icon presets + อัปโหลด button
  - **ลดราคา:** unified box "ลด [N] [บาท/%]"
- `S13.create` + `S13.create.empty` offer attach states

### E. Shop · S3 issue methods
- `S3.issue` 4 buttons 2x2 grid: ลูกค้าสแกน · ร้านสแกน · กรอกเบอร์ · ค้นชื่อ
- `S3.qr` dynamic QR display

### F. Shop · S2.1 district + collision
- `S2.1` district picker → auto จังหวัด
- `S2.1.warn` inline collision (replaced separate page)

### G. Customer · semantic naming convention (massive rename)

| เก่า | ใหม่ |
|---|---|
| C1 | `shop.daily` |
| C2.1 | `onboard.greet` |
| C2.2 | `onboard.first_stamp` |
| C2.3 | `onboard.link_account` |
| C2.4 | `onboard.recovery` |
| C3 | `verify.phone` |
| C3.line | `verify.line` |
| C4 | `stamps.almost` |
| C5 | `reward.claim` |
| C6 | `settings` |
| C6.notif | `settings.notif` |
| C7 | `cards.list` |
| C8 | `my.qr` |
| C9 | `shop.story` |
| C-scan | `scan.camera` |
| C-gifts | `gifts.list` |
| Inbox | `inbox.list` |
| Inbox.empty | `inbox.empty` |
| Inbox.detail | `inbox.message` |
| Voucher.activate | `voucher.use` |
| Push.prompt | `push.prompt` |
| Install | `install.android` |
| Install.iOS | `install.ios` |

- Removed `shop.daily.guest` (replaced by `link.prompt` pattern)
- CSS classes (.c1-, .c5-, .c7-, .c9-) kept (not user-visible)
- Shop side (S1/S2/S3...) NOT renamed (per user)

### H. shop.story redesign
- Standalone page (NOT merged into shop.daily after multiple attempts)
- `.ss-points` minimal point display (subtle · less bold than shop.daily hero)
- `.ss-story` italic + signature ("— ลุงหมี")
- `.ss-menu` horizontal scroll cards (4 items · butter-soft bg)
- `.ss-info` key-value with dashed dividers

### I. Inbox.message CTA
- 2 buttons (บัตรร้าน + ดูร้าน) → 1 button "ดูร้าน" (full width · primary ink+butter)

### J. settings.notif · 3 channel options
- **แต้มดี** (default · in-app push) · selected
- **LINE** (enabled if linked)
- **เบอร์โทร SMS** (disabled if not linked · inline "ผูกที่นี่" link)

### K. Misc fixes
- "สวัสดีพี่สมศรี" globally (no ครับ in greeting)
- cards.list top-right: QR + ⚙ icons
- reward.claim voucher box min-height 140px
- Customer dock: บัตร · สแกน · ข้อความ · ของขวัญ

## Design system reminders

1. **3 bar patterns**: `.shop-head` / `.shop-head.hero` / `.app-bar` / `.page-head`
2. **Surface tone**: cream=page bg · surface=interactive · butter-soft=น้องแต้ม voice · accent-soft=warning · mint=positive
3. **Dock order**: บัตร · สแกน · ข้อความ · ของขวัญ
4. **Voice**: น้องแต้ม uses ผม/พี่/ครับ · shop voice = generic
5. **Logo rule**: Only orange tile + butter features for primary
6. **Smart trigger stages**:
   - Stage 1 · `install.android/ios` (stamps≥2)
   - Stage 1.5 · `link.prompt` (stamps 3-5 · not linked) ⭐ NEW
   - Stage 2 · `push.prompt` (isPWA · first_pwa_open)
7. **Channel waterfall**: Push 0.5s → LINE 1s → SMS 3s → Inbox always
8. **Term policy**:
   - "แคมเปญ" → "ข้อความ"
   - "สมัครสมาชิก" → "ผูกบัญชี" ⭐ NEW
9. **Issue methods**: 4 buttons 2×2 in S3.issue
10. **Font-size scale**: rem-based · base 16px · 9 tiers · html.fs-sm/md/lg toggles
11. **Auth model**: Staff = identity-bound (LINE/phone) · Customer same
12. **Location**: เขต/อำเภอ → auto จังหวัด · collision inline warning
13. **Voucher flow**: Auto-receive · trust-based activation (ลูกค้ากด=ใช้แล้ว)
14. **Greeting**: "สวัสดีพี่สมศรี" (no ครับ in greeting)
15. **Naming convention**: customer = semantic dot-notation · shop = numeric S1/S2
16. **Prompt pattern**: faded background + sheet overlay (`.pp-overlay`/`.pp-page-fade`/`.pp-sheet`) · push.prompt + link.prompt

## Pending / Next sessions

### 🟡 Medium priority
- Welcome screen ก่อน S1 (shop onboarding)
- Tutorial overlay บน S3 first-time
- S2.4 done screen next-steps panel
- Font-size system port to shop file
- Communication features: announce closure · 3-tap feedback · request fix points
- Backend voucher schema · redemption flow · multi-shop pool (Phase 2)

### 🔴 Dev/launch
- Service worker (sw.js) · cache + Notification handler
- Recovery code spec · generation + verify flow
- PWA manifest validation
- Push subscription backend endpoint
- Voucher schema + redemption flow (Phase 2)
- Offer creation backend (Phase 2)

## User communication style
- Thai-mostly · short directives ("ok", "มา", "ลุย", "ไว้ก่อน", "เฮ่อ")
- Visual-first feedback · decisive on A/B/C/D
- Will revert often ("เอาออกก่อน" · "revert เฮ่อ")
- Direct iteration: "เกือบดีหละ X → Y"
- Pushed back on friction · agreed to auto-receive voucher
- Decisive on naming: "เลิกใช้ C1 ใช้ชื่อไปเลย"
- Sometimes terse ("?", "พ่องสิ", "เฮ่อ") — ask for clarification respectfully
