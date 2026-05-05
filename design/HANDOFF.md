# TaemDee Design — Handoff to Next Session

## Project context
**TaemDee (แต้มดี)** — Thai SME stamp card SaaS. Brand voice = "น้องแต้ม" helper character (ผม/พี่/ครับ). PWA-first. Monetization = "ส่งข้อความ" outbound (term unified).

## Files in /mnt/user-data/outputs/
- `taemdee-shop.html` — shop owner mockups (semantic naming, NOT S1/S2 anymore)
- `taemdee-customer.html` — customer mockups · semantic naming
- `taemdee-home.html` — landing page (hero · For Shops · For Customers · Manifesto · FAQ · CTA)
- `HANDOFF.md` — this file
- (Optional inputs: PRODUCT.md · taemdee-brand-sheet.html)

---

## Sessions 9 cont 5–7 summary (May 1–5)

### Naming · shop side renamed to semantic dot-notation
All shop S1/S2/S3 kickers renamed. CSS class names (`.s2-`, `.s3-`) NOT renamed (user-invisible).

| Old | New |
|---|---|
| S1 | `login`, `login.phone`, `login.otp` |
| S2.1 / S2.1.warn | `setup.shop`, `setup.shop.warn` |
| S2.2 | `setup.reward` |
| S2.3 | `setup.theme` |
| S2.4 | `setup.done` |
| S3 | `home` |
| S3.issue | `issue` (with `issue.scan/qr/phone/search`) |
| S3.customers | `customers`, `customer.detail` |
| S3.insights | `insights`, `insights.history` |
| S3.reach | `reach.send`, `reach.create`, `reach.empty`, `reach.sent`, `reach.confirm` |
| S11 | `settings.team` (consolidated · old S11 deleted, `staff` renamed) |
| S11.add/invite/join | `staff.add/invite/join` (kept staff. namespace · entity-level) |
| S12 | `settings.branches` |
| S13 | `offer.create`, `offer.discount` |
| Other | `settings.identity/contact/location`, `topup`, `topup.pay`, `print.qr`, `themes`, `landing` |

### Customer naming (already done in cont 4 · still valid)
`shop.daily`, `cards.list`, `cards.list.single`, `gifts.list`, `inbox.list/message`, `voucher.use`, `voucher.confirm`, `reward.claim`, `settings`, `settings.notif`, `scan.camera`, `my.qr`, `shop.story`, `onboard.greet/first_stamp/link_account/recovery`, `verify.phone/line`, `push.prompt`, `link.prompt`, `install.android/ios`

### Critical changes

#### A. Reward auto-flow (cont 6)
- `reward.almost` / `stamps.almost` **DELETED**
- Customer scan #10 → ระบบ **auto-create voucher + save to gifts.list ทันที** · ไม่ต้องกด
- Screen: `reward.claim` (WOW celebration) · then back to `shop.daily` 0/10 fresh
- No friction · 0 clicks for voucher creation

#### B. reward.claim WOW redesign (cont 6)
- Sun rays animation (conic-gradient · 18s rotate · masked radial)
- Achievement ribbon "✦ 10/10 ครั้งสำเร็จ" (dark pill · butter num)
- Big gift box hero (110×110 · orange gradient · ink border 2px · shine highlight · ink shadow · bobbing animation)
- Confetti 8 stars · wider scatter · mint added
- Title: 32px weight 800 "เก็บครบแล้ว!" (! orange) — NOT "ว้าว!" (removed)
- Sub: "ของขวัญ **กาแฟ Signature ฟรี** เก็บไว้ใช้ครั้งหน้านะครับ"
- Voucher card REMOVED (info redundant with sub)

#### C. cards.list voucher carousel (cont 6)
- Multi-voucher state · 3-card swipeable carousel (78% width · scroll-snap)
- Single state: `.cl-voucher-section.single` → full width (no scroll)
- Each card: reward image (130px gradient + custom SVG) + pulse "พร้อมใช้" tag + shop logo+name + reward + white CTA "**แลกของขวัญ**"
- Mockup variant `cards.list.single` exists in gallery
- CTA label evolution: "ใช้ที่ร้าน" → "แลกของขวัญ" (final)

