# Промпт в соседний HUB-тред: включить Max long-poll

Скопируй ниже в тред `3dhub` (ветка `cursor/hub-mvp-portal-c542` или новее).

---

Проблема: FSM регистрации дизайнера есть, но бот в Max молчит, потому что нет live long-poll и ответы не уходят в чат.

Нужно:
1. Добавить модель `MaxBotSettings` (token, long_poll_enabled, updates_marker, welcome_text).
2. Добавить `hub/max_bot.py`: `GET /updates` long-poll, `send_max_message`, разбор нативных Max update, вызов `process_bot_message`, **отправка reply в Max**.
3. Стартовать воркер в `HubConfig.ready()`.
4. В `MaxWebhookView` принимать и `{user_id,text}`, и нативный update; при наличии токена тоже слать ответ в Max.
5. Admin singleton для токена; README: на одном токене не держать long-poll SITE и HUB одновременно.
6. Тесты + миграция.

Готовый патч лежит в SITE-репо:
- https://github.com/pulya-na-vullet/codex/blob/cursor/refactor-tkinter-app-9e27/docs/hub-max-long-poll.patch
- drop-in файлы: `docs/hub_max_long_poll_dropin/`
- ZIP SITE с патчем: https://github.com/pulya-na-vullet/codex/archive/refs/heads/cursor/refactor-tkinter-app-9e27.zip

Либо скачай артефакт соседнего агента / примени `git apply docs/hub-max-long-poll.patch` из содержимого патча.

После деплоя:
1. Выключить long-poll Max в SITE (админ мастерской), если тот же токен.
2. В HUB admin → Настройки Max-бота → вставить токен → Long Poll вкл → Save.
3. `runserver` / сервис HUB должен крутиться постоянно.
4. В Max: `Регистрация: Дизайнер` → FSM → логин/пароль в чат.
