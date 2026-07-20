# HUB — agent brief (start here)

**Purpose:** handoff for a separate Cursor/AI thread that builds the **HUB** Django project from an empty repo.

**Companion (already shipped on SITE):**  
`https://github.com/pulya-na-vullet/codex` branch `cursor/refactor-tkinter-app-9e27`  
Contract: `docs/FRANCHISE_CONTRACT.md`  
SITE integration: `workshop/hub.py`, admin «HUB», `/modeling`, webhook `/hooks/hub/briefs`

Copy **`docs/HUB_AGENT_PROMPT.md`** into the new thread as the first user message. Keep this file + the contract in the HUB repo (or attach both).

---

## 1. What HUB is

Public always-online Django app for the franchise 3D network:

| Actor | Needs |
|-------|--------|
| **Network admin** | Register SITE nodes (`site_id`, token, secret, callback URL), designers, view queue/economics |
| **Designer** | Register via Max (`Регистрация: Дизайнер`), take briefs, ask clarification, mark done, see **70%** payout |
| **SITE workshops** | Push briefs in; receive status webhooks out |

SITE is LAN-only (one instance per workshop). HUB is the public glue.

---

## 2. Locked product rules (do not renegotiate)

1. **One Max bot** for clients and designers. Exact designer trigger: `Регистрация: Дизайнер` → ask FIO → SBP phone → experience → portfolio URL.
2. Long-poll for that bot runs on **HUB** (not SITE).
3. Economics: designer **70%**, site **30%** of `agreed_price` (SITE already computes and sends both share fields).
4. Clarification = **same brief** updated/resubmitted — never a new brief.
5. Terminal success = **`done`** (no separate `accepted`).
6. **Never** send client PII (name/phone/address) to designers. SITE sends only `client_ref` (opaque local id).
7. **`delivery_address` is SITE-only** — must not appear in HUB models/API payloads from SITE (SITE already omits it).

Statuses enum:

`draft` → `queued` → `assigned` | `in_progress` → `needs_clarification` ⇄ `clarification_provided` → `done` | `cancelled`

---

## 3. SITE is already implemented — match these wire formats exactly

### 3.1 Auth SITE → HUB (every API call)

```
Authorization: Bearer <site_token>
X-Site-Id: <site_id>
X-Timestamp: <unix seconds>
X-Signature: hex(hmac_sha256(site_secret, timestamp + "\n" + raw_body))
Content-Type: application/json
```

Reject if skew > 300s or signature mismatch.

SITE settings fields (admin → HUB): `site_id`, `hub_base_url`, `site_token`, `site_secret`, `designer_share_percent` (default 70), `enabled`.

### 3.2 SITE → HUB create brief

`POST {hub_base_url}/api/v1/briefs`

Body (exactly what SITE sends today):

```json
{
  "local_brief_id": 12,
  "brief_number": "3D-000001",
  "client_ref": "5",
  "model_url": "https://...",
  "description": "ТЗ text",
  "agreed_price": "5000.00",
  "designer_share_amount": "3500.00",
  "site_share_amount": "1500.00",
  "has_stl": true,
  "screenshots_count": 2
}
```

**Expected JSON response (SITE reads these keys):**

```json
{ "brief_id": "hub-uuid-or-int", "id": "optional-alias", "status": "queued" }
```

SITE stores `brief_id` or `id` into `ModelingBrief.hub_brief_id` and sets local status `queued`.

### 3.3 SITE → HUB update / clarification resubmit

`POST {hub_base_url}/api/v1/briefs/{hub_brief_id}`

Same body shape as create (updated description/files metadata). After success SITE may set status `clarification_provided` if it was `needs_clarification`.

Also planned (implement):

- `GET /api/v1/briefs/{brief_id}`
- `POST /api/v1/briefs/{brief_id}/messages` — clarification notes

**Files:** SITE currently only sends flags (`has_stl`, `screenshots_count`) + `model_url`. MVP: accept URL + metadata; v1.1: multipart upload endpoints or signed download URLs from SITE. Do not block MVP on file binary transfer if URL is present.

### 3.4 HUB → SITE webhook (SITE already handles)

`POST {site_callback_base}/hooks/hub/briefs`  
CSRF exempt on SITE. Same HMAC scheme using that site’s `site_secret` (and preferably Bearer site_token + X-Site-Id).

Idempotent by `event_id` (SITE stores seen ids).

Payload SITE accepts:

```json
{
  "event_id": "unique-string",
  "event": "taken_in_work",
  "local_brief_id": 12,
  "brief_id": "hub-42",
  "designer_name": "Иван Дизайнер",
  "designer_id": "d1",
  "eta": "2 дня",
  "message": "optional text for client/manager"
}
```

| `event` values SITE understands | Effect on SITE |
|---------------------------------|----------------|
| `taken_in_work`, `assigned` | status `assigned`; Max → **manager only** |
| `in_progress` | status `in_progress`; Max → manager |
| `needs_clarification` / `clarification` | status `needs_clarification`; Max → **client** + manager alert |
| `done` / `completed` | status `done`; Max → client + manager |
| `cancelled` / `canceled` | cancelled; Max → manager |
| `queued` | status `queued` |

Lookup order on SITE: `local_brief_id` then `hub_brief_id`.

SITE callback URL must be reachable from HUB (public tunnel / reverse proxy per workshop, or VPN). Store `callback_base_url` per SiteNode on HUB.

---

## 4. Suggested HUB Django models (MVP)

