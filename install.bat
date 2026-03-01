@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   ИНН Парсер — Установка зависимостей
echo ========================================
echo.

:: Check Python — try "python" first, then "py"
set PYTHON_CMD=
python --version >nul 2>&1
if not errorlevel 1 set PYTHON_CMD=python
if not defined PYTHON_CMD (
    py --version >nul 2>&1
    if not errorlevel 1 set PYTHON_CMD=py
)
if not defined PYTHON_CMD goto :no_python

echo Найден Python: %PYTHON_CMD%
%PYTHON_CMD% --version

echo.
echo [1/3] Устанавливаю Python-зависимости...
%PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 goto :pip_fail

echo.
echo [2/3] Устанавливаю браузер Chromium для Playwright...
%PYTHON_CMD% -m playwright install chromium
if errorlevel 1 goto :chromium_fail

echo.
echo [3/3] Готово!
echo ========================================
echo   Для запуска используйте start.bat
echo   или: %PYTHON_CMD% script\inn_web.py
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