#### D. cards.list todos section (cont 6)
- New onboarding nudges section: "📋 รายการแนะนำ · 4 รายการ"
- 4 todos: เพิ่มลงหน้าจอ · ผูกบัญชี · เปิดแจ้งเตือน · ตั้งรูปโปรไฟล์
- Card pattern: text top + actions row (ข้าม underline + accent CTA pill bottom)
- Butter-soft warm cards · accent CTA · NO icons (clean)
- "+" ghost dashed card at bottom for system-added suggestions
- Section header has orange icon tile + "**4 รายการ**" butter pill count

#### E. gifts.list WOW redesign (cont 6)
- 🟡 Summary card (butter): big "3" accent + "ของขวัญพร้อมใช้" + "เก็บไว้ใช้เมื่อกลับมาร้านครั้งหน้านะครับ"
- 🟧 Hero featured (gradient orange · reward image + pulse tag + shop+expiry pill + reward 22px + white CTA "แลกของขวัญ →")
- 2-col grid mini cards "ของขวัญอื่น" with reward image SVGs
- "ใช้แล้ว · 2 ชิ้น" accordion (collapsed default)
- **Hero selection rule:** voucher closest to expiry (sort by expiresAt asc) · ≤7 days → urgent pulse แดง "หมดใน X วัน" + 1s animation · >7 days → butter pulse "พร้อมใช้" 1.5s · Grid sorted by expiry asc

#### F. voucher.confirm bottom sheet (cont 6)
- Inserted before voucher.use · irreversible action warning
- Sheet handle + ⚠️ icon + "แลกของขวัญตอนนี้?" + "แลกแล้วจะคืนไม่ได้ · พี่ต้องอยู่ที่ร้านเพื่อโชว์หน้าจอให้พนักงาน"
- 🟡 Voucher card review (butter-soft) · 2 buttons: ยังไม่แลก + แลกเลย (orange · 1.4× wide)
- Pattern: `.rc-*` overlay+sheet+handle (shared with reach.confirm)

#### G. reach.confirm bottom sheet (cont 6)
- Quick-confirm sheet for single-customer reach (from "ส่งเตือน"/"ชวนกลับ" buttons in customers attention list)
- Customer header (avatar+name+reason+orange tag) → preview message → channel info (Web Push 0.5 เครดิต butter tile) → 2 buttons (ยกเลิก + ส่งเลย · 0.5)
- Distinction: insights tab = bulk system-recommended · reach.confirm = single-customer manual

#### H. customers redesign (cont 6)
- Stats band 4-up: 412 ทั้งหมด · 23 ใกล้รับ · 47 หายไป (warn red) · 18 ใหม่ (mint)
- "น่าสนใจวันนี้" section: 2 actionable highlights (gift icon ก้องใกล้รับ + wave icon ป่านหายไป) + "ดูทั้งหมด →"
- Filter chips with count badges: ทั้งหมด 412 · ลูกค้าประจำ 86 · ใกล้รับ 23 · หายไป 47

#### I. Shop login flow split (cont 6 · refined)
**LINE path:** login → tap LINE → OAuth → display_name auto pulled → setup.shop (2 tap)
**Phone path:** login → tap "ใช้เบอร์โทร" → **`login.phone`** (เบอร์ + ชื่อเรียก paired in butter card · "เรียกพี่ว่าอะไรดี? · ใช้ทักทายในแดชบอร์ด · ลูกค้าไม่เห็น · เปลี่ยนได้ทีหลัง" · CTA "ส่ง OTP →") → **`login.otp`** (OTP cells only · clean · CTA "ยืนยัน · ตั้งค่าร้าน →") → setup.shop

