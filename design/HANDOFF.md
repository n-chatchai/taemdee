# TaemDee · Design Handoff Package

**Design v1 · April 2026**
**Brand:** แต้มดี (TaemDee) — บัตรสะสมแต้มดิจิทัลสำหรับร้านเล็กในไทย

---

## 📦 Files in this package

### Core deliverables (HTML)

| File | Purpose | Phase |
|---|---|---|
| `taemdee-customer.html` | Customer app mockups (12 screens) | **Phase 1 — start here** |
| `taemdee-shop.html` | Shop app mockups (21 screens) | Phase 2 |
| `taemdee-home.html` | Marketing landing page (production responsive) | Anytime — deploy this as-is |
| `taemdee-brand-sheet.html` | Brand system handoff doc | Reference |

### Assets

- `manifest.json` — PWA manifest (referenced by all HTML files)
- `taemdee-icons/` — 6 PNG files (32, 192, 512, LINE OA 640, LINE login 256, transparent 512)
- `taemdee-package.zip` — full package archive

---

## 🎯 Recommended dev order

1. **Customer screens first** (`taemdee-customer.html`) — ลูกค้าใช้ก่อน, simpler scope
2. **Landing page** (`taemdee-home.html`) — already responsive, deploy as-is
3. **Shop screens** (`taemdee-shop.html`) — phase 2, larger scope (21 screens incl. campaign system)

---

## 🗺️ Customer file structure (12 mockups)

```
01  ลูกค้า · สแกนครั้งแรก          C2 → C2.welcome → C2.signup → C3
02  ลูกค้า · ใช้งานประจำ            C1 → C1.guest → C4 → C5
03  ลูกค้า · จัดการบัตร             C7 → C8 → C6
04  ติดตั้ง · ลงเครื่อง              Install bottom sheet
```

| Screen | URL | Description |
|---|---|---|
| C2 | `/s/{shop_id}?first=true` | First scan — celebration + 1/10 stamp + member upgrade banner |
| C2.welcome | (modal on C2) | Bottom sheet asking for nickname (auto-opens, skippable) |
| C2.signup | (modal on C2) | Bottom sheet — choose LINE or phone signup |
| C3 | `/auth/phone` | Phone OTP form (only for "ใช้เบอร์โทร" path) |
| C1 | `/s/{shop_id}` | Daily card — member view (avatar top-right → C7) |
| C1.guest | `/s/{shop_id}` (guest) | Daily card — guest mode (QR top-right → C8 to show shop) |
| C4 | `/s/{shop_id}` (10/10 state) | Ready to redeem — guest must register before redeem (signup gate) |
| C5 | `/s/{shop_id}` (post-redemption) | Reward claimed — voucher with conic gradient sweep + 8 star particles (anti-screenshot) |
| C7 | `/me` | My cards — multi-shop home (member only) |
| C8 | `/me/qr` | My QR — guest QR for shop to scan (rotates every 60s, anti-screenshot) |
| C6 | `/me/account` | Account settings (back arrow → C7) |
| Install sheet | (component) | Triggered by "เพิ่มลงหน้าจอ" link in footer |

---

## 🗺️ Shop file structure (21 mockups)

```
01  ร้าน · ค้นพบ + เข้าสู่ระบบ        HM → S1
02  ร้าน · ตั้งค่าร้าน                 S2.1 → S2.2 → S2.3 → S2.4
03  ร้าน · ใช้งานประจำวัน              S3 → S3.scan → S3.phone → S3.search
04  ร้าน · ตั้งค่า                      S10 → S5 → S7 → S7-confirm → S8 → S9 → S11 → S12
05  ร้าน · แต้มดีแนะนำ                  S13 → S13.detail → S13.sent
```

