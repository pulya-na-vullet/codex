# IT Workshop Order Manager

Web-based order management system for an IT workshop.

## Stack

- Python 3
- Flask (UI + routing)
- SQLite (`orders.db`)
- Jinja2 templates + Bootstrap 5
- openpyxl (Excel import/export)
- reportlab (PDF)

## Run (на компьютере в технической зоне)

```bash
python -m pip install -r requirements.txt
python app.py
```

Или на Windows двойным кликом:

`start_lan_server.bat`

Сервер слушает `0.0.0.0:8000` — доступен всем устройствам в той же Wi‑Fi/LAN сети.

При запуске в консоли печатаются адреса вида:

`http://192.168.x.x:8000`

Именно этот адрес вводит менеджер в браузере на своём ПК в клиентской зоне.

## Доступ менеджеру в локальной сети

1. Компьютер с сервером и ПК менеджера подключены к **одной** Wi‑Fi сети.
2. На серверном ПК запущен `python app.py` (окно не закрывать).
3. Менеджер открывает в браузере адрес из консоли сервера, например `http://192.168.101.9:8000`.
4. Если страница не открывается на Windows — один раз запустите `start_lan_server.bat` **от имени администратора** (откроет порт 8000 в брандмауэре) или вручную разрешите входящие TCP 8000.

Печать на принтер технической зоны работает с серверного ПК (кнопка «Печать» отправляет PDF на принтер этого компьютера).

## Main sections

- Dashboard (быстрые кнопки клиента/услуги + ссылка на статистику)
- Orders / New Order / Order detail (дерево категорий услуг + поиск)
- Clients (Excel import/export, RF phone validation)
- Services
- Statistics (месячная разбивка, топ-10 клиентов)

## Notes

- Existing legacy database migrations and merge import are handled in `database.py`.
- The web interface is implemented in `webapp/`.
- Host/port can be overridden: `IT_MASTER_HOST`, `IT_MASTER_PORT`.
