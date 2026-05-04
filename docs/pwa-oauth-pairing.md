# PWA OAuth Pairing Handoff

LINE / Google / Facebook OAuth from inside a home-screen PWA on iOS doesn't
finish cleanly. The user authenticates in the system browser (could be Safari,
Chrome, Firefox — whichever is set as default), the customer cookie lands in
that browser's cookie store, and the PWA web view never sees it. iOS 16.4+
"first-party cookie sharing" only helps when the default browser is Safari —
which is not always the case here.

This doc describes the pairing-code handoff that makes OAuth work end-to-end
regardless of platform or default browser.

## TL;DR

The PWA generates a pairing code, opens the OAuth flow in the external
browser with `?pair=<code>`, and listens on an SSE channel. After OAuth
completes, the callback updates the Pairing row server-side. The PWA's
SSE wakes up, calls a redeem endpoint, and the response sets the customer
cookie inside the PWA's own cookie store. Reload, logged in.

```
┌────────┐    1. POST /auth/pair/start         ┌──────────┐
│  PWA   ├─────────────────────────────────────▶  Server  │
│        ◀──── code, sets pwa_token cookie ───┤          │
│        │                                     │          │
│        │    2. open https://…/auth/line/customer/start?pair=<code>
│        │       (system browser launches)     │          │
│        │                                     │          │
│        │    3. GET /auth/pair/<code>/events  │          │
│        ├─────────────────────────────────────▶          │
│        │        (SSE — server holds open)    │          │
└────────┘                                     │          │
                                               │          │
┌──────────┐                                   │          │
│ External │   4. user authenticates           │          │
│ Browser  │   5. /auth/line/callback?…&pair=  │          │
│          ├───────────────────────────────────▶          │
│          ◀── shows "เข้าสู่ระบบสำเร็จ" page  │          │
└──────────┘                                   │          │
                                               │          │
                Pairing row.customer_id = X    │          │
                NOTIFY pair_<code>             │          │
                                               │          │
┌────────┐                                     │          │
│  PWA   ◀── 6. SSE event: "claimed" ─────────┤          │
│        │                                     │          │
│        │    7. POST /auth/pair/<code>/redeem │          │
│        ├─────────────────────────────────────▶          │
│        ◀── 200 + Set-Cookie: customer=…  ───┤          │
│        │                                     │          │
│        │    8. window.location.reload()      │          │
│        │       — logged in                   │          │
└────────┘                                     └──────────┘
```

## Why not cookie sharing?

Apple shipped first-party cookie sharing between Safari and Add-to-Home-Screen
PWAs in iOS 16.4. That works when:

- Platform is iOS 16.4+
- User's default browser is Safari (or they explicitly chose Safari for the
  OAuth flow)
- User remembers to close + reopen the PWA after OAuth

Three blocking conditions, two of which we can't influence. In practice this
fails for:

- iOS users with Chrome / Firefox / Edge as default browser (common)
- iOS ≤16.3
- Users who don't realize they need to reopen the app

Pairing is platform-agnostic — works the same on Android Chrome, iOS Safari,
iOS-with-Chrome-default, anything.

## Why not deep linking?

Android can deep-link from a browser back to a PWA via `intent://` URLs or
PWA protocol handlers. iOS can't — PWAs there are home-screen shortcuts, not
real apps. They have no `Universal Links`, no `web+taemdee://` schemes, no
programmatic "open this PWA" call. Pairing sidesteps the whole problem by
not relying on the browser to "send" anything to the PWA — the PWA polls
server state via SSE.

## Data model

### `pairings` table

| column | type | notes |
|---|---|---|
| `id` | uuid pk | primary key |
| `code` | str unique | 32-byte URL-safe random; the only thing in the URL |
| `pwa_token` | str | session secret bound to the PWA's cookie store; verified at /redeem |
| `customer_id` | uuid nullable | filled by OAuth callback when auth succeeds |
| `provider` | str nullable | `line` / `google` / `facebook` — for audit |
| `created_at` | datetime | |
| `expires_at` | datetime | created_at + 10 minutes |
| `redeemed_at` | datetime nullable | flipped to now() on /redeem; second redeem fails |

### Why `pwa_token`?

