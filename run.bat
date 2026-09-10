@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set "PY=py") || (set "PY=python")

%PY% --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo Python ne nayden. Ustanovite ego s https://www.python.org/downloads/
    echo Pri ustanovke otmette galochku "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

if not exist ".deps-ok" (
    echo Ustanovka bibliotek, eto odin raz...
    %PY% -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto :fail
    echo ok> .deps-ok
)

if "%~1"=="" (
    %PY% -m spooler.hint
    pause
    exit /b 0
)

%PY% -m spooler.cli %*
echo.
pause
exit /b 0

:fail
echo.
echo Ne udalos ustanovit biblioteki.
pause
exit /b 1
