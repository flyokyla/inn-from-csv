@echo off
chcp 65001 >nul 2>&1

:: Detect Python command
set PYTHON_CMD=
python --version >nul 2>&1
if not errorlevel 1 set PYTHON_CMD=python
if not defined PYTHON_CMD (
    py --version >nul 2>&1
    if not errorlevel 1 set PYTHON_CMD=py
)
if not defined PYTHON_CMD goto :no_python

echo Запуск ИНН Парсер...
echo Откройте в браузере: http://localhost:1337
echo Для остановки нажмите Ctrl+C
echo.
%PYTHON_CMD% script\inn_web.py
pause
exit /b 0

:no_python
echo [ОШИБКА] Python не найден! Сначала запустите install.bat
pause
exit /b 1
