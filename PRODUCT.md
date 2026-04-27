# TaemDee (แต้มดี) — Product Requirements Document

**Audience:** Designer · **Scope:** v1 · **Last updated:** 2026-04-27

---

## 1. What we're building

TaemDee is a digital point-card platform for Thai SME shops (cafés, salons, food stalls). It replaces the paper point card with a friction-free digital experience: customer scans a QR, gets a point, sees progress — no app install, no signup. Shops sign up in 60 seconds and pay **nothing** for the loyalty engine itself.

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
- Permissions are **per-staff toggles** set at invite time: Issue points (always on), Void (default on), DeeReach (off), Top-up (off), Settings (off). Owner can edit anytime.
- DeeBoard surfaces hide/show based on permissions — a staff member without DeeReach permission sees no DeeReach cards; without Top-up sees no credit balance; and so on.

## 3. Brand & voice

- **Bilingual.** Thai script primary, English secondary. Use ร้าน not "merchant", ลูกค้า not "customer".
- **Casual and warm.** No corporate language. Short sentences.
- **Minimal aesthetic.** Lots of whitespace, one action per screen, big tap targets (≥48px).
- **Mobile-first.** Every screen designed for a phone first, scaled up for desktop.

## 4. Proprietary terms — use these in copy

| Term | Meaning |
|---|---|
| **DeeCard™** | The customer's digital point card |
| **DeeBoard™** | The shop's dashboard |
| **DeeReach™** | An outbound message the shop sends (win-back, birthday, new-product, almost-there) |

## 5. v1 screen inventory

### Customer-facing
| # | Screen | Purpose |
|---|---|---|
| C1 | **DeeCard** | Shows point progress (e.g., 7/10), shop logo, theme branding, "Redeem" button when full |
| C2 | **First-point welcome** | After the first scan ever: "+1 point at [Shop]. No signup needed." |
| C3 | **Soft Wall prompt** | Appears at redeem or "Save my points" — two options: Link LINE / Verify Phone |
| C4 | **Redeem state** | DeeCard at full progress (e.g., 10/10). A "Redeem Now" button appears on the card; customer taps to claim. |
| C5 | **Reward claimed** | Success screen after redemption — *"Reward claimed!"* with a subtle live pulse (anti-screenshot). Card resets to 0/N. Simultaneously fires on shop's DeeBoard (S3) with a [Void] button (60-sec window). |
| C6 | **Account menu** | Customer can log out, delete account (PDPA right) |
| C7 | **My Cards** | Logged-in customer sees a list of their DeeCards across all shops they've visited, sorted by closest-to-reward. Tap a row to open the full DeeCard. |

### Shop-facing
| # | Screen | Purpose |
|---|---|---|
| S1 | **Login** | Mobile OTP or LINE Login. No passwords. |
| S2 | **Onboarding (4 steps)** | Shop name + reward goal → logo (AI-generate from name + typography, or upload) → pick a theme → print QR |
| S3 | **DeeBoard home** | One big number + live notification feed + 1–3 DeeReach suggestion cards. Multi-branch shops show a branch selector at top that filters all three. |
| S4 | **DeeReach suggestion card** | Inline in feed: *"23 customers silent 60 days. Send win-back? [Yes · 50 Credit]"* |
| S5 | **Issuance** | Three buttons: Customer Scans / Shop Scans / Phone Entry. Multi-branch shops: staff picks their current branch context at login. |
| S6 | **Point notification** | Guest ID + [Void] button (60-sec window) |
| S7 | **Top-up** | Pick package → PromptPay QR → upload slip → done |
| S8 | **Shop QR print page** | Printable PDF/PNG of the shop's pointing QR |
| S9 | **Theme picker** | Gallery of free themes — one tap to apply |
| S10 | **Settings** | Edit shop name, reward goal, logo, theme |
| S11 | **Team (in Settings)** | Owner-only: invite staff via phone/LINE with per-staff permission toggles. Edit permissions or remove a member anytime. |
| S12 | **Branches (in Settings)** | Owner-only: add/edit/remove branches. Each has its own QR (→ S8). When adding the 2nd branch, owner picks **reward mode**: **Shared** (one reward across all branches, one DeeCard per customer) or **Separate** (each branch has its own reward, one DeeCard per branch per customer). Credits, theme, DeeReach, and staff are always shop-level. Single-branch shops see no branch UI anywhere. |

## 6. Key flows

