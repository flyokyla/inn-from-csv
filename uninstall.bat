@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   Удаление зависимостей ИНН Парсер
echo ========================================
echo.

set PYTHON_CMD=
python --version >nul 2>&1
if not errorlevel 1 set PYTHON_CMD=python
if not defined PYTHON_CMD (
    py --version >nul 2>&1
    if not errorlevel 1 set PYTHON_CMD=py
)
if not defined PYTHON_CMD goto :no_python

echo Найден: %PYTHON_CMD%
echo.
echo Удаляю пакеты...
%PYTHON_CMD% -m pip uninstall -y pandas playwright starlette uvicorn sse-starlette openpyxl python-multipart
echo.
echo Готово! Теперь запустите install.bat для чистой установки.
echo.
pause
exit /b 0

:no_python
echo [ОШИБКА] Python не найден!
pause
exit /b 1
