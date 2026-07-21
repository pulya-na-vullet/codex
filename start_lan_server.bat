@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo === ИТ-мастерская (Django): запуск сервера для локальной сети ===
echo.

net session >nul 2>&1
if %errorlevel%==0 (
  netsh advfirewall firewall show rule name="IT Master Workshop 8000" >nul 2>&1
  if errorlevel 1 (
    echo Добавляю правило брандмауэра для порта 8000...
    netsh advfirewall firewall add rule name="IT Master Workshop 8000" dir=in action=allow protocol=TCP localport=8000 >nul
  )
) else (
  echo Подсказка: для авто-открытия порта 8000 запустите от имени администратора один раз.
)

python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Не удалось установить зависимости.
  pause
  exit /b 1
)

python manage.py migrate --noinput
if exist orders.db (
  echo Найден orders.db — импорт только если Django БД ещё пустая...
  python manage.py import_legacy_db orders.db --only-if-empty
)

echo.
echo Сервер стартует. Не закрывайте окно.
echo Данные хранятся в db.sqlite3 — при обновлении копируйте этот файл в новую папку.
echo При каждом запуске копия БД пишется в dumpDB\orders.db
echo.
python app.py
pause
