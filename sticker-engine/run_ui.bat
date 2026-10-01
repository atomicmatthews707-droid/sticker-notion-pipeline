@echo off
setlocal
cd /d "%~dp0"
title Sticker Studio

rem --- find Python (the "py" launcher first, then "python") ---
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo.
  echo Python was not found on this computer.
  echo Install Python 3.11 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" on the first screen, then run this again.
  goto :end
)
echo Using: & %PY% --version

rem --- first-time setup (re-runs itself if a previous setup did not finish) ---
if not exist ".venv\setup_done.txt" (
  echo Setting up for the first time. This can take a few minutes...
  if exist ".venv" rmdir /s /q ".venv"
  %PY% -m venv .venv || goto :failed
  ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :failed
  ".venv\Scripts\python.exe" -m pip install -r requirements-local.txt || goto :failed
  echo done> ".venv\setup_done.txt"
)

if not exist ".env" copy ".env.example" ".env" >nul

echo.
echo Starting Sticker Studio. Your browser will open at http://127.0.0.1:8081
echo Leave this window open while you use it. Close it to stop.
echo.
".venv\Scripts\python.exe" -m src.ui.app
if errorlevel 1 goto :failed
goto :end

:failed
echo.
echo ===== Something went wrong. Please copy the text above and send it to Claude. =====

:end
echo.
pause
