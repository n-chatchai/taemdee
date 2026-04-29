# TaemDee · HANDOFF · Dev-ready

**Last updated:** 2026-04-29 · **Status:** Customer file ready for dev review · **Session:** 8 (post brand overhaul + PWA flow)

---

## What's in this package

| File | Purpose |
|---|---|
| `taemdee-customer.html` | 23 customer mockups · all sections · v1 ready |
| `taemdee-shop.html` | 28 shop mockups · v0.9 (most sections done, needs S4 DeeReach polish) |
| `taemdee-brand-sheet.html` | Color/logo system · updated April 29 |
| `taemdee-logo.svg` | Vector primary logo |
| `taemdee-icon-32.png` | Favicon |
| `taemdee-icon-192.png` | Android home icon |
| `taemdee-icon-512.png` | iOS / high-res icon |
| `taemdee-icon-transparent-512.png` | Overlay-ready (no bg) |
| `PRODUCT.md` | Full PRD · screen inventory + flows |

---

## Customer file · 23 screens

### Section 01 · ลูกค้าใหม่ (onboarding)
1. **C2.1** First scan · Greeting + nickname
2. **C2.2** First scan · First stamp (1/10)
3. **C2.3** Save & signup choice (LINE / Phone / skip)
4. **C2.4** Recovery code (post-skip path)
5. **C3** Phone OTP + DeeReach consent toggle
6. **C3.line** LINE confirm + DeeReach consent toggle (off-state demo)

### Section 02 · ใช้งานประจำ (daily use)
7. **C1** DeeCard 7/10 · daily card
8. **Push.prompt** Web Push consent (overlay)
9. **C1.guest** DeeCard for unsigned users (single shop)
10. **C4** Ready to redeem (10/10 state)
11. **C5** Reward claimed + voucher

### Section 03 · จัดการ (management)
12. **C7** My Cards (multi-shop list)
13. **Inbox** Message list (DeeReach inbox)
14. **Inbox.empty** Empty state
15. **Inbox.detail** Message with offer (win-back)
16. **Inbox.detail.no-offer** Almost-there nudge
17. **Inbox.voucher** Voucher activated full screen
18. **C8** My QR (guest scan target)
19. **C6** Account menu
20. **C6.notifications** Notification preferences
21. **C9** Shop Story page

### Section 04 · ติดตั้ง (PWA install)
22. **Install** Bottom sheet · Android variant (native API)
23. **Install.iOS** Bottom sheet · iOS variant (step-by-step)

---

## Design system · key decisions

### Logo (NEW · April 29)
- **Variants eliminated:** ดำ + ตาส้ม (used everywhere previously)
- **Primary:** ส้ม tile (`#FF5E3A`) + butter eyes (`#FFD952`, r=9) + butter smile (stroke-width=7)
- **On accent bg:** butter tile + ink eyes (high contrast)
- **Inverted (dark bg):** cream tile + accent eyes/smile
- **On butter bg:** primary works (subtle contrast)
- **On LINE green:** cream tile + green features
- All HTML files (customer/shop/brand) updated · 26+ instances replaced

### Color tokens
```css
--bg: #F6F1E5;         /* page cream */
--surface: #FDFAF2;    /* card surface */
--ink: #111111;
--ink-soft: #6B6660;
--ink-softer: #A39D92;
--accent: #FF5E3A;     /* primary orange */
--accent-soft: #FFE6DF;
--butter: #FFD952;
--butter-soft: #FFF1B8;
--mint: #C8E8D0;
--mint-ink: #1A6B3A;
--line-green: #06C755;
--line: rgba(17,17,17,0.08);
```

### Top bar patterns (3 only)
- `.shop-head` — wordmark · used in shop-scoped pages
- `.shop-head.hero` — 44px wordmark · hero version (C1, C1.guest, C4)
- `.app-bar` — back icon + title + right slot (Inbox detail, Push, etc.)
- `.page-head` — greeting h1 + sub + ph-actions (dock destinations)

### Dock (5 tabs · liquid glass nav)
| Tab | Label | Icon |
|---|---|---|
| 1 | บัตร | Card stack |
| 2 | สแกน | Camera |
| 3 | ข้อความ | **Chat bubble** (changed from ECG line · April 29) |
| 4 | ตั้งค่า | Cog |

### Voice
- **น้องแต้ม** uses ผม / พี่ / ครับ (Customer-facing dialogue)
- **Shop voice** = generic, no character
- **Inbox detail body** = shop voice (the shop is sending)

---

## Major flows · dev specs