```
SiteNode
  site_id (unique)
  name
  callback_base_url   # e.g. https://shop1.example.com
  site_token
  site_secret
  is_active
  created_at

Designer
  max_user_id (unique, from Max)
  full_name
  sbp_phone
  experience_text
  portfolio_url
  is_active
  registered_at

HubBrief
  id (public brief_id string or UUID)
  site (FK SiteNode)
  local_brief_id
  brief_number
  client_ref          # opaque — NO name/phone
  model_url
  description
  agreed_price
  designer_share_amount
  site_share_amount
  status
  designer (FK null)
  eta
  last_message
  created_at / updated_at / done_at

HubBriefEvent (outbound log)
  event_id (unique)
  brief
  event
  payload_json
  delivered_ok
  created_at

MaxBotSettings (singleton)
  bot_token
  bot_username
  long_poll_enabled
  # welcome texts, designer registration prompts
```

Admin UI (Django admin or simple staff site): SiteNodes CRUD, designers list, briefs board, payout report (sum of `designer_share_amount` where `done`).

Designer-facing UI (MVP options — pick one and ship):

- **A (faster):** staff/admin board + Max bot commands for take / clarify / done  
- **B:** simple login-by-Max session web UI for designers  

Prefer **A** for first vertical slice if time-boxed; add **B** next.

---

## 5. Max bot on HUB (MVP flows)

### Long-poll is mandatory for live registration

SITE CRM bot long-poll and HUB designer long-poll **cannot share one Max token**.

When HUB is live: enable HUB `MaxBotSettings.long_poll_enabled` + token; **disable** SITE Max long-poll for that bot.

If HUB only exposes `POST /api/v1/max/webhook` without a poller/proxy, writing `Регистрация: Дизайнер` in Max does nothing.

### Designer registration

1. User sends exactly: `Регистрация: Дизайнер`
2. Bot asks sequentially: ФИО → телефон СБП → опыт → ссылка на портфолио
3. Create/update `Designer` linked to `max_user_id`; reply with web login/password in Max

### Designer work (commands or buttons)

Examples (Russian, keep UX simple):

- `Очередь` — list open `queued` briefs (no client PII)
- `Беру <brief_id> <срок>` → assign designer, webhook `taken_in_work`
- `Уточнение <brief_id> <текст>` → webhook `needs_clarification` with `message`
- `Готово <brief_id>` → webhook `done`

### Client side of the shared bot

SITE today still has Max long-poll for client phone linking. Target architecture: HUB owns long-poll; client phone messages are routed to the correct SITE via callback.

**MVP compromise (recommended):**

1. Phase 1 HUB: designer registration + brief API + webhooks; **SITE keeps client Max long-poll** temporarily (two pollers cannot share one token — so for Phase 1 either disable SITE long-poll when HUB is live, or use webhook mode on Max toward HUB only).
2. Phase 2: move **all** Max updates to HUB; implement client phone→SITE routing (`POST {site}/hooks/hub/max-client` or reuse existing SITE link logic via internal API).

Document in README which phase is active. Do not run two long-pollers on the same bot token.

---

## 6. Suggested build order (other thread)

1. Empty Django project + `requirements.txt` + sqlite/postgres + deploy notes  
2. Models + admin for `SiteNode`, `Designer`, `HubBrief`  
3. HMAC auth middleware/decorators for `/api/v1/*`  
4. `POST/GET /api/v1/briefs` (+ update by id) matching SITE payloads  
5. Outbound webhook client → SITE `/hooks/hub/briefs` with retries + `event_id`  
6. Max long-poll worker: designer registration + take/clarify/done  
7. Admin board: queue, assign, mark done (even without Max)  
8. Tests: HMAC, create brief, webhook idempotency, designer registration FSM  
9. Copy `FRANCHISE_CONTRACT.md` into HUB repo; link back to SITE branch  

---

## 7. Security / privacy checklist

- [ ] HMAC on all SITE↔HUB calls  
- [ ] No client name/phone/address in designer views or Max messages  
- [ ] Secrets not in git; env or local settings  
- [ ] Rate-limit public Max webhook if used  
- [ ] SiteNode credentials rotatable  

---

## 8. Acceptance criteria (HUB MVP done)

1. Create `SiteNode` in admin; configure same token/secret/site_id on a SITE instance.  
2. From SITE «Отправить в HUB» on a modeling brief → HubBrief appears as `queued`.  
3. Assign designer (admin or Max) → SITE status becomes assigned; manager Max notified (SITE side).  
4. Clarification event → SITE asks client via Max.  
5. SITE manager edits brief + «Отправить уточнение» → HUB brief updated, status `clarification_provided`.  
6. Done → SITE marks done; economics 70/30 visible on both sides.  
7. `delivery_address` never stored/shown on HUB.  

---

## 9. Reference links

| Item | URL |
|------|-----|
| SITE PR | https://github.com/pulya-na-vullet/codex/pull/2 |
| SITE ZIP (branch) | https://github.com/pulya-na-vullet/codex/archive/refs/heads/cursor/refactor-tkinter-app-9e27.zip |
| Contract in SITE | `docs/FRANCHISE_CONTRACT.md` |
| SITE hub client | `workshop/hub.py` |
| SITE webhook | `POST /hooks/hub/briefs` |

---

## 10. Out of scope for HUB MVP

- Printing / shipping logistics (SITE `delivery_address`)  
- Replacing SITE workshop ERP (orders, debtors, services)  
- Multi-currency / tax  
- Designer payout bank integration beyond storing SBP phone + report of 70% amounts  
