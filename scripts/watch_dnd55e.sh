#!/bin/bash
# Daily dnd55e breaking-change watch (see bg3data/watch.py). Scheduled by Windows Task Scheduler task "bg3-dnd55e-watch".
export UV_PROJECT_ENVIRONMENT="$HOME/.cache/bg3-data-mcp/venv"
cd "$(dirname "$0")/.." && exec "$HOME/.local/bin/uv" run bg3-data watch-dnd55e
