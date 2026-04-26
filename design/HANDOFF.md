# TaemDee · Design Handoff Package

**Design v1 · April 2026**
**Brand:** แต้มดี (TaemDee) — บัตรสะสมแต้มดิจิทัลสำหรับร้านเล็กในไทย

---

## 📦 Files in this package

### Core deliverables (HTML)

| File | Purpose | Phase |
|---|---|---|
| `taemdee-customer.html` | Customer app mockups (8 screens) | **Phase 1 — start here** |
| `taemdee-shop.html` | Shop app mockups (16 screens) | Phase 2 |
| `taemdee-home.html` | Marketing landing page (production responsive) | Anytime — deploy this as-is |
| `taemdee-brand-sheet.html` | Brand system handoff doc | Reference |
| `taemdee-final-thai.html` | Combined master (customer + shop) | Reference / archive |

### Assets

- `manifest.json` — PWA manifest (referenced by all 3 HTML files)
- `taemdee-icons/` — 6 PNG files (32, 192, 512, LINE OA 640, LINE login 256, transparent 512)

---

## 🎯 Recommended dev order

1. **Customer screens first** (`taemdee-customer.html`) — ลูกค้าใช้ก่อน, simpler scope
2. **Landing page** (`taemdee-home.html`) — already responsive, deploy as-is
3. **Shop screens** (`taemdee-shop.html`) — phase 2, larger scope (16 screens)

---

## 🗺️ Customer file structure

```
01  ลูกค้า · สแกนครั้งแรก          C2 → C3
02  ลูกค้า · ใช้งานประจำ            C1 → C4 → C5
03  ลูกค้า · จัดการบัตร             C7 → C6
04  ติดตั้ง · ลงเครื่อง              Install bottom sheet
```

| Screen | URL | Description |
|---|---|---|
| C2 | `/s/{shop_id}?first=true` | First scan — celebration + 3 benefits + LINE/phone/skip CTAs |
| C3 | `/auth/phone` | Phone OTP form (only for "ใช้เบอร์โทรแทน" path) |
| C1 | `/s/{shop_id}` | Daily card — shop's stamp page (anonymous or logged-in) |
| C4 | `/s/{shop_id}` (10/10 state) | Ready to redeem — counter-only button |
| C5 | `/s/{shop_id}` (post-redemption) | Reward claimed — celebration + voucher |
| C7 | `/me` | My cards — multi-shop home (logged-in only) |
| C6 | `/me/account` | Account settings (back arrow → C7) |
| Install sheet | (component) | Triggered by "เพิ่มลงหน้าจอ" link in footer |

## 🗺️ Shop file structure

```
01  ร้าน · ค้นพบ + เข้าสู่ระบบ        HM → S1
02  ร้าน · ตั้งค่าร้าน                 S2.1 → S2.2 → S2.3 → S2.4
03  ร้าน · ใช้งานประจำวัน              S3 → S6
04  ร้าน · ตั้งค่า                      S10 → S5 → S7 → S7-confirm → S8 → S9 → S11 → S12
```

| Screen | URL | Description |
|---|---|---|
| HM | `/` | Homepage on mobile (role chooser) |
| S1 | `/shop/login` | Shop login (LINE / OTP) |
| S2.1 | `/shop/setup/identity` | Setup step 1 — name + AI logo (3 options) |
| S2.2 | `/shop/setup/reward` | Setup step 2 — reward + AI image (3 options) + points (5/10/20/custom) |
| S2.3 | `/shop/setup/theme` | Setup step 3 — theme picker (big preview + 4 swatches) |
| S2.4 | `/shop/setup/done` | Setup complete — celebration + next step |
| S3 | `/shop/dashboard` | DeeBoard — daily home (stats, DeeReach, feed) |
| S6 | (toast on S3) | Stamp notification — appears when customer scans |
| S10 | `/shop/settings` | Settings home — 3 sections (ร้าน / ทีม & บิล) |
| S5 | `/shop/settings/issue-method` | Issue method config (default = ลูกค้าสแกน) |
| S7 | `/shop/settings/topup` | Top-up credits — 3 packages |
| S7-confirm | `/shop/settings/topup/confirm` | Top-up confirm before payment |
| S8 | `/shop/settings/qr` | Print QR code for counter |
| S9 | `/shop/settings/theme` | Change theme (same component as S2.3) |
| S11 | `/shop/settings/team` | Team members |
| S12 | `/shop/settings/branches` | Branch management |

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
--butter: #FFD952;      /* Celebration yellow */
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
- All 3 HTML files have `<link rel="manifest" href="manifest.json">` + Apple touch icons + theme-color meta
- Standalone install supported (Add to Home Screen on iOS, Install App on Android)
- Service worker NOT included — needed for Chrome auto-prompt + offline (recommend adding `sw.js` in production)

### Mobile mockup specs
- Phone frame: `9:19` aspect ratio, 320px max-width, rounded 40px
- Notch: `top: 16px, height: 22px, width: 84px` (cleared by 48px padding-top in `.app-bar` + `.c7-greet` + `.shop-head`)
- Sticky top + bottom pattern: header `position: sticky; top: 0` + footer `position: sticky; bottom: 0` with `backdrop-filter: blur(8px)`

### Critical UX patterns
- **No platform brand intrusion** — TAEMDEE pill removed from all in-app screens. Footer-mark "taemdee." + install link is the only attribution.
- **Customer never sees S1** — login flow is in-place via C2 ("เก็บกับ LINE" or "ใช้เบอร์โทรแทน" → C3 OTP form)
- **Shop S5 (issue method) is config, not action** — default is "ลูกค้าสแกน" (no shop interaction). Conditional FAB appears on S3 only if shop selects "ร้านสแกน" or "กรอกเบอร์"
- **Install pill is in footer of every primary screen** — persistent affordance, not modal

---

## ⚠️ Known dead CSS (safe to ignore or purge)

These classes still exist in stylesheets but are no longer used in HTML:

```
.dc-*, .sw-*, .sc-*  — old C1/C2/C5 designs, replaced
.topbar, .screen-status, .dots  — iOS status bar simulation, removed
.td-badge, .screen-tilted-badge  — TAEMDEE pill, removed
.pricing-grid, .price-card, .pkg-*  — pricing packages, removed from home
.s2-themes, .s2-theme, .s2-tprev, .s2-tmeta  — old S2.3 grid, replaced with new big-preview pattern
.s9-grid, .theme-card, .theme-preview, .theme-meta  — old S9 grid, replaced with same pattern as S2.3
.c2-welcome-text, .title, .sub  — removed welcome filler
.s1-tabs, .s1-tab, .tab-pill  — removed shop/customer toggle
.install-pill, .ip-stamp, .ip-text, .ip-arrow, .ip-close  — replaced by footer .fm-install link
.s2-reward-img, .s2-reward-img-empty (and inner .thumb, .info, .actions, .text)  — old simple thumbnail picker, replaced with .s2-rewards tile grid
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

- [x] Customer 8 screens — all complete
- [x] Shop 16 screens — all complete
- [x] Brand sheet (10 sections) — handoff-ready
- [x] PWA manifest + icons (6 PNG sizes)
- [x] Mobile-first responsive home page
- [x] Sticky top + bottom UI pattern (consistent everywhere)
- [x] Logo system applied (favicon, splashes, footer marks, gallery cover)
- [x] All cover stats accurate (8 / 16 mockups verified)
- [x] No `localStorage` / `sessionStorage` (per artifact constraint)
- [x] All HTML parses cleanly
- [x] taemdee.com (not .co)
- [x] No iOS status bar leftovers
- [x] No TAEMDEE platform pill intrusion
