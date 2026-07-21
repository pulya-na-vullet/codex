# IT Workshop Order Manager (Django)

Система учёта заказ-нарядов ИТ-мастерской.

## Stack

- Python 3 + Django 5/6
- SQLite (`db.sqlite3`) — нормализованные модели
- Bootstrap 5, reportlab, openpyxl

## Запуск

```bash
python -m pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py seed_catalog
# если есть старая Flask-база:
python manage.py import_legacy_db orders.db
python app.py
```

`python app.py` **сам** выполняет `migrate --noinput` при каждом старте.

Или Windows: `start_lan_server.bat`

### Миграции вручную

```bash
python manage.py migrate --noinput
```

Сервер: `0.0.0.0:8000`. Вход: `ITM` / `pass` (выход через 6 ч бездействия).

## Обновление версии без потери данных

1. Остановить сервер.
2. Скопировать **`db.sqlite3`** (это ваши данные) в новую папку программы.
3. При наличии старого `orders.db` можно снова импортировать: `python manage.py import_legacy_db orders.db`.
4. Запустить `python app.py` (миграции применятся автоматически) или вручную:
   `python manage.py migrate --noinput` затем `python app.py`.

Либо задайте постоянный путь:

```bat
set IT_MASTER_DB_PATH=C:\IT-Master\data\db.sqlite3
python app.py
```

## Функции

- Клиенты (история, Excel, удаление, постоянный клиент и скидки 5/7/10%)
- Услуги с деревом категорий + удаление
- Заказ-наряды, печать/PDF на принтер техзоны
- **Акт приёма-передачи техники** (создание, печать, PDF)
- Статистика по месяцам и топ клиентов
- LAN-доступ для менеджеров

## Нормализованная схема

`Client` · `ServiceCategory` (parent) · `Service` · `Order` · `OrderLine` · `AcceptanceAct`
