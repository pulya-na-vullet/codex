# Промпт для нового треда (HUB) — вставь как первое сообщение

Скопируй всё ниже целиком в новый Cursor Cloud / Agent тред на **пустом** репозитории HUB.

---

Ты строишь **HUB** — публичное Django-приложение сети 3D-дизайнеров для франшизы ИТ-мастерских.

SITE (мастерская, LAN) уже готов и ждёт эти контракты. Не меняй wire-format без крайней нужды.

## Источники правды (прочитай / приложи в репо)

1. `docs/HUB_AGENT_BRIEF.md` — полный бриф (если файла ещё нет в HUB-репо — он лежит в SITE: branch `cursor/refactor-tkinter-app-9e27`, путь `docs/HUB_AGENT_BRIEF.md`).
2. `docs/FRANCHISE_CONTRACT.md` — зафиксированные продуктовые решения.
3. SITE код интеграции: `workshop/hub.py` (payload create/update + HMAC + webhook events).

SITE ZIP: https://github.com/pulya-na-vullet/codex/archive/refs/heads/cursor/refactor-tkinter-app-9e27.zip  
SITE PR: https://github.com/pulya-na-vullet/codex/pull/2

## Зафиксировано

- Один Max-бот; регистрация дизайнера точной фразой `Регистрация: Дизайнер` → ФИО → СБП-телефон → опыт → портфолио.
- Long-poll бота на HUB (не гоняй второй long-poll на том же токене на SITE).
- Доля дизайнера **70%**, точки **30%** от `agreed_price` (поля приходят с SITE).
- Уточнение = тот же brief; терминальный успех = `done`.
- Клиентские ПДн дизайнеру не отдаём; `delivery_address` только на SITE — на HUB не принимать/не хранить.

## MVP сделай по порядку

1. Django-проект + модели: `SiteNode`, `Designer`, `HubBrief`, outbound event log, Max settings.
2. HMAC API: `POST/GET /api/v1/briefs`, `POST /api/v1/briefs/{id}` (update/resubmit) — совместимо с SITE `brief_payload`.
3. Ответ create: JSON с `brief_id` (SITE его сохраняет).
4. Webhook на SITE: `POST {callback}/hooks/hub/briefs` с `event_id` + events `taken_in_work|assigned|in_progress|needs_clarification|done|cancelled|queued`.
5. Админка: точки, очередь заявок, назначение дизайнера, done/clarification без бота.
6. Max long-poll: регистрация дизайнера + команды взять / уточнение / готово.
7. Тесты на HMAC, create brief, идемпотентность webhook, FSM регистрации.
8. README: как создать SiteNode и прописать те же `site_id/token/secret/hub_url` в SITE admin → HUB.

Работай в feature-ветке, коммить и пушь итерациями. Стек: Django 5+, простой UI (admin + минимум своих шаблонов ок). Язык интерфейса — русский.

Начни с каркаса проекта и моделей, затем API под SITE, затем webhooks, затем Max.