| Screen | URL | Description |
|---|---|---|
| HM | `/` | Homepage on mobile (role chooser) |
| S1 | `/shop/login` | Shop login (LINE / OTP) + branch info note (dashed border) |
| S2.1 | `/shop/setup/identity` | Setup step 1 — name + AI logo (3 options) |
| S2.2 | `/shop/setup/reward` | Setup step 2 — reward + 4 reward icon options + points (5/10/20/custom) |
| S2.3 | `/shop/setup/theme` | Setup step 3 — full-card customer view preview (mirrors C1 exactly) + 4 swatches |
| S2.4 | `/shop/setup/qr` | Setup complete — QR card + "ดาวน์โหลดคิวอาร์โค๊ด" + "ไปที่แดชบอร์ด" |
| S3 | `/shop/dashboard` | DeeBoard — hero metric, stats pills, แต้มดีแนะนำ brief, **sticky bottom dock** (latest customer + 3 actions: สแกน/กรอกเบอร์/ค้นชื่อ) |
| S3.scan | (modal on S3) | Modal สแกน QR ลูกค้า (full screen camera viewfinder) |
| S3.phone | (modal on S3) | Modal กรอกเบอร์ — auto-creates new customer if not found |
| S3.search | (modal on S3) | Modal ค้นหาลูกค้า + step counter + "ออกแต้ม" button |
| S10 | `/shop/settings` | Settings home — shop card + ร้าน section (วิธีออกแต้ม/รางวัล/ธีม/สาขา) + ทีม & บิล |
| S5 | `/shop/settings/issue-method` | Issue method config (default = ลูกค้าสแกน) |
| S7 | `/shop/settings/topup` | Top-up credits — 3 packages |
| S7-confirm | `/shop/settings/topup/confirm` | Top-up confirm before payment |
| S8 | `/shop/settings/qr` | Print QR code for counter |
| S9 | `/shop/settings/theme` | Change theme (same component as S2.3) |
| S11 | `/shop/settings/team` | Team members |
| S12 | `/shop/settings/branches` | Branch management |
| S13 | `/shop/recommend` | แต้มดีแนะนำ — list of 4 template campaigns + "สร้างแคมเปญเอง" |
| S13.detail | `/shop/recommend/{id}` | Campaign editor — customer list (checkboxes) + message with $variables + preview pane |
| S13.sent | `/shop/recommend/{id}/sent` | Confirmation — credits remaining + estimated returning customers |

---

## 🎨 Design system

See `taemdee-brand-sheet.html` for full spec. Key tokens:

```css
--bg: #F6F1E5;          /* Page bg, cream */
--surface: #FDFAF2;     /* Cards, slight off-white */
--ink: #111111;         /* Text, borders */
--ink-soft: #6B6660;    /* Secondary text */
--ink-softer: #A39D92;  /* Tertiary, dividers */
--accent: #FF5E3A;      /* Brand orange — CTAs */
--accent-soft: #FFE6DF; /* Accent bg, soft */
--butter: #FFD952;      /* Celebration yellow */
--butter-soft: #FFF1B8; /* Butter bg, soft */
--mint: #C8E8D0;        /* Success states */
--mint-ink: #1A6B3A;    /* Success text */
--line-green: #06C755;  /* LINE official */
```

**Typography:**
- `Host Grotesk` — Latin display, brand wordmark, big numbers
- `Prompt` — Thai body, UI labels (default)
- `JetBrains Mono` — IDs, timestamps, meta

**Logo (Smile Stamp):** see brand sheet §01-02 for anatomy + variants.

---

## ⚙️ Technical specs

### PWA setup
- All HTML files have `<link rel="manifest" href="manifest.json">` + Apple touch icons + theme-color meta
- Standalone install supported (Add to Home Screen on iOS, Install App on Android)
- Service worker NOT included — needed for Chrome auto-prompt + offline (recommend adding `sw.js` in production)

### Mobile mockup specs
- Phone frame: `9:19` aspect ratio, 320px max-width, rounded 40px
- Notch: `top: 16px, height: 22px, width: 84px`
- Sticky top + bottom pattern with `backdrop-filter: blur(8px)`

### Critical UX patterns

**Customer flow:**
- **Guest-first** — customer never required to sign up to collect points (1-9/10)
- **Signup gate at redeem** (C4) — must register before claiming reward (prevents fraud, gives shop contact)
- **Nickname asked at first scan** (C2.welcome modal) — skippable, used for greetings
- **Anti-screenshot voucher** (C5) — conic gradient sweep + 8 star particles. Static screenshot loses motion = exposed
- **Anti-screenshot guest QR** (C8) — rotates every 60s. Static screenshot expires immediately

**Shop flow:**
- **No "ออกแต้มลูกค้า" intermediate sheet** — S3 dock has 3 inline actions (สแกน/กรอกเบอร์/ค้นชื่อ) tappable directly
- **Sticky bottom dock on S3** — "ลูกค้าล่าสุด" feed (3 rows, latest highlighted with butter bg + ★ + ยกเลิก button) + 3 action buttons + brand bar below, all in one merged dock with drag handle on top
- **Auto-create on S3.phone** — if entered phone not found, shows "ลูกค้าใหม่" banner + "ออกแต้ม" button creates account + issues stamp atomically
- **Onboarding has no skip** — 4 mandatory steps, "ไม่กี่นาที" (was "60 วินาที")
- **Branch onboarding is hands-off** — S1 has dashed-border note "เป็นสาขาของร้านที่มีอยู่แล้ว? ติดต่อเจ้าของร้านให้เพิ่มเป็นสาขา" (no branch toggle in S2.1; branches managed in S12 only)
- **แต้มดีแนะนำ campaign system** — entry from S3 brief card → S13 list of templates (winback / near-reward / unclaimed / new customer) → S13.detail editor with $variables → S13.sent confirmation

