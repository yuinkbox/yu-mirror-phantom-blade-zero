"""Summarise PBZSmartCombat telemetry snapshots collected by tools_drive.py."""
from __future__ import annotations

import re
import sys
from pathlib import Path

RUNS = Path(r"C:\dev\pbz-runs")

KEYS = [
    "version", "frames", "frames_late", "frame_kinds", "frame_src_field", "frame_type_field",
    "parry", "dodge_red", "guard", "def_ok", "def_fail", "attack_ok", "attack_no_damage",
    "attack_fail", "combo_chain", "combo_press", "move_times_ms", "enemy_list", "faction_seen",
    "inj_ok", "inj_fail", "heal_ok", "heal_fail",
]


def parse(path: Path) -> dict[str, str]:
    data = {}
    try:
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return data
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    return data


def main() -> int:
    runs = sorted(p for p in RUNS.iterdir() if p.is_dir())
    if not runs:
        print("no runs")
        return 1
    run = runs[-1]
    diags = sorted(run.glob("*-diag.txt"), key=lambda p: int(re.match(r"(\d+)", p.name).group(1)))
    print(f"run={run.name} snapshots={len(diags)}")
    previous = {}
    for path in diags:
        data = parse(path)
        changed = {k: data.get(k, "") for k in KEYS if previous.get(k) != data.get(k)}
        previous = data
        if changed:
            print(path.name.replace("-diag.txt", ""), changed)
    statuses = sorted(run.glob("*-status-a.ini"), key=lambda p: int(re.match(r"(\d+)", p.name).group(1)))
    for path in statuses[-3:]:
        data = parse(path)
        print("STATUS", path.name, {k: data.get(k) for k in ("action", "enemy_count", "target_distance", "attacks", "blocks", "dodges")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
