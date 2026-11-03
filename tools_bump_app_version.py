"""Set the desktop app version (default: read it from the Lua mod)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "mod_app.py"
LUA = ROOT / "mod-dev-v0.10" / "mod" / "PBZSmartCombat" / "Scripts" / "main.lua"

version = sys.argv[1] if len(sys.argv) > 1 else re.search(
    r"local VERSION = '([^']+)'", LUA.read_text("utf-8")).group(1)
text = APP.read_text("utf-8")
updated, count = re.subn(r"^VERSION = '[^']+'", f"VERSION = '{version}'", text, count=1, flags=re.M)
if count != 1:
    raise SystemExit("VERSION line not found")
APP.write_text(updated, "utf-8", newline="\n")
print(f"mod_app.py VERSION = {version}")