#### J. settings.team (cont 6)
- Old `settings.team` (S11 with text-based role + "..." menu) DELETED
- New `staff` mockup RENAMED → `settings.team` · title "ทีม"
- Owner row at top: dark ink avatar + "หมี (คุณ)" + locked tag "เจ้าของร้าน · ทำได้ทุกอย่าง" + no toggle
- Active rows: avatar + name + permission badges (butter pills · ออกแต้ม/ยกเลิก/ส่งข้อความ) + toggle on/off
- Inactive rows: dimmed opacity 0.5 + "หยุดใช้" gray pill + toggle off
- Pending rows: "ยังไม่เข้าร่วม · ส่งไปเมื่อ X" + "ดู QR" black button (re-show invite QR)
- "+ เพิ่มพนักงาน" button at top
- staff.add/invite/join keep `staff.` namespace (entity-level)

#### K. staff.add permissions (cont 6)
- 5-permission section "สิทธิ์การใช้งาน" + sub "เลือกสิ่งที่...ทำได้ · ปรับได้ทีหลังในตั้งค่า"
- ออกแต้ม (locked · "เปิดเสมอ" · core staff function) / ยกเลิกแต้ม ON / ส่งข้อความ OFF / เติมเครดิต OFF / ตั้งค่าร้าน OFF
- Each row: butter icon tile + name + desc + toggle

#### L. setup.reward advanced (cont 6)
- "ตั้งค่าเพิ่มเติม (ไม่บังคับ)" collapsible card after goal pills
- Field 1 · **อายุของขวัญ** · 6 expiry pills: 30 วัน / **60 วัน** default / 90 วัน / **จนกว่าของจะหมด** / ไม่หมดอายุ / + กำหนดเอง
- Field 2 · **เงื่อนไข · disclaimer** · textarea + 4 template chips (จนกว่าของจะหมด · ใช้ได้ครั้งละ 1 ใบ · ใช้กับเมนูใดก็ได้ · ไม่รวมกับโปรอื่น)
- Connect: gh-expiry pill in gifts.list

#### M. Role switcher · top-right user icon (cont 6)
- Customer 3 icons: [QR] · **[👤]** · [⚙]
- Shop: REMOVED bell entirely · 2 icons: **[👤]** · [⚙]
- Style: neutral/transparent (NOT orange · per user)
- Tap → opens profile menu with role switcher (between customer/shop personas if user has both)
- Implemented on every main page (cards.list / gifts.list / inbox.list / settings · home / issue / customers / insights · etc.)
- Customer keeps QR (visible · primary action) · shop only has profile + settings

#### N. Emoji removal · UI replacement with SVG (cont 6)
- All UI emoji icons replaced with SVG · keep emoji ONLY in message body content (user/system msg)
- Customer: 🎁 in section headers → outlined gift SVG
- Shop: 🎁 (gift icons in customers attention + reach.confirm + cust-tag inline + offer/po/opv blocks) → gift SVG · 👋 (away/หายไป) → wave hand SVG · 📲 (channel) → mobile phone SVG
- Kept: ✓ ✦ ★ (typographic chars) · emojis in message body content (☕🍵🎂🌿) · menu items in shop.story (☕🥐🍰🍩 · placeholder for real food images)

#### O. Term policy (cont 6 final)
- "แคมเปญ" → "ข้อความ" (already in cont 5)
- **"ดีรีช" → "ข้อความ"** (cont 6 · UI-visible only)
- "DeeReach" English kept in CSS comments + mockup gallery descriptions only
- "ส่งดีรีชหาลูกค้า" → "ส่งข้อความหาลูกค้า"
- Examples: "ส่งข้อความได้ X ครั้ง" (topup packages) · "DeeReach หาลูกค้า · ใช้เครดิต" → "ส่งข้อความหาลูกค้า · ใช้เครดิต" (staff perm)

#### P. Shop home page final state (cont 6)
- ลูกค้าวันนี้ chart (today snapshot · period pills วันนี้/สัปดาห์/เดือน)
- รายการที่ต้องทำ (system todos · 1 todo · เครดิต 100 เปิดบัญชี)
- **แต้มดีแนะนำ ✦** section · 3 cards: เครดิตเหลือ · 3 ลูกค้าใกล้รับ · ชวน 23 คนที่หายไปกลับ
- **NO sticky issue CTA** (tried + removed)
- Dock: หน้าแรก · ออกแต้ม · ลูกค้า · แนะนำ — "**3**" badge (count) on แนะนำ tab

