# TaemDee (แต้มดี) — Product Requirements Document

**Audience:** Core Product & Engineering · **Status:** Current · **Last updated:** 2026-04-28

---

## 1. What we're building

TaemDee is a digital point-card platform for Thai SME shops (cafés, salons, food stalls). It replaces the paper point card with a friction-free digital experience: customer scans a QR, gets a point, sees progress — no app install, no signup. Shops sign up in minutes and pay **nothing** for the loyalty engine itself.

We earn only when a shop taps "Send" on an outbound, system-generated message we surfaced (**DeeReach™**).

## 2. Users

### Customer
- Walks into a shop, sees a QR sticker, scans with phone camera.
- Wants a free coffee eventually. Will not install an app. Will not fill a form.
- Mid-range Android phone, 4G or shop Wi-Fi, attention span = seconds at the counter.

### Shop Owner
- One person doing everything — cashier, marketer, accountant.
- Decision capacity: yes/no taps. **Never forms or rules.**
- Time per interaction: ~10 seconds between customers.
- Devices: phone (primary, all day), tablet + occasional desktop (evenings, top-ups).

### Shop Staff (optional)
- Invited by the Shop Owner via phone OTP or LINE.
- Permissions are **per-staff toggles** set at invite time: Issue points, Void, DeeReach, Top-up, Settings. Owner can edit anytime.
- Dashboard surfaces adapt based on permissions.

## 3. Brand & Voice Principles

- **Bilingual:** Thai script primary, English secondary. Use ร้าน not "merchant", ลูกค้า not "customer".
- **Casual and warm:** No corporate language. Short sentences.
- **Helper character — "น้องแต้ม":** The customer-side voice is a friendly helper character named **น้องแต้ม**. Speaks in first-person ("ผม"), addresses the customer with "พี่" + their nickname.
- **Shop voice:** Generic, no character — the shop side stays neutral. Greetings like "สวัสดี" without a name.
- **Mobile-first & Minimal:** Lots of whitespace, one primary action per view. Designed for a phone first, scaled up for desktop.

## 4. Proprietary Terms

| Term | Meaning |
|---|---|
| **DeeCard™** | The customer's digital point card |
| **DeeBoard™** | The shop's dashboard |
| **DeeReach™** | An outbound message the shop sends (win-back, birthday, new-product, almost-there) |

## 5. Screen Inventory (Route Identifiers)

The codebase routes, templates, and comments are heavily mapped to these identifiers. While the visual UX may change, these represent the constant logical views of the application.

### Shared
| # | Screen | Purpose |
|---|---|---|
| Role picker | **เข้าใช้งานเป็น...** | Shown at `/` when one device carries a valid shop session AND a valid customer session — two tiles ("ร้านค้า" → /shop/dashboard, "ลูกค้า" → /my-cards) so an owner who also collects points elsewhere isn't auto-shoved into the shop side. Single-cookie devices skip the picker and redirect as before. |

### Customer-facing
| # | Screen | Purpose |
|---|---|---|
| C1 | **DeeCard** | Daily point card — shows progress, shop branding, reward summary. |
| C2.1 | **First scan · Greeting** | First-ever scan. น้องแต้ม character introduces itself and asks for nickname. |
| C2.2 | **First scan · First stamp** | Reveal of the first point (+1/N) and the reward goal. |
| C2.3 | **First scan · Save & signup** | Soft Wall: Offers Link LINE or Verify Phone (OTP) for permanent save, or skip. |
| C3 | **Phone OTP** | Phone OTP form for customers who chose "Verify Phone". |
| C4 | **Redeem state (reward.almost)** | DeeCard at reward threshold. Primary "เปิดของขวัญ" CTA — open to guests; soft optional signup link below. |
| C5 | **Reward claimed (reward.claim)** | Celebration screen — voucher saved to "ของขวัญ" tab for next visit. New blank card starts immediately. |
| Gifts | **ของขวัญของพี่ + voucher.use** | List of earned vouchers (พร้อมใช้ / ใช้แล้ว). Tapping "ใช้" stamps it served immediately and shows a fullscreen QR for staff to scan within a 5-minute audit window. |
| C6 | **Account menu** | Profile, preferred notification channel toggle, privacy submenu, logout, delete account (PDPA). |
| C7 | **My Cards** | List of all DeeCards across shops. Has scan-camera button. |
| C8 | **My QR** | Guest QR for shops that use "Shop scans customer" issuance method. |
| C9 | **Shop Story** | Customer-facing emotional layer — shop's story, menu, reviews. |
| Install | **Add to home** | Bottom sheet with install steps. |

