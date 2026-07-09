@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo.
echo === ИТ-мастерская: запуск сервера для локальной сети ===
echo.

REM Optional: open Windows Firewall for TCP 8000 (needs admin once)
net session >nul 2>&1
if %errorlevel%==0 (
  netsh advfirewall firewall show rule name="IT Master Workshop 8000" >nul 2>&1
  if errorlevel 1 (
    echo Добавляю правило брандмауэра для порта 8000...
    netsh advfirewall firewall add rule name="IT Master Workshop 8000" dir=in action=allow protocol=TCP localport=8000 >nul
  ) else (
    echo Правило брандмауэра уже есть.
  )
) else (
  echo Подсказка: для авто-открытия порта 8000 запустите этот файл от имени администратора один раз.
)

echo.
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Не удалось установить зависимости. Проверьте Python.
  pause
  exit /b 1
)

echo.
echo Сервер стартует. Не закрывайте это окно.
echo Менеджер в клиентской зоне открывает в браузере адрес, который покажет сервер ниже.
echo.
python app.py

pause
