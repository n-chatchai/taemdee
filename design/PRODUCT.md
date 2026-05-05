# TaemDee (แต้มดี) — Product

**Last updated:** 2026-05-05

## 1. What it is

A digital point-card platform for Thai SME shops (cafés, salons, food stalls). Customer scans a QR sticker, gets a stamp, sees progress — no app install, no signup. Shops sign up in minutes. The loyalty engine itself is **free**; revenue comes only when a shop sends an outbound message (**DeeReach**).

## 2. Users

- **Customer** — walks in, scans, wants a free coffee eventually. Won't install an app or fill a form. Mid-range Android, attention span ≈ seconds at the counter.
- **Shop owner** — does everything. Decisions are yes/no taps, never forms. Phone is the primary device.
- **Shop staff (optional)** — invited by the owner. Per-staff toggles: issue, void, DeeReach, top-up, settings. The dashboard adapts to permissions.

## 3. Voice & brand

- **Bilingual.** Thai script primary, English secondary. ร้าน, ลูกค้า — never "merchant" or "customer".
- **น้องแต้ม** is the customer-side helper character. First-person ผม, addresses the customer as "พี่ + nickname". Shop side stays generic / neutral.
- **One primary action per view.** Mobile-first, scaled up for desktop.

## 4. Terms

| Term | Meaning |
|---|---|
| **DeeCard** | The customer's digital point card |
| **แดชบอร์ด** | The shop's dashboard |
| **DeeReach** | An outbound message the shop sends to its customers |

## 5. Screens

### Customer (PWA, no login)
| # | Route | Purpose |
|---|---|---|
| C1 | `/card/{shop_id}` | DeeCard — daily progress + reward summary |
| C2 | first-scan flow | Greeting → first stamp → optional "save" via LINE / Google |
| C4 | C1 at threshold | "เปิดของขวัญ" CTA — open to guests; soft signup link below |
| C5 | redeem result | Voucher saved to "ของขวัญ" tab; new blank card starts immediately |
| C6 | `/card/account` | Profile, preferred channel, privacy, logout, delete |
| C7 | `/my-cards` | List of all DeeCards across shops + onboarding todos |
| C8 | `/my-qr` | Customer's QR for shops that scan customers |
| C9 | `/story/{shop_id}` | Shop's story / menu / vibe |
| Gifts | `/my-gifts` + `/voucher/{id}` | พร้อมใช้ / ใช้แล้ว · tap "ใช้" → fullscreen QR for staff |
| Inbox | `/my-inbox` | DeeReach messages addressed to this customer |
| Install | sheet | Add-to-home prompt |

### Shop (login required)
| # | Route | Purpose |
|---|---|---|
| S1 | `/shop/login` · `/staff/pin-login` | LINE / Google / username + 6-digit PIN |
| S2 | `/shop/onboarding` | Name + reward goal → logo → theme → print QR |
| S3 | `/shop/dashboard` | One headline number + live feed + DeeReach suggestion cards + แต้มดีแนะนำ todos |
| S5 | `/shop/issue/*` | Customer-scans · shop-scans · phone-entry. Method toggles per shop |
| S6 | feed-row sheet | +1 / รับรางวัล with `[Void]` for a short window |
| S7 | `/shop/topup` | Pick package → PromptPay QR → upload slip (Slip2Go verify) |
| S8 | `/shop/qr` | Printable QR sheet |
| S9 | `/shop/themes` | Theme picker |
| S10 | `/shop/settings` | Name, reward goal, logo, theme, location, hours, login methods |
| S11 | `/shop/team` | Owner-only · invite staff with permission toggles |
| S12 | `/shop/branches` | Owner-only · shared vs separate reward mode |

### Shared
| # | Route | Purpose |
|---|---|---|
| Picker | `/` (when device has both shop + customer cookies) | Two tiles: ร้านค้า / ลูกค้า. Re-entry via `/switch` |

## 6. Flows

**Customer onboarding.** Anonymous on first scan — points issue immediately. The "save your card" prompt at ≥3 stamps is soft, never required. Linking LINE / Google merges identity onto a shared `users` row so stamps survive across devices. iOS PWA Safari hand-off uses a transfer-token bounce so the OAuth callback's cookie reaches the PWA cleanly; stale PWA cookies that miss the Set-Cookie follow `customers.merged_into_id` to the live identity.

