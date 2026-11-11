"""Before/after benchmark of the combo engine against the windowed game model.

Runs the 0.17.14 Lua (kept under evidence/, the fixed-cadence engine) and the
current Sources/main.lua through the same simulator fight.  The fake game only
chains an attack input inside that move's combo_open..combo_close window, so the
numbers show how many of the mod's presses the game can actually use.
"""
import argparse
import re
from pathlib import Path

from sim_mod_lua import (
    Actor, AttributeSet, DeflectComponent, FactionComponent, Frame, Sim, WindowedFight,
    build,
)

ROOT = Path(__file__).resolve().parent
BASELINE_LUA = ROOT / 'evidence' / 'combo-baseline-0.17.14.lua'
BASELINE_17017 = ROOT / 'evidence' / 'combo-baseline-0.17.17.lua'
BASELINE_EXE = ROOT / 'release-0.17.14' / 'PBZ战斗助手-0.17.14.exe'
CURRENT = ROOT / 'mod-dev-v0.10' / 'mod' / 'PBZSmartCombat' / 'Scripts' / 'main.lua'


def baseline_source():
    """The 0.17.14 fixed-cadence Lua, for the before/after numbers."""
    if BASELINE_LUA.is_file():
        return BASELINE_LUA.read_text(encoding='utf-8')
    if not BASELINE_EXE.is_file():
        return None
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        return None
    archive = CArchiveReader(str(BASELINE_EXE))
    data = archive.extract('PBZSmartCombat\\Scripts\\main.lua')
    return data.decode('utf-8') if data else None


def run(source, seconds=30.0, defense_every=0.0, tmp=None, shachi=400.0):
    tmp.mkdir(parents=True, exist_ok=True)
    sim = Sim(tmp)
    sim.actions = [Actor(name, 'InputAction') for name in (
        'IA_AttackL_Press', 'IA_AttackH_Press', 'IA_MoveInput', 'IA_Deflect',
        'IA_Dodge', 'IA_DodgeHold')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 1000.0, 'Health_Max': 1000.0, 'Shachi': shachi, 'Shachi_Max': 400.0,
        'SpiritShadow': 0.0, 'UltimateChargeCount': 0.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (200, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 9000.0, 'Health_Max': 9000.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    sim.damage_on_attack = False
    sim.combo_game = WindowedFight(sim)
    build(sim, source)
    sim.write_control(lock_enabled=0, switch_enabled=0, execution_enabled=0)
    frames = int(seconds * 60)
    next_defense = defense_every
    game = sim.combo_game
    for index in range(frames):
        if defense_every and sim.clock_value - 100.0 >= next_defense:
            next_defense += defense_every
            sim.deflect.ReceiveFrames.frames[99] = Frame(sim.game_time + 0.02, is_dodge=True)
            sim.step(1 / 60, 8)
            sim.deflect.ReceiveFrames.frames.clear()
            continue
        sim.step(1 / 60, 1)
    result = {
        'presses': len(game.presses),
        'accepted': game.accepted,
        'rejected': game.rejected,
        'chains': game.completed,
        'heavies': sum(1 for key, _, _ in game.presses if key == 'B'),
    }
    result['useful'] = 100.0 * game.accepted / max(1, len(game.presses))
    result['hits_per_s'] = game.accepted / seconds
    result['idle'] = 100.0 * game.idle_seconds / seconds
    result['max_idle'] = game.longest_idle
    status = sim.read_status()
    result['combo_mode'] = status.get('combo_mode', '')
    result['error'] = status.get('error', '')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=30.0)
    parser.add_argument('--defense-every', type=float, default=0.0)
    args = parser.parse_args()

    rows = []
    baseline = baseline_source()
    base_dir = ROOT / 'tmp-benchmark'
    if baseline:
        rows.append(('0.17.14 (fixed cadence)', run(
            baseline, args.seconds, args.defense_every, base_dir / 'before')))
    else:
        print('baseline unavailable: need release-0.17.14 next to the project')
    if BASELINE_17017.is_file():
        rows.append(('0.17.17 (window aligned)', run(
            BASELINE_17017.read_text(encoding='utf-8'), args.seconds, args.defense_every,
            base_dir / 'v17017')))
    current = CURRENT.read_text(encoding='utf-8')
    version = re.search(r"local VERSION = '([^']+)'", current)
    label = f'{"current " + version.group(1) if version else "current"} (window aligned)'
    rows.append((label, run(current, args.seconds, args.defense_every, base_dir / 'after')))
    print(f'{"build":26} {"presses":>7} {"chained":>7} {"dropped":>7} '
          f'{"chains":>6} {"heavy":>5} {"useful%":>7} {"hits/s":>6} '
          f'{"idle%":>6} {"max_idle":>8}  mode')
    for name, row in rows:
        print(f'{name:26} {row["presses"]:7d} {row["accepted"]:7d} {row["rejected"]:7d} '
              f'{row["chains"]:6d} {row["heavies"]:5d} {row["useful"]:7.1f} '
              f'{row["hits_per_s"]:6.2f} {row["idle"]:6.1f} {row["max_idle"]:7.2f}s  '
              f'{row["combo_mode"]}')
        if row['error']:
            print('   error:', row['error'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
