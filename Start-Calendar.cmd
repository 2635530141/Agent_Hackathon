@echo off
setlocal
set "CALENDAR_EXIT=0"
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "CALENDAR_PY=%~dp0.venv\Scripts\python.exe"
if exist "%CALENDAR_PY%" goto check_runtime
py -3 -c "import sys; assert sys.version_info >= (3, 12)" >nul 2>&1
if errorlevel 1 goto python_required
py -3 -m venv .venv
if errorlevel 1 goto failed
:check_runtime
"%CALENDAR_PY%" -c "import sys; assert sys.version_info >= (3, 12)" >nul 2>&1
if errorlevel 1 goto python_required
"%CALENDAR_PY%" -c "import fastapi, uvicorn, sqlalchemy, authlib, cryptography, httpx, jinja2, multipart, itsdangerous" >nul 2>&1
if not errorlevel 1 goto launch
"%CALENDAR_PY%" -m pip install -r requirements.txt -c requirements-calendar-lock.txt
if errorlevel 1 goto failed
:launch
if "%~1"=="--check" goto diagnostics
"%CALENDAR_PY%" -m app.calendar_start --choose-client --open-browser
if errorlevel 1 goto failed
goto done
:diagnostics
"%CALENDAR_PY%" -m app.calendar_start --check
set "CALENDAR_EXIT=%ERRORLEVEL%"
goto done
:python_required
set "CALENDAR_EXIT=2"
echo Install Python 3.12 or newer with the Windows Python launcher, then reopen this file.
goto done
:failed
set "CALENDAR_EXIT=2"
echo Calendar did not start. Read the setup message above; keep your existing data and keys.
echo Setup guide: docs\calendar-google-user-connect.md
:done
pause
exit /b %CALENDAR_EXIT%
