#!/usr/bin/env sh
# Mac/Linux launcher: first-run setup, then opens Sticker Studio. Stops with a message on any error.
cd "$(dirname "$0")" || exit 1
PY=$(command -v python3 || command -v python) || { echo "Python 3.11+ not found. Install it from python.org."; exit 1; }
if [ ! -f .venv/setup_done.txt ]; then
  echo "Setting up for the first time (a few minutes)..."
  rm -rf .venv
  "$PY" -m venv .venv && .venv/bin/python -m pip install --upgrade pip && .venv/bin/python -m pip install -r requirements-local.txt \
    || { echo "Setup failed. Copy the text above and send it to Claude."; exit 1; }
  echo done > .venv/setup_done.txt
fi
[ -f .env ] || cp .env.example .env
echo "Starting Sticker Studio at http://127.0.0.1:8081 (Ctrl+C to stop)"
.venv/bin/python -m src.ui.app || { echo "Something went wrong. Copy the text above and send it to Claude."; exit 1; }