**Issuance.** Three methods: customer scans the shop's QR, shop scans the customer's QR, or shop types the customer's phone. Each is a per-shop toggle. Optional anti-rescan cooldown.

**Auto-redeem.** Hitting `shop.reward_threshold` inside `issue_point()` flips the stamp into a redemption automatically — works for every issuance path (customer scan, shop scan, phone entry, manual grant, DeeReach `bonus_stamp_count`). The voucher lands in the customer's "ของขวัญ" tab.

**Voucher use.** Customer taps "ใช้" → `served_at` stamps now → fullscreen QR shows for staff (5-min audit window). The shop dashboard's feed picks this up live. If the customer's voucher is already pending and the shop scans them within 30 minutes, the scan flips `served_at` instead of issuing a new stamp — handles the "stamp the customer who's collecting their free coffee" race.

**Live updates.** `/sse/me` is the customer's per-tab SSE channel. Events: `inbox-update` (unread count), `gifts-update` (active voucher count), `stamped`, `redeemed`. Cross-worker fan-out via Postgres `LISTEN/NOTIFY`. Shop side has the equivalent on `shop.id` driving the live feed.

**DeeReach delivery.** Routed by the customer's preferred channel; otherwise waterfall (cheapest viable). Cost is locked when the shop taps "Yes" and reconciled per recipient — failed sends refund automatically.

| Channel | Cost | Notes |
|---|---|---|
| `inbox` | 0 Cr | Always reachable. Free fallback. |
| `web_push` | 0.5 Cr | PWA installed + permission granted |
| `line` | 1.0 Cr | LINE OA push (customer must be friended) |
| `sms` | 2.0 Cr | Phone-only customers |

Trigger kinds the platform suggests today: `almost_there`, `win_back`, `unredeemed_reward`, `new_customer`. Per-customer rate limit + per-shop mute live in `customer_shop_mutes`.

## 7. Principles

- **Speed.** Issuance and redemption flows must complete in under 3 seconds.
- **One tap, one cost.** Every paid action shows the credit cost on the same button that approves it. No surprise modals.
- **Bookmarkable URLs.** A customer can pin their DeeCard.
- **No login wall on PWA.** The customer side is connect-only. Logout returns to `/my-cards`, never to a marketing page.

## 8. Trust & PDPA

- Every issuance and redemption row carries a `[Void]` action for a short window. Voiding releases the underlying points.
- Pattern alerts surface unusual activity (e.g. "8 points in 2 minutes") to the shop.
- Customer data is only captured after explicit consent. Anonymous profiles expire after 12 months. Account deletion is a single tap.
- Per-shop mute: customers can silence one shop without losing reach from others. Muted recipients fall back to the inbox channel.

## 9. Offers & referrals

- **Offers** (welcome credit, free stamp, bonus-stamp count, etc.) are funded by either the platform or the shop and create ledger records on redemption. Surfaced contextually — no dedicated offer pages.
- **Referrals** are Shop → Shop today. An existing shop generates `/shop/login?ref=<code>`; on the new shop's first onboarding completion, both sides receive a `credit_grant` Offer.

## 10. Monetization

Freemium. The loyalty engine is free. Revenue comes from DeeReach credits (PromptPay top-up). See pricing table in §6.

## 11. Tech notes

- Web app, FastAPI + Jinja + HTMX + small inline scripts. No native apps.
- Mobile-first responsive — desktop is a scaled-up phone view.
- Real-time via SSE (`/sse/me` customer · `/shop/events` shop) over Postgres `LISTEN/NOTIFY`.
- Background jobs (DeeReach send, reconciliation) run in RQ workers.

## 12. Future

| Feature | Status |
|---|---|
| Analytics dashboards | future |
| Advanced offer kinds (`point_multiplier`, `discount`, ...) | future |
| Customer-side discovery (DeeWelcome) | future |
| Cross-shop wallet (DeePass) | future |
| Shop → customer referral | future |
| Gamification / missions | future |
