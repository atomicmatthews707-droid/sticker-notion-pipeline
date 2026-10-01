#!/usr/bin/env sh
# Mac/Linux launcher: creates a private Python environment on first run, then opens Sticker Studio.
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements-local.txt; }
[ -f .env ] || cp .env.example .env
.venv/bin/python -m src.ui.app
