# ИТ-мастерская — API / HTTP Reference

Базовый URL (LAN): `http://<host>:8000`

Сервис — Django web-приложение с session-авторизацией. Большинство методов возвращают HTML или redirect; «настоящий» JSON API — webhook Max.

---

## 1. Авторизация

| | |
|---|---|
| **Схема** | Cookie session (`workshop_authenticated`) |
| **Логин по умолчанию** | `ITM` / `pass` (env: `IT_MASTER_USER`, `IT_MASTER_PASSWORD`) |
| **Idle timeout** | 6 часов (`IT_MASTER_IDLE_SECONDS`) |
| **CSRF** | Обязателен для всех POST, кроме `/max/webhook` и `/hooks/hub/briefs` |
| **Исключения middleware** | `/login`, `/logout`, `/static/`, `/admin/`, `/max/` |

### `POST /login`

Создаёт сессию.

| Поле | Тип | Описание |
|------|-----|----------|
| `username` | string | Логин |
| `password` | string | Пароль |
| `next` | string | URL редиректа после входа |

**Ответ:** `302` → `next` или `/`

### `POST /logout`

Сбрасывает сессию. **Ответ:** `302` → `/login`

---

## 2. Auth / Session summary

Все методы ниже (кроме помеченных Public) требуют активной workshop-сессии.

Формат таблицы:

`METHOD path` — назначение — тело/query — ответ

---

## 3. Dashboard & Statistics

| Method | Path | Params | Response |
|--------|------|--------|----------|
| `GET` | `/` | — | HTML дашборд (счётчики очереди) |
| `GET` | `/statistics` | `period=week\|month\|year` | HTML статистика |

---

## 4. Clients

| Method | Path | Params | Response |
|--------|------|--------|----------|
| `GET` | `/clients` | `q` — поиск | HTML список |
| `POST` | `/clients` | `name`, `phone`, `comment` | `302` |
| `GET` | `/clients/{id}` | — | HTML карточка |
| `POST` | `/clients/{id}` | `comment`, `max_user_id`, `allow_marketing_sms=1`, `discount_percent` (0–15) | `302` |
| `POST` | `/clients/{id}/delete` | — | `302` (ошибка, если есть заказы/акты) |
| `GET` | `/clients/export.xlsx` | — | Excel |
| `POST` | `/clients/import` | `excel_file` (multipart) | `302` |

Импорт Excel: колонки телефон, имя, комментарий.

---

## 5. Services

| Method | Path | Params | Response |
|--------|------|--------|----------|
| `GET` | `/services` | `status=active\|inactive\|all` (default `active`) | HTML |
| `POST` | `/services` | `name`, `price`, `category` | `302` |
| `GET` | `/services/print` | — | HTML прайс (2 колонки, A4) |
| `POST` | `/services/{id}/toggle-active` | `status` (для редиректа) | `302` |
| `POST` | `/services/{id}/delete` | `status` | `302` |

---

## 6. Orders

| Method | Path | Params | Response |
|--------|------|--------|----------|
| `GET` | `/orders` | — | HTML список |
| `GET` | `/orders/export.xlsx` | — | Excel |
| `GET` | `/orders/new` | `client_id?` | HTML форма |
| `POST` | `/orders/new` | `client_id?` | `302` → деталь |
| `GET` | `/orders/{id}` | — | HTML деталь |
| `POST` | `/orders/{id}/meta` | `device_type`, `extra_periphery`, `technical_notes` | `302` |
| `POST` | `/orders/{id}/add-service` | `service_name`, `quantity` | `302` |
| `POST` | `/orders/{id}/line/{line_id}/delete` | — | `302` |
| `POST` | `/orders/{id}/delete` | — | `302` |
| `GET` | `/orders/{id}/print` | — | HTML печать |
| `GET` | `/orders/{id}/pdf` | — | PDF |
| `POST` | `/orders/{id}/print-direct` | — | `302` (очередь печати) |
| `POST` | `/orders/{id}/payment` | `payment_method`, `payment_note`, `payment_receipt?`, `clear_receipt?` | `302` |
| `POST` | `/orders/{id}/mytax` | `mytax_issued=1?`, `mytax_receipt?`, `clear_mytax_receipt?` | `302` |
| `POST` | `/orders/{id}/status` | `status`, `next?` | `302` |
| `POST` | `/orders/{id}/mark-called` | `next?` | `302` |