#### Q. Shop dock badge variants (cont 6)
- Standard pulse dot: small accent circle (8×8 · pulse animation)
- Count badge: `.gn-badge.count` · numeric (16px height · accent bg · white num · no animation)

### This session (May 5) · home page

#### R. Manifesto section · BY SME, FOR SME
- Inserted before FAQ (#manifesto)
- Dark ink bg with subtle radial gradients (butter + accent)
- Audience tag: "04 ภารกิจของเรา · OUR MISSION"
- Kicker: "BY SME · FOR SME"
- H2: "สร้างโดย *คนตัวเล็ก* · เพื่อ <hl>คนตัวเล็ก.</hl>"
- 4 pillars in grid (max 1100px):
  1. **01 / REIMAGINE** — *คิดใหม่* เรื่อง SME
  2. **02 / EASY** — ใช้ง่ายเหมือน *มือถือ*
  3. **03 / AFFORDABLE** — *ฟรี* ตลอดไป จ่ายเฉพาะที่ใช้
  4. **04 / OPEN** — เคารพ *ลูกค้าของคุณ*
- Pledge quote (butter glow zone): *"เราจะไม่สร้างฟีเจอร์ที่ SME ใช้ไม่ได้ และไม่คิดราคาที่ SME จ่ายไม่ไหว."* — TaemDee Pledge

#### S. How section deleted
- DELETED `<section class="how">` (the 3-step "ลูกค้าสแกน QR" section)
- Removed "วิธีใช้" links from nav (top + footer)
- Reason: overlaps with For Shops + For Customers · message duplication

#### T. Hero revisions (multiple iterations)
- Headline: "บัตรสะสมแต้ม / ที่<hl>**คุณจะรัก**.</hl>" (changed from "ที่ลูกค้ารัก" to resolve contradiction with shop-targeted body copy)
- Title tag updated to "บัตรสะสมแต้มที่คุณจะรัก"
- Big WOW CTA "✦ ฟรี เปิดร้านใน 60 วิ →" was created then **REMOVED** (felt rushed · came before reader understood value)
- Hero feature card "✦ ตัวช่วยอัจฉริยะในตัว" + trust line **REMOVED** (per user)
- Final hero = **3 elements only:** tag · h1 · short sub paragraph
- CTAs remain: nav top-right "เปิดร้านฟรี" + Final CTA section (post-context)

#### U. FAQ compact (this session final)
- Reduced section padding 80–120 → 48–72 (~40% lighter)
- H2 26–40 (was 32–56)
- Item padding 18×20 → 12×16
- Toggle icon 28 → 22
- Title font 15 → 14 · letter-spacing tighter
- Item gap 12 → 8 · radius 16 → 14
- Net height: ~870px → ~605px (-30%)
- 6 questions kept

---

## Design system reminders

1. **3 bar patterns**: `.shop-head` / `.shop-head.hero` / `.app-bar` / `.page-head`
2. **Surface tone**: cream=page bg · surface=interactive · butter-soft=น้องแต้ม voice · accent-soft=warning · mint=positive
3. **Dock customer (4)**: บัตร · สแกน · ข้อความ · ของขวัญ
4. **Dock shop (4)**: หน้าแรก · ออกแต้ม · ลูกค้า · แนะนำ (with optional count badge)
5. **Voice**: น้องแต้ม uses ผม/พี่/ครับ · shop voice = generic
6. **Logo rule**: Only orange tile + butter features for primary
7. **Smart trigger stages**:
   - Stage 1 · `install.android/ios` (stamps≥2)
   - Stage 1.5 · `link.prompt` (stamps 3-5 · not linked)
   - Stage 2 · `push.prompt` (isPWA · first_pwa_open)
8. **Channel waterfall**: Push 0.5s → LINE 1s → SMS 3s → Inbox always
9. **Term policy**:
   - "แคมเปญ" → "ข้อความ"
   - "ดีรีช" → "ข้อความ" (UI · keep "DeeReach" in code comments)
   - "สมัครสมาชิก" → "ผูกบัญชี"
10. **Issue methods**: 4 buttons 2×2 in `issue` page · ลูกค้าสแกน primary
11. **Naming convention**: BOTH customer + shop = semantic dot-notation now (cont 6)
12. **Voucher flow**: Auto-receive · auto-create on scan #10 · trust-based activation (กดแลก = ใช้แล้ว)
13. **voucher.confirm**: Bottom sheet warns irreversible · before voucher.use
14. **Top-right header icons**: Customer [QR · 👤 · ⚙] · Shop [👤 · ⚙] (no bell · no orange)
15. **Hero gift selection**: closest expiry asc · ≤7 days = urgent red pulse
16. **No emoji as UI icon**: SVG only · emoji ONLY in message body content
17. **Class namespacing**: avoid generic names (.line, .phone) that collide · prefix mockup ID (.sj-line) · learned from staff.join phone-button bug
18. **Bottom sheet pattern**: `.rc-*` overlay+sheet+handle (used by reach.confirm + voucher.confirm)
19. **WOW CTA pattern**: orange gradient + ink border + offset shadow + shine sweep + pulse ring + butter badge + black arrow chip (still in CSS · usage TBD)
20. **Manifesto section pattern**: dark ink bg + radial gradients + numbered grid cards + butter pledge zone

## Pending / Next sessions

### 🟢 Light items
- voucher.use copy fix: "ของขวัญถูกใช้แล้ว" + "หมดอายุใน 4:32" contradicts. Should reframe as "show window" — "โชว์หน้านี้ให้ร้านดูภายใน 4:32" + remove "หมดอายุ"
- "จนกว่าของจะหมด" expiry pill needs paired "จำนวน vouchers" field for stock-limited mode (Phase 2)
- voucher.confirm: should appear from BOTH cards.list AND gifts.list (currently both have "แลกของขวัญ" CTA)
- Hero sub copy + manifesto alignment with new "ที่คุณจะรัก" angle (could revise sub but defer)

### 🟡 Medium priority
- Welcome screen ก่อน setup.shop (shop onboarding intro)
- Tutorial overlay บน home first-time
- setup.done next-steps panel
- Font-size system port to shop file
- Communication features: announce closure · 3-tap feedback (❤️/🤔/👎) · request fix points form · reply to message threads
- Identity model decision: 1 identity many roles vs. 2 separate accounts (raised but undecided)

### 🔴 Dev/launch
- Service worker (sw.js) cache + Notification handler
- Recovery code spec · generation + verify flow
- PWA manifest validation
- Push subscription backend endpoint
- Voucher schema + redemption flow (Phase 2)
- Multi-shop voucher pool
- Offer creation backend (Phase 2)

## User communication style
- Thai-mostly · short directives ("ok", "มา", "ลุย", "revert", "เฮ่อ", "?", "A", "C")
- Visual-first feedback expected · will revert often
- Decisive on A/B/C/D options
- Pushed back when needed ("ย้อนแย้ง", "settings ลึกไป", "ไม่มีปุ่มข้าม", "มันแน่นไป", "ไม่ draw attention", "ค่อยๆคิดนะ")
- Asks good architectural questions (เหตุผลทำไมต้องกด, role switcher discoverability, identity model, hero selection rule)
- Comfortable with ambiguity, willing to defer
- Shows screenshots from real iPhone for inspiration
- Push toward less emoji + cleaner pro look ("emoji ดูไม่ pro")

## Working files location
- `/home/claude/taemdee-customer.html` (working)
- `/home/claude/taemdee-shop.html` (working)
- `/home/claude/taemdee-home.html` (working)
- Outputs mirrored to `/mnt/user-data/outputs/`

## Transcripts
All session transcripts archived in `/mnt/transcripts/` with journal entries.
