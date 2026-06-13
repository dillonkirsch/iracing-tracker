@echo off
rem ============================================================
rem  iRacing Config Tracker - double-click to open the app.
rem  Opens a real app window if pywebview is installed, otherwise
rem  opens in your web browser. Keep this window open while you
rem  use the app; close it (or press Ctrl+C) to quit.
rem ============================================================
title iRacing Config Tracker
cd /d "%~dp0"

set "PY="
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import irtracker" >nul 2>&1 && set "PY=.venv\Scripts\python.exe"
)
if not defined PY (
  python -c "import irtracker" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  py -c "import irtracker" >nul 2>&1 && set "PY=py"
)

if not defined PY (
  echo.
  echo Could not find the iRacing Config Tracker installation.
  echo Open PowerShell in this folder and run:
  echo     python -m venv .venv
  echo     .venv\Scripts\Activate.ps1
  echo     pip install -e .[gui]
  echo.
  pause
  exit /b 1
)

%PY% -m irtracker gui
if errorlevel 1 pause
