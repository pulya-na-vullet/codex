# HUB Portal (Django + Bootstrap)

MVP-портал для сети CRM-точек и дизайнеров 3D-моделирования.

## Что реализовано

- Приём заявок из CRM по API:
  - `POST /api/v1/briefs`
  - `GET /api/v1/briefs/{brief_id}`
  - `POST /api/v1/briefs/{brief_id}`
  - `POST /api/v1/briefs/{brief_id}/messages`
- HMAC-аутентификация входящих запросов от CRM:
  - `Authorization: Bearer <site_token>`
  - `X-Site-Id`
  - `X-Timestamp`
  - `X-Signature = hex(hmac_sha256(site_secret, timestamp + "\n" + raw_body))`
- Модель зарегистрированных через Max дизайнеров.
- **Max long-poll воркер** (основной путь для LAN/без публичного webhook):
  - слушает `GET /updates` у Max API
  - фраза `Регистрация: Дизайнер` → ФИО → телефон СБП → опыт → портфолио
  - после регистрации бот **присылает в Max** web-логин и пароль
- Endpoint `POST /api/v1/max/webhook` (для тестов / публичного webhook Max):
  - упрощённый JSON `{user_id, text}`
  - или нативный update Max (`update_type` / `message`)
- Команды дизайнера в Max:
  - `Очередь`
  - `Беру <brief_id> <срок>`
  - `Уточнение <brief_id> <текст>`
  - `Готово <brief_id>`
- Web API для кабинета дизайнера:
  - `POST /api/v1/designer/auth/login`
  - `GET /api/v1/designer/briefs`
  - `POST /api/v1/designer/briefs/{brief_id}/claim`
- Django admin + веб-кабинет `/designer/login`, `/designer/queue`

## Важно про один бот

На **одном** токене Max может работать только **один** long-poll.

Если HUB слушает бота — **выключите** long-poll в SITE (мастерской), иначе обновления «съест» другой процесс и регистрация дизайнера не стартует.

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Открыть в браузере:

- `http://127.0.0.1:8000/designer/login` — кабинет дизайнера
- `http://127.0.0.1:8000/admin/` — админка

## Как включить регистрацию дизайнера в Max

1. `python manage.py runserver` (воркер long-poll стартует вместе с приложением).
2. Admin → **Настройки Max-бота** → вставить токен бота, включить Long Poll, сохранить.
3. В Max написать боту точно: `Регистрация: Дизайнер`
4. Пройти шаги; бот пришлёт логин/пароль для `/designer/login`.

Переменные:

- `MAX_LONG_POLL_WORKER=0` — полностью выключить фоновый воркер
- `MAX_API_BASE` — по умолчанию `https://platform-api2.max.ru`

## Настройки Django

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG` — `1` или `0`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_TIMEZONE` — по умолчанию `UTC`

## Порядок подключения CRM

1. В админке HUB создать `SiteNode` с `site_id`, `site_token`, `site_secret`, `callback_base_url`.
2. В CRM прописать те же `site_id/token/secret` и адрес HUB.
3. Отправлять заявки в `POST /api/v1/briefs` в формате из `openapi-hub.yaml`.
