@echo off
rem intelscraperstudio launcher for Windows. Double-click to start.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer is required.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo FFmpeg is required. Install it by running this in a terminal:
  echo     winget install Gyan.FFmpeg
  echo Then close and reopen this window.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: setting things up. This takes a few minutes...
  python -m venv .venv
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto fail

set "PYTHONPATH=%~dp0engine"
".venv\Scripts\python.exe" -m iss serve %*
exit /b 0

:fail
echo Setup failed. See the messages above.
pause
exit /b 1
