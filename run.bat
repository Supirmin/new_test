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

rem Raspoznavanie kartinok stavitsya otdelno i ne obyazatelno:
rem esli ne vstanet, programma vse ravno rabotaet.
if not exist ".ocr-ok" if not exist ".ocr-skip" (
    echo Ustanovka raspoznavaniya kartinok...
    %PY% -m pip install --quiet --disable-pip-version-check -r requirements-ocr.txt >nul 2>nul
    if errorlevel 1 (
        %PY% -m pip install --quiet --disable-pip-version-check --ignore-requires-python -r requirements-ocr.txt >nul 2>nul
    )
    %PY% -c "import rapidocr_onnxruntime" >nul 2>nul
    if errorlevel 1 (
        echo ok> .ocr-skip
        echo.
        echo Raspoznavanie kartinok ne ustanovilos - eto ne oshibka.
        echo Programma budet rabotat, no listy, gde tablitsy vstavleny
        echo kartinkoy, prochitayutsya ne polnostyu.
        echo Chtoby poprobovat snova, udalite fayl .ocr-skip
        echo.
    ) else (
        echo ok> .ocr-ok
    )
)

%PY% -m spooler %*
if errorlevel 1 (
    echo.
    pause
)
exit /b 0

:fail
echo.
echo Ne udalos ustanovit biblioteki.
pause
exit /b 1
