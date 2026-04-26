# TaemDee — TODO

Last updated: 2026-04-25 · Spec: [PRODUCT.md](PRODUCT.md) · Deploy runbook: [RELEASE.md](RELEASE.md) · Designer drop: `taemdee-final-thai.html`

## Where we are

**v1 functionally complete in dev/staging.** All releases R1–R6b + R8 + R10 shipped. 94 tests passing.

- ✅ All 22 designer screens ported into Jinja templates (Thai-first)
- ✅ Customer flow: scan → first-stamp celebration → DeeCard → redeem → claim screen
- ✅ Soft Wall + My Cards (claimed customer's cross-shop list)
- ✅ Shop flow: phone-OTP/LINE login → onboarding → DeeBoard with SSE live feed → settings (team, branches, refer-a-shop) → QR print
- ✅ Live SSE feed on DeeBoard with 60-sec [Void] window per row
- ✅ Multi-staff with per-staff permissions
- ✅ Multi-branch with reward-mode picker (locked at 2nd branch)
- ✅ DeeReach: suggestion engine + send pipeline + credit deduction (R6a + R6b)
- ✅ Offers: model + service (system→shop credit_grant, shop→customer free_stamp / bonus_stamp_count / free_item)
- ✅ Shop→Shop referrals: `/shop/register?ref=<code>` + auto-grant on onboarding
- ✅ PDPA: customer-initiated `/card/account/delete` scrubs PII
- ❌ External integrations (real SMS / LINE Messaging API / Slip2Go) — needs creds
- ❌ Production hardening (TLS, backups, Sentry, rate limit)

---

## Releases

### R1 — Shop login + onboarding

**Demo:** open URL → sign up by phone or LINE → set shop name + reward → land on DeeBoard → download QR PDF.

- [ ] Port S1 Login from `taemdee-final-thai.html` (replaces current register.html)
- [ ] New `/shop/onboard` route + S2.1 template (shop name + reward)
- [x] S2.2 logo — typography-based AI gen with regenerate (per PRD §6.E). Upload escape hatch still deferred.
- [ ] Skip S2.3 theme picker for R1 — default theme used
- [ ] Port S3 DeeBoard *minimal* — headline number, static feed from DB, DeeReach card as placeholder (no live updates yet)
- [ ] Port S8 Shop QR print page — add `segno` dep, render QR for `/scan/<shop_id>`
- [ ] Extract designer CSS into `static/css/app.css`

### R2 — Customer stamp + view

**Demo:** print QR → scan with phone → see 1/10 stamps → scan tomorrow → 2/10 → same-day re-scan silently capped.

- [ ] Port C1 DeeCard into `templates/themes/default.html`
- [ ] Port C2 First-stamp welcome (new template, conditional render on first scan)

### R3 — Redemption + void

**Demo:** customer taps Redeem at 10/10 → "Reward claimed!" → shop's DeeBoard live feed shows redemption with [Void] for 60 sec.

- [ ] Port C4 Redeem state (button on full DeeCard)
- [ ] Port C5 Reward claimed
- [ ] Port S6 Stamp notification (toast/banner pattern)
- [ ] Wire **SSE on S3** (real-time stamp/redemption events)

### R4 — Soft Wall + My Cards

**Demo:** anonymous customer claims account via OTP/LINE → sees all their cards across shops at `/my-cards`.

- [ ] Port C3 Soft Wall (replaces inline button)
- [ ] **Request from designer:** C6 Account menu (logout, delete account, language)
- [ ] Port C7 My Cards + new `GET /my-cards` route
- [ ] Customer-side LINE Login claim — currently OTP-only (`POST /card/claim/line`)

### R5 — Multi-staff + branches

**Demo:** invite a friend's phone as Void-only staff. Add a 2nd branch, pick reward mode, branch selector appears in DeeBoard.

- [ ] Port S5 Issuance — 3 buttons + branch picker
- [ ] Port S7 Top-up + confirm screens (UI only — Slip2Go integration is R7)
- [ ] Port S9 Theme picker
- [ ] Port S10 Settings home
- [ ] Port S11 Team
- [ ] Port S12 Branches (incl. reward-mode picker — request from designer)
- [ ] **Request from designer:** S3 staff variant with permission-gated surfaces hidden

### R6 — DeeReach (revenue) — partial ✅✅⬜

**Demo (dev):** lapsed customers seed → DeeBoard shows suggestion cards → tap → "send" logged to journal → credits drop.

- [x] Model: `DeeReachCampaign`
- [x] Suggestion engine — win-back, almost-there, unredeemed-reward
- [x] Send pipeline — atomic credit deduction + Campaign + CreditLog
- [x] Routes: `POST /shop/deereach/send`
- [x] Permission gate: `require_permission("can_deereach")` — staff need explicit perm
- [x] Tests: 15+ for suggestions and send
- [ ] **R6c — External:** LINE Messaging API integration (replace `_dispatch` stub)
- [ ] SMS fallback for non-LINE customers
- [ ] birthday + new_product kinds (need new fields)

### R7 — Top-up via Slip2Go (real money) ⚠

**Demo:** owner taps Top Up → pays PromptPay → uploads slip → Slip2Go verifies → credits land in <5 sec.

- [ ] Slip2Go API integration in `services/topup.py`
- [ ] **External:** Slip2Go API key
- [ ] **External:** object storage for slip images (R2 / S3 / Supabase Storage)
- [ ] Wire S7 forms to actual upload + verification

### R8 — Offers + Shop→Shop referrals — done ✅

- [x] `Offer` model (polymorphic source/target)
- [x] `services/offers.py` — `grant_credit_to_shop`, `grant_offer_to_customer`, `redeem_offer`, `list_active_offers_for_customer`
- [x] `Referral` model
- [x] `/shop/register?ref=<code>` landing — referrer name shown in the required-notice banner
- [x] "แนะนำเพื่อน" surface in S10 Settings (`/shop/refer`) with copy-link button
- [x] On referee onboarding completion → both shops get `credit_grant` Offers (100 credits each)
- [x] Tests: full referral cycle + idempotence + offer grants + redemption
- [ ] Wire Offers into DeeReach send so a campaign can carry an offer (deferred — independent of v1 closeout)
- [ ] Customer DeeCard banner showing active offers (deferred — needs designer pass)

### R9 — Production hardening + soft launch

**Demo:** open public registrations.

- [ ] **External:** real SMS for production OTP login (currently `NotImplementedError`)
- [ ] TLS (Let's Encrypt + Caddy or Nginx with auto-renew)
- [ ] `pg_dump` nightly → off-VPS storage
- [ ] Sentry — `sentry-sdk[fastapi]`
- [ ] Structured logging — `structlog` JSON
- [ ] `/healthz` endpoint
- [ ] Rate limiting on `/auth/otp/*` — `slowapi`
- [ ] CSRF protection on form routes — `fastapi-csrf-protect`
- [ ] CI workflow (import-check + ruff + tests)
- [ ] Populate GitHub deploy secrets

### R10 — PDPA & customer ops — partial ✅✅⬜

- [x] `POST /card/account/delete` scrubs phone / line_id / display_name; sets `is_anonymous=True`; clears the customer cookie. Stamps remain (counts preserved per PRD §12)
- [x] `find_inactive_anonymous_customers` query (input for the eventual purge job)
- [ ] Actual purge — deferred until policy decision (cascade-delete stamps vs nullable FK vs reassign to placeholder)
- [ ] Audit log for sensitive actions (R10.5)

---

## Backlog (post-v1, deferred per PRD §15)

- DeePass — cross-shop customer wallet (v3)
- DeeWelcome — System → Customer offers (v2)
- DeeMap — customer-facing shop directory (v2)
- Analytics dashboards beyond the headline number (v2)
- Offer kinds: `stamp_multiplier`, `free_reward`, `free_gift`, `discount` (v2)
- Shop → Customer + Customer → Customer referrals (v2 / v3)
- Audit log UI for shop owners

---

## Open questions / waiting on decision

- [ ] THB-to-Credit ratio (PRD §11)
- [ ] Low-credit alert threshold (PRD §6.G)
- [ ] Free theme names beyond placeholder list (PRD §9)
- [ ] Reward mode default (currently Shared)
- [ ] Referral rewards (R8 — TBDs above)
- [ ] Pick SMS provider · LINE Messaging plan · object-storage backend