### Shop-facing
| # | Screen | Purpose |
|---|---|---|
| S1 | **Login** | Mobile OTP or LINE Login. |
| S2 | **Onboarding** | Shop name + reward goal → logo → pick a theme → print QR. |
| S3 | **DeeBoard home** | One big number + live notification feed + DeeReach suggestion cards. |
| S4 | **DeeReach suggestion card** | Inline in feed: one-tap outbound message approval. |
| S5 | **Issuance** | Issuance methods (Customer Scans / Shop Scans / Phone Entry). |
| S6 | **Point notification** | Guest ID + [Void] button. |
| S7 | **Top-up** | Pick package → PromptPay QR → upload slip. |
| S8 | **Shop QR print page** | Printable PDF/PNG of the shop's pointing QR. |
| S9 | **Theme picker** | Gallery of themes to apply. |
| S10 | **Settings** | Edit shop name, reward goal, logo, theme, location, contact phone, opening hours. |
| S11 | **Team (in Settings)** | Owner-only: invite staff with per-staff permission toggles. |
| S12 | **Branches (in Settings)** | Owner-only: add/edit/remove branches and set reward mode (Shared/Separate). |

## 6. Key Mechanisms & Flows

### Customer Onboarding & Retention
- **First Visit:** A brief, warm onboarding flow introduces "น้องแต้ม" and asks for a nickname. Points are issued instantly without forced account creation (anonymous guest mode).
- **Return Visits:** Extremely fast (≤2 seconds). A scan updates the progress in place without re-onboarding.
- **Identity Claim (Soft Wall):** Guests are encouraged — never required — to link their LINE account or phone number (OTP) so their points and earned vouchers survive across devices. Redemption itself is open to guests as of the May 1 design rev; the C4 redeem screen surfaces an optional "สมัครเพื่อเก็บบัตรไม่ให้หาย" link below the primary "เปิดของขวัญ" CTA.

### Shop Operations
- **Frictionless Setup:** Phone/LINE login, minimal configuration (name, reward, logo, theme). First batch of points is free.
- **Daily Issuance:** Staff can issue points via shop-side scanning, customer-side scanning (QR stickers), or phone entry. Configurable cooldowns prevent abuse.
- **Redemption:** Customers (including anonymous guests) initiate redemption on their phone when reaching the goal — the resulting voucher lands in their "ของขวัญ" tab to be used on the next visit. The shop's dashboard reflects the redemption instantly with a window to void if fraudulent.
- **Multi-Branch:** Shops can add branches, choosing between "Shared" (one reward goal across all) or "Separate" (independent goals per branch) reward modes.

## 7. Product Principles

- **Speed:** Point issuance and redemption flows must complete in under 3 seconds. Use partial DOM updates (e.g., HTMX).
- **One action per view:** Avoid complex, multi-step forms.
- **Actionable Costs:** Every paid action must show the cost in credits **inside the same tap that approves it** (no surprise confirmation modals).
- **Bookmarkable URLs:** Customers can bookmark their DeeCard for easy access.

## 8. Trust & Compliance

- **Anti-Fraud Mechanics:** 
  - Every point or redemption notification carries a `[Void]` action with a short time window.
  - An anti-rescan cooldown prevents rapid scanning (configurable per shop, defaults to 0).
  - Pattern alerts surface unusual activity (e.g., "8 points in 2 minutes") directly to the shop.
- **PDPA / Privacy:** Customer data is captured only after explicit consent (Soft Wall). Anonymous profiles expire after 12 months. Account deletion is a single-tap process.

## 9. Offers & Referrals

- **Offers:** Promises funded by either the system or the shop (e.g., `credit_grant`, `free_point`, `bonus_point_count`, `free_item`). They act as digital vouchers that create ledger records upon redemption. They are presented contextually (on cards or inside messages) rather than on dedicated "offer pages".
- **Referrals:** Current implementation supports Shop-to-Shop referrals. An existing shop generates a link, and when a new shop signs up and completes onboarding, both receive a `credit_grant` Offer.


## 10. Monetization (DeeReach)

TaemDee operates on a **freemium** model. The core loyalty engine (DeeCard, DeeBoard, point issuance, voiding, redemption, themes) is completely free for shops to use. 

Our sole revenue stream is **DeeReach™** — outbound, system-generated marketing messages that shops send to their customers (e.g., win-back campaigns, birthday rewards, almost-there nudges). 

