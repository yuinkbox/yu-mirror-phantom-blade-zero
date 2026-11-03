from pathlib import Path

MODS = Path(r"Z:\steam\steamapps\common\PBZ\platform\steam\ue4ss\Mods")
WANT = ["PBZSmartCombat", "PBZProbe"]

txt = MODS / "mods.txt"
text = txt.read_text("utf-8-sig") if txt.is_file() else ""
lines = [line.rstrip() for line in text.splitlines()]
lines = [line for line in lines if line.strip()]

for name in WANT:
    if not any(line.strip().lower().startswith(name.lower() + " ") for line in lines):
        idx = next((i for i, line in enumerate(lines) if line.strip().startswith("; Built-in")), len(lines))
        lines.insert(idx, f"{name} : 1")

txt.write_text("\n".join(lines) + "\n", "utf-8")
print(txt.read_text("utf-8"))