### Статусы заказа

| Value | Значение |
|-------|----------|
| `active` | В работе |
| `ready_call` | Готово, позвонить клиенту |
| `done` | Выполнена |
| `cancelled` | Отменена |

### Оплата

| Value | Значение |
|-------|----------|
| `unpaid` | Не оплачен |
| `cash` | Наличные |
| `transfer` | Перевод |

`device_type`: `ПК` | `Ноутбук` | `Телефон` | `Телевизор`

---

## 7. Work queue

| Method | Path | Params | Response |
|--------|------|--------|----------|
| `GET` | `/work-queue` | — | HTML очередь заказов + актов |

Статусы/звонки — через endpoints заказов и актов выше.

---

## 8. Debtors

| Method | Path | Params | Response |
|--------|------|--------|----------|
| `GET` | `/debtors` | — | HTML должники |
| `POST` | `/debtors/sms-all` | — | `302` (Max-рассылка всем) |
| `POST` | `/debtors/{order_id}/sms` | — | `302` |

Критерий долга: статус `done`, оплата `unpaid`, сумма > 0, закрыт ≥ `DEBT_GRACE_DAYS`, дата создания ≥ `DEBT_TRACKING_START_DATE`.

---

## 9. Marketing / Max

| Method | Path | Params | Response |
|--------|------|--------|----------|
| `GET` | `/marketing` | `q`, `sort=name\|-name\|date\|-date\|max\|-max\|regular\|-regular` | HTML |
| `POST` | `/marketing` | `text`, `client_ids` (multi) | `302` |
| `GET` | `/marketing/poster` | — | HTML плакат A4 |
| `POST` | `/marketing/blasts/{id}/delete` | — | `302` |
| `POST` | `/marketing/messages/{id}/delete` | — | `302` |
| `GET` | `/marketing/bot-qr.png` | `size` (4–16, default 8) | `image/png` |

### Плейсхолдеры маркетинга

`{name}` `{phone}` `{company}` `{company_phone}`

Масс-рассылка (2+ клиентов) создаёт `MarketingBlast` + строки `SmsLog`.

---

## 10. Acceptance acts

| Method | Path | Params | Response |
|--------|------|--------|----------|
| `GET` | `/acceptance` | `client_id?`, `order_id?` | HTML |
| `POST` | `/acceptance` | `client_id`, `order_id?`, `device_type`, `brand_model`, `serial_number`, `accessories`, `appearance`, `declared_defect`*, `password_info`, `notes` | `302` |
| `GET` | `/acceptance/{id}` | — | HTML |
| `POST` | `/acceptance/{id}/status` | `status`, `next?` | `302` |
| `POST` | `/acceptance/{id}/mark-called` | `next?` | `302` |
| `GET` | `/acceptance/{id}/print` | — | HTML |
| `GET` | `/acceptance/{id}/pdf` | — | PDF |
| `POST` | `/acceptance/{id}/print-direct` | — | `302` |
| `POST` | `/acceptance/{id}/delete` | — | `302` |

### Статусы акта

| Value | Значение |
|-------|----------|
| `diagnostics` | Диагностика идёт |
| `diagnostics_done` | Диагностика выполнена |
| `done` | Выполнен |

---

## 11. Admin panel / AI

| Method | Path | Params | Response |
|--------|------|--------|----------|
| `GET` | `/admin-panel` | — | HTML (только admin) |
| `POST` | `/admin-panel` | `section=max\|ai\|ai_report_now\|ai_report_reset_today\|hub\|staff_create\|staff_update` + поля секции | `302` |
| `GET` | `/modeling` | `status` | HTML |
| `GET/POST` | `/modeling/new` | клиент, URL, STL, скрины, сумма | HTML/`302` |
| `GET/POST` | `/modeling/{id}` | save / push / resubmit / ack_alert | HTML/`302` |
| `POST` | `/modeling/{id}/delete` | — | `302` (только admin) |
| `POST` | `/hooks/hub/briefs` | HMAC + JSON event | `200` |