---

## 📋 Recent design decisions

### Customer
- "บัตร" → **"แต้ม"** in copy where possible (e.g., "แต้มไม่หาย", "แต้มครบแล้ว")
- Member upgrade benefits: **"แต้มไม่หาย · เปลี่ยนมือถือก็ใช้ต่อได้ · ติดตามร้านโปรด"**
- C5 voucher uses conic gradient + animated stars instead of timestamp (sexier, language-free)
- C7 banner moved to **bottom** of card (less aggressive)

### Shop
- DeeReach feature renamed to **"แต้มดีแนะนำ"** (consistent across product)
- S3 stats simplified: "147 แต้มที่แจก · 6 รางวัลที่แลก · 412 เครดิตคงเหลือ" (was ambiguous)
- S3 hero restructured: top row (label left + delta pill right) → big centered number → caption
- S3 stats moved to **inline pill** below hero (saves vertical space, "แต้มดีแนะนำ" now above-fold)
- S3 dock has **drag handle** at top (collapsible affordance)
- S3 dock latest row gets butter highlight + star + "ยกเลิก" button only on the most recent transaction
- **S6 toast removed** (redundant with dock latest row)
- **S3.choose removed** (3 actions inline in dock, no intermediate sheet)
- S2.2 reward icons: 4 generic icons in grid (gift / voucher / star / coffee) — no "AI generated" copy
- S2.3 preview now mirrors C1 exactly (full-width card, not mini-phone)
- S2.4 changed from celebration → QR download page
- Modals/sheets do NOT have footer-mark (only pages do)

---

## ⚠️ Known dead CSS (safe to ignore or purge)

These classes still exist in stylesheets but are no longer used in HTML:

```
.s3-issue-fab + .fab-* — old single FAB, replaced by 3-button dock
.s2-branch-row + .s2-branch-toggle — removed branch toggle from onboarding
.s2-theme-preview + .preview-* — old S2.3 mini-card, replaced with C1-style full preview
.s2-customer-preview + .cp-* — old S2.4 mini-phone preview, replaced with QR card
.s6-bg + .s6-overlay + .s6-toast + .s6-pushed — S6 mockup removed entirely
.s3-action-panel — superseded by .s3-dock (rules still scoped to both)
.live-pulse + .pulse-dot + #claim-counter — old C5 timestamp, replaced with conic+particles
.c2-celebration / .c2-benefits / .c2-cta — old C2 layout
.nickname-ask + .na-* — inline nickname (replaced with modal C2.welcome)
.r-coffee-cup / .r-latte-art / .r-iced — old gradient mockup reward images
.method-picker + .mp-* — S3.choose removed
.phone-numpad + .key — S3.phone numpad removed (full-phone state shown)
```

Removing these is purely a code-cleanup task — no functional impact.

---

## 🚦 What's NOT included (recommend for next phase)

- **Service worker** (`sw.js`) — needed for Chrome PWA auto-prompt + offline support
- **LINE OA cover image** (1080×878 spec) — for LINE OA banner
- **LINE rich menu mockup** — 6-cell menu for OA quick actions
- **DeeBoard staff variant** of S3 (gated surfaces removed for non-owner roles)
- **Offers integration** — banner on DeeCard when active offer
- **Shop → Shop referral** — entry in S10 + landing for `?ref=X`
- **Reward-mode picker in S12** — Shared vs Separate per-branch rewards
- **CSS purge** of dead classes listed above

---

## ✅ Design v1 final checklist

- [x] Customer 12 screens — all complete
- [x] Shop 21 screens — all complete (incl. แต้มดีแนะนำ campaign system)
- [x] Brand sheet — handoff-ready
- [x] PWA manifest + icons (6 PNG sizes)
- [x] Mobile-first responsive home page
- [x] Logo system applied (favicon, splashes, footer marks, gallery cover)
- [x] All cover stats accurate (12 / 21 mockups verified)
- [x] No `localStorage` / `sessionStorage` (per artifact constraint)
- [x] All HTML parses cleanly
- [x] taemdee.com (not .co)
- [x] No iOS status bar leftovers
- [x] No TAEMDEE platform pill intrusion
- [x] All "60 วินาที" → "ไม่กี่นาที" (toast countdown excepted)
- [x] All "DeeReach/ดีรีช" → "แต้มดีแนะนำ"
- [x] Modals/sheets have no footer-mark (only pages do)
