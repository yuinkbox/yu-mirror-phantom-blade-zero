import json
from pathlib import Path

MODS = Path(r"Z:\steam\steamapps\common\PBZ\platform\steam\ue4ss\Mods")

path = MODS / "mods.json"
data = json.loads(path.read_text("utf-8-sig"))
for row in data:
    if row.get("mod_name") == "PBZProbe":
        row["mod_enabled"] = False
path.write_text(json.dumps(data, indent=4, ensure_ascii=False) + "\n", "utf-8")

txt = MODS / "mods.txt"
lines = []
for line in txt.read_text("utf-8").splitlines():
    if line.strip().startswith("PBZProbe"):
        line = "PBZProbe : 0"
    lines.append(line)
txt.write_text("\n".join(lines) + "\n", "utf-8")
print("\n".join(lines))
