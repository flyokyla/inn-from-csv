@echo off
chcp 65001 >nul 2>&1
echo Запуск ИНН Парсер...
echo Откройте в браузере: http://localhost:1337
echo Для остановки нажмите Ctrl+C
echo.
python script\inn_web.py
pause
