from pathlib import Path

ini = Path(r"Z:\steam\steamapps\common\PBZ\platform\steam\ue4ss\UE4SS-settings.ini")
text = ini.read_text("utf-8-sig")
text = text.replace("EnableHotReloadSystem = 0", "EnableHotReloadSystem = 1")
if "EnableHotReloadSystem = 1" not in text:
    text = text.replace("EnableHotReloadSystem=0", "EnableHotReloadSystem = 1")
ini.write_text(text, "utf-8")
for line in ini.read_text("utf-8").splitlines():
    if "HotReload" in line:
        print(line)
