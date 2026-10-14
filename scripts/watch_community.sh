#!/bin/bash
# Daily community (mod.io comments) watch (see bg3data/community.py). Scheduled by Windows Task Scheduler task "bg3-community-watch".
export UV_PROJECT_ENVIRONMENT="$HOME/.cache/bg3-data-mcp/venv"
cd "$(dirname "$0")/.." && exec "$HOME/.local/bin/uv" run bg3-data watch-community
