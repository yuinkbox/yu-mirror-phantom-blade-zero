import json
from pathlib import Path

MODS = Path(r"Z:\steam\steamapps\common\PBZ\platform\steam\ue4ss\Mods")
WANT = ["PBZSmartCombat", "PBZProbe"]

# mods.json
path = MODS / "mods.json"
data = json.loads(path.read_text("utf-8-sig")) if path.is_file() else []
names = {row.get("mod_name") for row in data}
for name in WANT:
    if name not in names:
        data.append({"mod_name": name, "mod_enabled": True})
path.write_text(json.dumps(data, indent=4, ensure_ascii=False) + "\n", "utf-8")
print("mods.json:", [(r["mod_name"], r["mod_enabled"]) for r in data])

# mods.txt
txt = MODS / "mods.txt"
text = txt.read_text("utf-8-sig") if txt.is_file() else ""
lines = text.splitlines()
for name in WANT:
    if not any(line.strip().lower().startswith(name.lower() + " ") for line in lines):
        idx = next((i for i, line in enumerate(lines) if line.strip().startswith("; Built-in")), len(lines))
        lines.insert(idx, f"{name} : 1")
        lines.insert(idx + 1, "")
txt.write_text("\r\n".join(lines) + "\r\n", "utf-8")
print("--- mods.txt ---")
print(txt.read_text("utf-8-sig"))