Shops pre-purchase **Credits** using real THB via PromptPay. The cost of sending a DeeReach campaign is calculated dynamically based on the delivery channel required for each customer in the target audience.

### Push Channel Pricing Strategy

The platform routes messages based on the customer's **Preferred Channel**. If the customer has not set a preference in their Account Menu (C6), the system defaults to a **Waterfall strategy**, automatically routing through the cheapest available channel the customer has unlocked. We only charge for the channel actually used.

1. **DeeCard Inbox (Cheapest Fallback)**
   - **Suggested Cost:** ~0.1 Credits per message.
   - **Mechanism:** For anonymous guests or customers who haven't opted into Web Push, LINE, or SMS, the message silently drops into an "Inbox" tab on their DeeCard. 
   - **Rationale:** Ensures 100% campaign coverage. Pure margin for the platform since there are no external API costs, while establishing a baseline value for reaching every customer.

2. **PWA Web Notification (Low Cost)**
   - **Suggested Cost:** ~0.5 Credits per message.
   - **Mechanism:** Delivered via Web Push API for customers who have saved the DeeCard to their home screen and allowed notifications.
   - **Rationale:** Zero variable cost for TaemDee. High margin, and incentivizes shops to encourage customers to "Add to Home Screen".

3. **LINE Message (Moderate)**
   - **Suggested Cost:** 1 Credit per message.
   - **Mechanism:** Delivered via LINE Official Account Push API for customers who linked their LINE account during the Soft Wall flow.
   - **Rationale:** High visibility. TaemDee passes on the LINE API per-message cost with a slight markup.

4. **SMS (Most Expensive)**
   - **Suggested Cost:** 2 Credits per message.
   - **Mechanism:** Delivered via an SMS gateway for customers who verified via OTP but haven't linked LINE or enabled Web Push.
   - **Rationale:** Universal reach, but carries the highest hard cost to the platform. The THB-to-Credit conversion ratio must include a sufficient safety margin to absorb fluctuating SMS carrier costs.

### Credit Calculation: Charge on Delivery

**Critical UX requirement:** The shop owner must never be surprised by a cost, and they only pay for successful deliveries.

When a DeeReach suggestion card appears on the DeeBoard, the system pre-calculates the maximum estimated cost.

- **Example:** *"Send birthday wishes to 10 customers? [Yes · Est. 15 Credit]"*
  *(Calculation breakdown hidden from user: 5 on Web Push @ 0.5 + 3 on LINE @ 1.5 + 2 on SMS @ 4.0 = 15.0 Credits)*
- Tapping "Yes" places a **hold (lock)** on the estimated 15 credits.
- **Delivery Reconciliation:** The system attempts delivery. If a Web Push fails or an SMS bounces, the unspent credits are automatically unlocked and refunded to the shop's balance.

### Anti-Spam & Opt-Outs (PDPA)

To protect the platform's reputation and prevent customers from mass-blocking the TaemDee LINE account:
- **Per-Shop Muting:** Customers can mute notifications from a specific shop (e.g., *Café Tana*) without losing updates from other shops. Muted customers default to the cheapest "DeeCard Inbox" fallback.
- **Platform Rate Limiting:** The system enforces a hard cap (e.g., 1 DeeReach message per customer per shop every 14 days) to prevent aggressive spamming.

## 11. Future Roadmap

| Feature | Status | Description |
|---|---|---|
| Analytics dashboards | Future | Currently, DeeBoard focuses on a single core metric. |
| Advanced Offer Kinds | Future | `point_multiplier`, `free_reward`, `free_gift`, `discount` (requires more complex cashier UX). |
| Customer Discovery | Future | System → Customer offers (DeeWelcome). |
| Cross-Shop Wallet (DeePass) | Future | Customer → Customer offers and referrals. |
| Shop → Customer Referral | Future | Cashier "first visit" UX. |
| Gamification (Missions) | Future | Behavior-driven challenges (e.g., `visit_streak`). See below. |

### Gamification (Missions) Overview (Deferred)

Missions are challenges that, when completed, generate an Offer. 
- **Example:** *"Visit 3 times this week → free shop t-shirt."*
- **Mechanism:** Count un-voided points in a window. When the goal is reached, an Offer appears on the DeeCard.
- **Why Deferred:** Requires a gamification rule engine and additional cashier/inventory UX support.

## 12. Tech Notes
- Web app, HTMX-powered. No native apps.
- Mobile-first responsive — desktop is a scaled-up phone view.
- Real-time notifications via server-sent events.
