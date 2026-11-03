"""Drive PBZSmartCombat without the desktop GUI: refresh control.ini and snapshot telemetry.

Usage:
    python tools_drive.py --seconds 600 --distance 6 --preset balanced
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import shutil
import sys
import time

from mod_bridge import ModBridge

GAME = r"Z:\steam\steamapps\common\PBZ\platform\steam\PBZ.exe"
OUT = Path(r"C:\dev\pbz-runs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--distance", type=float, default=3.5)
    parser.add_argument("--preset", default="balanced",
                        choices=["balanced", "pressure", "swift", "light"])
    parser.add_argument("--period", type=float, default=0.46)
    parser.add_argument("--threshold", type=float, default=0.4)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--snapshot", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bridge = ModBridge(GAME)
    info = bridge.inspect()
    print("mod:", info.get("status"), info.get("message"), flush=True)
    settings = {
        "attack_enabled": True, "guard_enabled": True, "dodge_enabled": True,
        "heal_enabled": True, "execution_enabled": True, "skills_enabled": True,
        "shadow_enabled": True, "switch_enabled": True, "lock_enabled": True,
        "combo_enabled": True,
        "threshold": args.threshold, "attack_distance": args.distance,
        "attack_period": args.period, "combo_preset": args.preset,
    }
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = OUT / stamp
    folder.mkdir(parents=True, exist_ok=True)
    mod_dir = bridge.mod_dir
    print("snapshots ->", folder, flush=True)

    started = time.time()
    last_pulse = 0.0
    last_snap = 0.0
    snaps = 0
    failures = 0
    while time.time() - started < args.seconds:
        now = time.time()
        if now - last_pulse >= args.interval:
            try:
                bridge.write_control(True, settings)
                failures = 0
                last_pulse = now
            except OSError as exc:
                # the mod reads control.ini every 200 ms; a replace can collide
                failures += 1
                print("control busy, retry:", exc, flush=True)
                if failures > 40:
                    print("giving up after 40 consecutive failures", flush=True)
                    return 2
                time.sleep(0.05)
                continue
            except (RuntimeError, ValueError) as exc:
                print("control write failed:", exc, flush=True)
                return 2
        if now - last_snap >= args.snapshot:
            last_snap = now
            snaps += 1
            for name in ("diag.txt", "status-a.ini", "status-b.ini"):
                source = mod_dir / name
                if source.is_file():
                    shutil.copyfile(source, folder / f"{snaps:04d}-{name}")
            print("snapshot", snaps, flush=True)
        time.sleep(0.2)

    try:
        bridge.write_control(False)
    except (OSError, RuntimeError, ValueError):
        pass
    print("done, snapshots:", snaps, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
