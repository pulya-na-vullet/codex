# Franchise contract: SITE ↔ HUB

**Locked decisions (2026-07-20)** — do not change without updating both repos.

## Systems

| System | Role |
|--------|------|
| **SITE** | Workshop LAN app (this repo). One instance per franchise location. |
| **HUB** | Public Django platform for 3D designers + network admin. |

## Max bot (single)

One Max bot for clients and designers.

| Actor | Entry |
|-------|--------|
| Client | Existing flow: send phone → link `Client.max_user_id` |
| Designer | Message exactly: `Регистрация: Дизайнер` → bot asks: FIO → SBP phone → experience → portfolio URL |

Long-poll for the shared bot runs on **HUB** (always-online). HUB routes client updates to the correct SITE via callback when needed.

## Economics

- Manager sets `agreed_price` with the client on SITE.
- **Designer share = 70%** of `agreed_price` (paid via HUB/SBP).
- **Site share = 30%** of `agreed_price` (stays in workshop economics/reports).
- Fields: `agreed_price`, `designer_share_amount`, `site_share_amount` (computed at quote/lock time).

## Brief statuses (API enum)

`draft` → `queued` → `assigned` | `in_progress` → `needs_clarification` ⇄ `clarification_provided` → `done` | `cancelled`

No separate `accepted` — **`done` is terminal success**.

## SITE ModelingBrief (minimum fields)

- `client` (local FK; hub gets only `client_ref` = local id)
- `model_url`, `stl_file`, `screenshots[]`
- `agreed_price`, shares 70/30
- `hub_brief_id`, `status`
- `designer_name`, `designer_id`, `eta` (when taken in work)
- `manager_alert` (clarification / done attention)
- `created_by`, `updated_by` (staff user)

## Hub → Site events

| Event | SITE behavior |
|-------|----------------|
| taken_in_work / assigned / in_progress | Store designer FIO + id + ETA; dashboard “in work” count; **Max notify manager** (not client) |
| needs_clarification | Max → **client**; `manager_alert`; list alert |
| clarification resubmit | Same brief updated → POST update to hub |
| done | Max → **client**; manager alerts on dashboard + list; include in revenue reports |

## SITE roles

| Role | Access |
|------|--------|
| `admin` | Full + admin panel + delete + create staff users |
| `manager` | No admin panel; no delete anywhere; can create orders/acts/3D briefs |

All mutating actions record **which staff user** performed them.

## Auth SITE → HUB

```
Authorization: Bearer <site_token>
X-Site-Id: <site_id>
X-Timestamp: <unix>
X-Signature: hex(hmac_sha256(site_secret, timestamp + "\n" + raw_body))
```

## REST (v1)

- `POST /api/v1/briefs` — create
- `GET /api/v1/briefs/{brief_id}`
- `POST /api/v1/briefs/{brief_id}/messages` — clarification reply / resubmit notes
- `POST /api/v1/briefs/{brief_id}` (PATCH-equivalent) — update files/text after clarification

## Webhook HUB → SITE

`POST /hooks/hub/briefs` — HMAC, idempotent by `event_id`.

Never send client PII to designers.
