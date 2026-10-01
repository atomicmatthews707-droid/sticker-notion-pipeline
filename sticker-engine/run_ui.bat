@echo off
rem Windows launcher: creates a private Python environment on first run, then opens Sticker Studio.
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements-local.txt
)
if not exist .env copy .env.example .env
.venv\Scripts\python -m src.ui.app
