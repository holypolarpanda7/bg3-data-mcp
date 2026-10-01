"""Smoke check for a new install (Windows or WSL): discovery, config, and - if the game is running with
Script Extender - one round trip through the console.  uv run python tests/env_check.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bg3data import deploy, se  # noqa: E402

print(deploy.environment())
print("--- Script Extender")
print(se.status())
try:
    print("eval:", se.eval_lua("return Osi.GetRegion(Osi.GetHostCharacter())", "server", timeout=15))
except (RuntimeError, TimeoutError) as e:
    print("eval skipped:", e)