### A. Customer — first visit (≤3 seconds)
1. Sees QR sticker → scans with phone camera.
2. Browser opens DeeCard URL → "+1 point at [Shop]" (~1 sec).
3. Sees **1/10** points and the reward goal. Walks away.

### B. Customer — return visit
1. Same scan → "+1 point" → progress updates (2/10 → 3/10).
2. At 8/10 the DeeCard reads "**ใกล้แล้ว! อีก 2 แต้ม**" (Almost there! 2 to go).

### C. Customer — reach reward
1. Point lands at 10/10 → celebration animation → **"Redeem Now"** button glows.
2. Customer taps Redeem → **"Reward claimed!"** screen appears (with a subtle live pulse so a screenshot can't fake it).
3. Same moment, the redemption fires on the shop's DeeBoard with a [Void] button (60-sec window) — same trust model as a point issuance.
4. Card resets to 0/10. Staff hands over the reward.

### D. Customer — Soft Wall (preserve identity)
1. Anonymous customer taps "Save my points" banner OR initiates a Redeem.
2. Soft Wall screen: **"Link LINE"** or **"Verify Phone (OTP)"**.
3. Anonymous data merges into permanent account → accessible from any device.
4. Customer now sees **My Cards** (C7) — every shop they've pointed at, in one list.

### E. Shop — onboarding (60-second setup)
1. Open TaemDee → "Sign up free" → phone OTP or LINE login.
2. Type shop name → set reward (e.g., "10 points = free latte").
3. **Logo:** default path is **"Create with AI"** — system generates 3 logo options from the shop name across different typography styles; owner taps the one they like. Escape hatch: **"Upload my own"** for shops that already have a logo.
4. Pick a theme.
5. Download/print Shop QR (S8).
6. Live. First batch of points is free immediately.

### F. Shop — daily use
1. Glance at DeeBoard (S3) → see one big number, e.g., **"23 customers came back this week."**
2. Notification feed updates live as points are issued. Tap [Void] within 60 sec if any are wrong.
3. Optional: tap a DeeReach suggestion (S4) — *"Send birthday wishes to 5 customers? [Yes · 10 Credit]"* → done.

### G. Shop — top-up
1. Credit balance low → DeeBoard alerts.
2. Tap "Top Up" → pick package (100 / 200 / 1,000 THB) → PromptPay QR shown.
3. Pay via banking app → upload slip → Slip2Go verifies → credits added (~5 sec).
4. If verification fails: "Submit for review" — manual SLA same business day.

### H. Shop — invite staff (multi-staff only)
1. Owner opens Settings → Team (S11) → "Invite staff" → enters phone or LINE ID.
2. Sets permissions via checklist: Void (default on), DeeReach, Top-up, Settings. Issue points is implicit.
3. Invitation link sent → staff logs in via OTP/LINE.
4. Staff lands on DeeBoard with surfaces hidden based on their permissions.
5. Owner can edit permissions or remove a member anytime from S11.

### I. Shop — add a branch (multi-branch only)
1. Owner opens Settings → Branches (S12) → "Add branch" → enters name + address.
2. **On the 2nd branch only:** owner picks **reward mode**:
   - **Shared** (default) — one reward across all branches. Customer DeeCards accumulate points across every branch toward a single card. Redemption works at any branch.
   - **Separate** — each branch has its own reward goal. Customers get a DeeCard per branch. Visiting 3 branches = 3 cards in their My Cards list (C7).
3. System generates a QR for the new branch (printable via S8).
4. From now on: DeeBoard shows a branch selector; staff pick their current branch at login.

**Mode is locked** once the 2nd branch is added. Changing mode later requires data migration — deferred to v2.

## 7. The DeeBoard home screen — design priorities

This is the screen the shop owner sees most. It must communicate three things at a glance:

1. **One headline number.** *"23 customers came back this week."* Big, single number, weekly cadence.
2. **Live point feed.** Real-time entries as points are issued. Each row: time + guest ID + method + [Void].
3. **1–3 DeeReach suggestion cards.** Each is one tap to act on, with cost shown. Owner can ignore or tap Yes.

**Anti-pattern:** no multi-tab analytics, no charts, no segments, no campaign builders. The shop owner cannot configure anything from this screen.

## 8. UI principles

- **Speed.** Point interaction must complete in <3 seconds. Use HTMX partial updates everywhere.
- **One action per screen.** No multi-step forms in v1.
- **Tap targets ≥48px.** Cashier may have wet fingers.
- **No silent spinners.** Always show text ("Verifying slip…").
- **Bookmarkable URLs.** Customer can bookmark their DeeCard.
- **Bilingual copy.** Default Thai; English where the shop sets locale.

## 9. Themes — DeeCard library

- **3–5 free themes** to start: Classic Cafe, Modern Minimal, Night Mode, Pastel, Thai Traditional.
- Every theme: full HTML template with a logo slot. **No per-shop CSS overrides in v1.** Logo upload is the only customization.

## 10. Anti-fraud surface (in DeeBoard)

Shop owner sees fraud-relevant signals inline — no separate page:

- Each point **or redemption** notification carries [Void] (60-sec window).
- Pattern alerts show as a banner at the top of the feed: *"Unusual: 8 points in 2 minutes."*
- Sane defaults baked in (1 point/customer/day, 60-min per-device rate limit). **No configuration UI in v1.**

## 11. Revenue model (designer context, not a screen)

- **Free forever:** DeeCard, DeeBoard, pointing, voiding, redemption, themes. No fee on top-ups.
- **Pay-per-send:** DeeReach messages (~1–2 Credit / LINE, ~3 Credit / SMS) — deducted on tap.

DeeReach is the **sole revenue source** in v1. In-app costs are always shown in **Credit** (THB-to-Credit ratio TBD). Shops top up Credit with real THB via PromptPay (§6.G).

**Critical UX requirement:** every paid action shows the cost **inside the same tap that approves it** — never as a surprise. Example: `[Yes · 50 Credit]`, not `[Yes]` followed by a confirmation modal.

## 12. PDPA (compliance surface)

- Customer data captured only after Soft Wall consent (phone or LINE ID only).
- Anonymous profiles auto-expire after 12 months of inactivity.
- "Delete my account" lives in the Customer Account menu (C6) — one tap + confirmation. Points are anonymized in shop reporting (counts kept, identity removed).

## 13. Offers

**An offer is a promise — system-funded or shop-funded — that gets redeemed later as points, credits, items, or gifts.** The customer never sees the word "offer"; they see a specific reward (*"Free pastry waiting at Café Tana"*).

### Directions (who → who)

| Direction | Sponsor | Examples | v1? |
|---|---|---|---|
| **System → Shop** | TaemDee | Welcome credits, referral bonus, comp / make-good | ✅ |
| **Shop → Customer** | Shop's credits | Free point, bonus points, free item | ✅ |
| System → Customer | TaemDee | DeeWelcome points (TaemDee-funded first-visit bonus) | v2 |
| Customer → Customer | TaemDee | Gifted points (needs cross-shop wallet) | v3 |

### Shop → Customer kinds in v1

| Kind | What it does |
|---|---|
| `free_point` | One-tap "give them a point" — birthday, apology, comp |
| `bonus_point_count` | Hand them N points at once (welcome bonus = 3) |
| `free_item` | Specific item — *"Free pastry"* — different from the standard reward |

Deferred (need extra UX): `point_multiplier` (double-points Tuesday), `free_reward` (skip points, claim reward), `free_gift` (physical merch), `discount` (% off — needs cashier UI).

### Where customers see offers

Offers do **not** get their own screen. They appear:
- **As a banner on the customer's DeeCard** when active & unredeemed
- **In the redemption flow** (*"Tap to claim your free pastry"*)
- **In LINE/SMS messages** sent via DeeReach

### Where shops see offers

Shops do **not** get an "offers" page either. Offers appear:
- **Inside DeeReach suggestion cards** (*"Send birthday wishes with a free-point gift to 5 customers? [Yes · 10 Credit]"*)
- **In the live DeeBoard feed** when a customer redeems one

### Relation to existing models

Offers are **promises**; the existing tables (`Point`, `CreditLog`, `Redemption`) are the **ledger**. Redeeming an offer creates the appropriate ledger record(s).

## 14. Referrals

A referral is a two-party flow: a referrer shares a link, the referee acts on it, both get a reward (delivered as Offers, §13).

### Directions

| Direction | Sponsor | Grows | v1? |
|---|---|---|---|
| **Shop → Shop** | TaemDee | New shops on the platform | ✅ |
| Shop → Customer | Shop's credits | New customers for that shop | v2 |
| Customer → Customer | TaemDee | Customers across the network (needs cross-shop wallet) | v3 |

### v1 — Shop → Shop only

- An existing shop's owner sees a **"Refer a shop"** action in their dashboard menu, generating a unique link (`/shop/register?ref=<code>`).
- A new shop signup that includes a valid `ref` code is recorded with `referred_by_shop_id`.
- Once the new shop completes onboarding (logo + first point issued), **both shops receive a `credit_grant` Offer** (System → Shop, §13). Amount TBD.
- Tracked in a `Referral` record; reward delivered via the Offer system.

### Why not Shop → Customer / Customer → Customer in v1

- **Shop → Customer** needs cashier UX to confirm a referee's first visit, a per-shop shareable code, and a two-party point-grant flow that hasn't been designed.
- **Customer → Customer** is bound to DeePass (cross-shop wallet) which is v3.

## 15. Missions (gamification)

**A mission is a challenge — a behaviour pattern that, when completed, grants a reward.** Where Offers (§13) are *promises already granted, waiting to be claimed*, Missions are *promises gated behind a goal*. Completing a mission creates an Offer.

**Example.** *"Visit 3 times this week → free shop t-shirt."* On the 3rd visit, an Offer ("Claim your t-shirt at the counter") appears on the customer's DeeCard and the standard redemption flow (§6.C) takes over.

### Why missions on top of offers
Offers grant value immediately — useful for birthdays, comps, win-back. Missions grant value **only after the customer demonstrates a behaviour** — driving repeat visits, menu exploration, off-hours traffic, friend referrals. The two layers compose: a mission's reward is an Offer.

### Mission shape

| Field | Example |
|---|---|
| Goal | "visit N times" / "spend N THB" / "try N items" / "bring a friend" |
| Window | "this week" / "this month" / "no limit" |
| Reward | An Offer kind (§13): `free_item`, `bonus_point_count`, `credit_grant` |
| Audience | All customers / claimed only / win-back targets |

### v1 — single mission kind

Ship one mission kind that's universally useful and doesn't need a builder UI:

- **`visit_streak`** — *"Visit N times in M days → free [item]"*. Shop owner picks N, M, and the reward from a short list. One toggle in Settings → Missions, defaulting off.

Tracking: count un-voided points in the window. When N reached → an Offer appears on the DeeCard; redemption follows §6.C.

### Where customers see missions

Like Offers, missions do **not** get their own screen. They appear:
- **As a banner on the DeeCard** while in progress — *"2 of 3 visits this week — 1 more for a free t-shirt!"*
- **As a DeeReach nudge** when 1 visit short of completion (*"ใกล้แล้ว! แวะมาอีกครั้งก่อนอาทิตย์"*)
- **In the redemption flow** when claiming the reward

### Where shops see missions

Owner has a single Settings → Missions row: on/off + N + window + reward. No builder, no rule engine, no analytics. Active missions show a small completion counter on the DeeBoard headline ("3 customers earned a t-shirt this week").

### v2+
- More mission kinds: spend threshold, menu coverage, off-hours visits, friend-referral.
- Per-customer missions (DeeReach-driven, *"Sirikorn — try our new latte before Friday"*).
- System-funded missions (TaemDee sponsors network-wide challenges).

### Relation to existing models
Missions are a separate layer (`Mission`, `MissionProgress`); on completion they **create** an Offer (§13). The Offer machinery handles delivery and claim — no second redemption path.

## 16. Not in v1 (roadmap + drops)

| Feature | Ships in | Why deferred |
|---|---|---|
| Analytics dashboards | v2 | DeeBoard shows ONE number in v1 |
| Offer kinds: point_multiplier, free_reward, free_gift, discount | v2 | Need extra UX surfaces (cashier, inventory) |
| System → Customer offers (DeeWelcome) | v2 | Requires customer-discovery surface |
| Customer → Customer offers (gifted points) | v3 | Bound to DeePass |
| Shop → Customer referral | v2 | Cashier "first visit" UX not designed |
| Customer → Customer referral | v3 | Bound to DeePass |
| Mission kinds beyond `visit_streak` | v2 | Need cashier/inventory UX |
| Per-customer & system-funded missions | v2 | Bound to DeeReach segmentation |

## 17. Tech notes (for designer awareness)

- Web app, HTMX-powered. No native apps in v1.
- Mobile-first responsive — desktop is a scaled-up phone view, not a separate design.
- Real-time notifications via server-sent events.
- Customer identity: `localStorage` + cookies → Soft Wall converts to permanent LINE/phone account.