### `section=max`

| Поле | Описание |
|------|----------|
| `enabled` | Вкл. рассылки |
| `marketing_enabled` | Вкл. маркетинг |
| `long_poll_enabled` | Long poll бота |
| `provider` | `log` \| `max` |
| `bot_token`, `bot_username`, `bot_link` | Max |
| `welcome_text`, `debt_template`, `order_done_template`, `diagnostics_done_template`, `marketing_default_text` | Шаблоны |

### `section=ai`

| Поле | Описание |
|------|----------|
| `ai_enabled` | Ежедневный отчёт |
| `api_key`, `folder_id`, `model_name` | YandexGPT |
| `admin_phone`, `admin_max_user_id` | Получатель в Max |
| `report_time_msk` | `HH:MM` (МСК) |

### `section=ai_report_now`

Тестовая отправка отчёта (`force=True`) — **не** блокирует автозапуск на сегодня.

### `section=ai_report_reset_today`

Снимает отметку «автоотчёт за сегодня уже ушёл».

---

## 12. Audit

| Method | Path | Params | Response |
|--------|------|--------|----------|
| `GET` | `/audit-log` | `q` | HTML (до 300) |
| `GET` | `/audit-log/export.log` | `q` | TSV `.log` (до 20k) |

---

## 13. Docs (эта документация)

| Method | Path | Response |
|--------|------|----------|
| `GET` | `/docs` | HTML API reference |

---

## 14. Public API — Max Webhook

### `GET /max/webhook` — Public, no CSRF

```
200 text/plain
max webhook ok
```

### `POST /max/webhook` — Public, CSRF exempt

Принимает updates от Max (или совместимый JSON). Связывает `Client.max_user_id` по телефону из сообщения.

#### Request examples

```json
{
  "updates": [
    {
      "update_type": "message_created",
      "message": {
        "sender": { "user_id": 7344745 },
        "body": { "text": "+79991234567" }
      }
    }
  ]
}
```

```json
{
  "update_type": "bot_started",
  "message": {
    "sender": { "user_id": 7344745 },
    "body": { "text": "/start" }
  }
}
```

Телефон также ищется в `attachments` / `payload.max_info.phone` / `vcf_info`.

#### Responses

| Status | Body |
|--------|------|
| `200` | `ok` |
| `400` | `bad json` |

---

## 15. Background workers (не HTTP)

| Worker | Условие | Назначение |
|--------|---------|------------|
| Print queue | `IT_MASTER_PRINT_WORKER=1` | Печать PDF через `lp` / Windows |
| Max long-poll | `provider=max`, token, `long_poll_enabled` | `GET {MAX_API_BASE}/updates` |
| AI daily report | `ai_enabled`, время МСК | Отчёт админу в Max |

Env:

- `IT_MASTER_MAX_API` — default `https://platform-api2.max.ru`
- `IT_MASTER_YANDEX_AI_SCHEDULER` — `1`/`0`
- `IT_MASTER_MAX_LONG_POLL` — `1`/`0`

---

## 16. Management commands

```bash
python manage.py send_ai_daily_report [--force]
python manage.py seed_catalog
python manage.py import_legacy_db [source.db] [--clear] [--only-if-empty]
```

---

## 17. Типовые коды ответов

| Code | Когда |
|------|-------|
| `200` | HTML / файл / webhook ok |
| `302` | Успешный POST → redirect |
| `400` | Плохой JSON на webhook |
| `404` | Объект не найден / нет ссылки бота для QR |
| `500` | Нет зависимости (например `qrcode`, `openpyxl`) |

---

## 18. Внешние вызовы (исходящие)

### Max API

- `POST .../messages` — отправка текста клиенту/админу
- `GET .../updates` — long poll

### YandexGPT

- `POST https://llm.api.cloud.yandex.net/foundationModels/v1/completion`

При ошибке AI используется локальный fallback-отчёт.
