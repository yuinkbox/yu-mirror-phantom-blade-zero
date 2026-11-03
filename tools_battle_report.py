"""Turn the telemetry snapshots of one run into a markdown battle report.

Usage:
    python tools_battle_report.py [run-folder]
"""
from __future__ import annotations

from datetime import datetime
import json
import re
import sys
from pathlib import Path

RUNS = Path(r"C:\dev\pbz-runs")
OUT = Path(r"C:\dev\pbz-reports")


def parse(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return data
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    return data


def counter(data: dict[str, str], key: str) -> int:
    try:
        return int(data.get(key, "0") or 0)
    except ValueError:
        return 0


def rate(good: int, bad: int) -> str:
    total = good + bad
    return f'{good * 100 // total}% ({good}/{total})' if total else '—'


def latest_diag(run: Path) -> dict[str, str]:
    files = sorted(run.glob("*-diag.txt"), key=lambda p: int(re.match(r"(\d+)", p.name).group(1)))
    return parse(files[-1]) if files else {}


def latest_status(run: Path) -> dict[str, str]:
    files = sorted(run.glob("*-status-a.ini"), key=lambda p: int(re.match(r"(\d+)", p.name).group(1)))
    return parse(files[-1]) if files else {}


def main() -> int:
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else sorted(p for p in RUNS.iterdir() if p.is_dir())[-1]
    diag = latest_diag(run)
    status = latest_status(run)
    ok, miss = counter(diag, "attack_ok"), counter(diag, "attack_no_damage")
    dok, dfail = counter(diag, "def_ok"), counter(diag, "def_fail")
    lines = [
        f"# 战斗报告 {run.name}",
        "",
        f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}　mod 版本：{diag.get('version', '?')}",
        "",
        "## 关键指标",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 攻击命中 | {rate(ok, miss)} |",
        f"| 防御成功 | {rate(dok, dfail)} |",
        f"| 格挡 / 闪避 | {diag.get('parry', 0)} / {diag.get('dodge_red', 0)} |",
        f"| 来招帧 / 迟到帧 | {diag.get('frames', 0)} / {diag.get('frames_late', 0)} |",
        f"| 连招 | {diag.get('combo_mode', '?')} · 按 {diag.get('combo_press', 0)} 次 · 步长 {diag.get('plan_delays_ms', '')} |",
        f"| 有效攻击距离 | {diag.get('reach_m', '?')} m（命中时 {diag.get('hit_dist_m', '?')} / 空挥时 {diag.get('whiff_dist_m', '?')}） |",
        f"| 喝药 | 成功 {diag.get('heal_ok', 0)} / 失败 {diag.get('heal_fail', 0)} · 最近 {diag.get('heal_last', 'none')} · 优先道具槽 {diag.get('heal_prefer_alt', '?')} |",
        f"| 注入 | ok {diag.get('inj_ok', 0)} / fail {diag.get('inj_fail', 0)} / missing {diag.get('inj_missing', 0)} |",
        f"| 敌人 | {status.get('enemy_count', '?')} 个 · {diag.get('enemy_list', '') or '无'} |",
        "",
        "## 招式来源",
        "",
        f"- 来源字段：`{diag.get('frame_src_field', 'none')}`，类型字段：`{diag.get('frame_type_field', 'none')}`",
        f"- 来源类名：{diag.get('src_classes', '') or '无'}",
        f"- 类型分布：{diag.get('frame_kinds', '') or '无'}",
        "",
        "## 技能链读取",
        "",
        f"- `spec_field`：{diag.get('spec_field', 'none')}",
        f"- `spec_probe`：{diag.get('spec_probe', '?')}",
        "",
        "## 建议",
        "",
    ]
    if ok + miss >= 10 and ok * 100 // (ok + miss) < 60:
        lines.append("- 命中率偏低：检查 `reach_m` 是否被压得太低，或目标在挥刀期间位移。")
    if dfail > 0:
        lines.append(f"- 有 {dfail} 次防御失败：看 `frame_kinds` 是否集中在某一类（多为贴脸帧）。")
    if not diag.get("combo_press"):
        lines.append("- 连招没有按键记录：攻击可能被距离闸门挡住。")
    if diag.get("heal_last") == "heal_alt":
        lines.append("- 喝药走的是道具槽：`IA_YaoHuLu` 未被游戏接受。")
    if len(lines) and lines[-1] == "":
        lines.append("- （无）")
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{run.name}.md"
    target.write_text("\n".join(lines) + "\n", "utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