### DeeReach Consent (PDPA-compliant)
1. C2.3 Soft Wall → user picks LINE / Phone / skip
2. After OAuth (LINE) or OTP (Phone) → toggle screen with **default ON**
3. Toggle off → soft plea appears (no opt-out link, no aggressive UX)
4. Save preference: `consent_dee_reach: true|false`
5. User can change anytime at C6.notifications

### Smart PWA Install → Push (2-stage)
```js
// Stage 1: Install
if (!isPWA && stamps >= 2 && !cooldown('install', 14)) {
  showInstallSheet()
}

// Stage 2: Push (after install + opened from icon)
if (isPWA && Notification.permission === 'default' && first_pwa_open) {
  showPushPrompt()
}

// Re-ask after C5
if (!push_granted && just_redeemed && !cooldown('push', 14)) {
  showPushPrompt()
}
```

### Channel Waterfall (DeeReach delivery)
1. Try **Push** first (if `notification_permission === 'granted'`)
2. Fall back to **LINE** (if user has LINE OA)
3. Fall back to **SMS** (if user gave phone)
4. Always copy to **Inbox** (source of truth)

---

## What's pending · for dev or next design session

### 🔴 Need before launch
- **Service worker** (sw.js) · cache strategy + Notification handler
- **Recovery code** spec · generation rules (8-char like `K7M-XQ4P-2H9R`) + backend verify flow
- **PWA manifest** (`manifest.json`) · already in uploads, validate
- **Push subscription** · backend endpoint to register/unregister

### 🟡 Improvements (next design pass)
- **Voucher used state** — after shop scans, voucher → "✓ ใช้แล้ว 14:32" + greyed
- **C1 unread indicator** — small dot on shop card (C7) if unread message
- **Mute confirm sheet** — bottom sheet "ปิดเสียง [ร้าน] ใช่ไหมครับ?"
- **C8/C9 right slot icons** — share QR / ⋮ menu (placeholder now)
- **Shop side · S4 DeeReach** — suggestion cards with cost preview + audience size

### 🟢 Polish
- LINE OA cover + rich menu design
- Onboarding step-dots animation polish
- Dashed border audit (12 changes pending in customer file)
- Inbox-empty state copy (already designed, may need illustration)

---

## Recent changes (Session 8 · April 28-29)

### Major
- ✅ Top bar unified to 3 patterns
- ✅ DeeReach consent UX redesigned (toggle + plea, no checkbox)
- ✅ C3.line mockup added
- ✅ Push.prompt smart trigger strategy
- ✅ Install sheet redesigned · benefit-first hero (home screen mock + spotlit icon)
- ✅ Install.iOS variant (step-by-step)
- ✅ All ดำ+ตาส้ม logo variants removed (26+ instances)
- ✅ New logo SVG + PNG assets generated
- ✅ Brand sheet color variants overhaul
- ✅ Dock label rename (กล่อง→ข้อความ, ฉัน→ตั้งค่า)
- ✅ Dock "ข้อความ" icon (ECG → chat bubble)
- ✅ Onboarding footer-mark sticky-bottom across all screens
- ✅ C5 enhancement (claimed mark + glow + voucher refinement)
- ✅ C2.4 recovery code label removed

### Minor
- C2.1 greeting 3-line break
- C5 voucher 2-line title (ระบุชื่อ + ฟรี 1 แก้ว)
- C7 nudge with shop logo
- ob-canvas min-height 0 (allow shrink for footer position)
- C3 sheet margin 76→18px (less header gap)

---

## User communication style notes

- Thai-mostly, very short directives ("ok", "มา", "ลุย", "ได้")
- Visual-first feedback expected — sketches before implementation
- "ถามจริง" → push back honestly when design is overreach
- Decisive · chooses A/B/C/D when shown options
- Multiple revert cycles common · expect "เอาออกก่อน" after trying things
- Direct iteration: "เกือบดีหละ X → Y"
- Values: mobile-app feel, large fonts, color hierarchy, less branding overload

## Voice spec (น้องแต้ม)

| Context | Example |
|---|---|
| Greeting | "สวัสดีครับ พี่สมศรี" |
| Affirmation | "ครับ" / "เลย" / "ได้เลย" |
| Suggestion | "ผมแนะนำ..." / "ให้ผม..." |
| Empathy | "เข้าใจครับ" / "พี่ครับ..." |
| Excitement | "เยอะมาก!" / "ดีมาก ✦" |
| Soft plea | "อย่าลืมเข้ามาดูนะครับ" |

Avoid: เธอ, คุณ, ครับผม, ขอบพระคุณ (too formal). Stick to ผม / พี่ / ครับ.
