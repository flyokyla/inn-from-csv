@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   ИНН Парсер — Установка зависимостей
echo ========================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 goto :no_python

echo [1/3] Устанавливаю Python-зависимости...
pip install -r requirements.txt
if errorlevel 1 goto :pip_fail

echo.
echo [2/3] Устанавливаю браузер Chromium для Playwright...
playwright install chromium
if errorlevel 1 goto :chromium_fail

echo.
echo [3/3] Готово!
echo ========================================
echo   Для запуска используйте start.bat
echo   или: python script\inn_web.py
echo ========================================
echo.
pause
exit /b 0

:no_python
echo [ОШИБКА] Python не найден!
echo Скачайте Python 3.10+ с https://www.python.org/downloads/
echo При установке обязательно поставьте галочку "Add Python to PATH"
echo.
pause
exit /b 1

:pip_fail
echo [ОШИБКА] Не удалось установить зависимости
pause
exit /b 1

:chromium_fail
echo [ОШИБКА] Не удалось установить Chromium
pause
exit /b 1