A leaked `code` (e.g. an attacker captures a screenshot of the OAuth URL on
the user's screen) shouldn't let the attacker sign in as the user. Binding
the redeem step to a `pwa_token` cookie set on the PWA means only the PWA
that started the pairing can finish it.

The `pwa_token` is set as an `httpOnly`, `Secure`, `SameSite=Lax` cookie on
the PWA's response when /pair/start is called. /redeem requires both the
URL `code` and the `pwa_token` cookie to match the row.

## Endpoints

### `POST /auth/pair/start`

Creates a Pairing row, returns the code, sets `pwa_token` cookie.

**Response**

```json
{ "code": "abc123…32 chars", "expires_at": "2026-05-04T10:30:00Z" }
```

**Sets cookie** `pwa_token` (httpOnly, Secure, SameSite=Lax, 10min).

### `GET /auth/pair/<code>/events`

SSE stream. Emits one event when the pairing is claimed, then closes.

**Events**

```
event: claimed
data: {"provider":"line"}
```

If the code is invalid or expired, returns 404. Otherwise streams keepalive
comments every 15s so proxies don't kill the connection, and closes after
the `claimed` event or at `expires_at`.

### `POST /auth/pair/<code>/redeem`

Verifies the `pwa_token` cookie matches the row, the row is `customer_id != null`,
and `redeemed_at is null`. Sets the customer cookie on the PWA's response.

**Response**: 200 with `Set-Cookie: customer=…` on success, 404 / 410 on
unknown / expired / wrong-pwa code.

### LINE / Google / Facebook callback (modified)

The existing OAuth callbacks gain a tiny branch: if `state` carries a
`pair` field (from the JWT we already added), look up the Pairing row, fill
its `customer_id` and `provider`, call `pg_notify('pair_<code>', '…')`,
and render a friendly "completion" page instead of the redirect — telling
the user "เข้าสู่ระบบสำเร็จ · กลับไปที่แอปแต้มดี".

## Client flow (PWA, Alpine)

```js
// On click of "ผูกบัญชี LINE" inside the PWA:
const r = await fetch('/auth/pair/start', { method: 'POST' });
const { code } = await r.json();

// Listen for the claim
const es = new EventSource(`/auth/pair/${code}/events`);
es.addEventListener('claimed', async () => {
  es.close();
  const redeem = await fetch(`/auth/pair/${code}/redeem`, { method: 'POST' });
  if (redeem.ok) window.location.reload();
});

// Send the user out — provider start URL with ?pair= so the callback
// knows which Pairing to mark when auth succeeds.
window.location.href = `/auth/line/customer/start?pair=${code}`;
```

In normal-browser mode (`html:not(.pwa-standalone)`), the existing `<a href>`
flow still runs — no pair, no SSE, just a regular OAuth round-trip.

## Edge cases

| case | behavior |
|---|---|
| User never finishes OAuth | Pairing row expires after 10 min, SSE closes, code becomes invalid. PWA shows "ลองอีกครั้ง". |
| User closes PWA mid-flow | SSE is gone; when PWA reopens, the localStorage'd code is still valid until expiry. PWA can resubscribe and pick up. |
| Two PWA tabs with same code | Both subscribe; first to redeem wins (`redeemed_at` is single-write). |
| Code leaked via screenshot | `pwa_token` cookie binding refuses redeem from any other browser context. |
| Replay of /redeem | `redeemed_at` is set on first call; subsequent calls return 410. |
| OAuth callback fired twice | `Pairing.customer_id` write is idempotent (same user); second call no-ops. Different user → reject. |

## Scope

Initial implementation: **LINE only** (the channel the operator said is
essential). Google / Facebook can opt in later by adding the same
`?pair=` branch to their callbacks — model and routes are already
provider-agnostic.

## Non-goals

- This isn't an Android-specific path (it works there too, just unnecessary).
- This doesn't replace the existing `is_pwa_standalone` cookie-sharing path
  — it's a fallback for when sharing fails.
- Token refresh / long-running session resync isn't here. After redeem, the
  PWA holds a normal customer cookie and behaves like any other client.

## Migration

```python
op.create_table(
    "pairings",
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("code", sa.String(64), nullable=False, unique=True, index=True),
    sa.Column("pwa_token", sa.String(64), nullable=False),
    sa.Column("customer_id", sa.UUID, sa.ForeignKey("customers.id"), nullable=True),
    sa.Column("provider", sa.String(32), nullable=True),
    sa.Column("created_at", sa.DateTime, nullable=False),
    sa.Column("expires_at", sa.DateTime, nullable=False),
    sa.Column("redeemed_at", sa.DateTime, nullable=True),
)
```
