"""Ad-hoc timeline dump for one simulator fight (development aid)."""
from pathlib import Path
import shutil
import sys

from sim_mod_lua import (
    Actor, AbilitySystemStub, AttributeSet, DeflectComponent, FactionComponent,
    MOD_SOURCE, Sim, build,
)

ROOT = Path(__file__).resolve().parent
BASE = ROOT / 'tmp-debug'


def main():
    names = ('IA_AttackL_Press', 'IA_AttackH_Press', 'IA_Stance', 'IA_Deflect', 'IA_Dodge',
             'IA_DodgeHold', 'IA_MoveInput')
    mod_dir = BASE / 'combo'
    if mod_dir.exists():
        shutil.rmtree(mod_dir)
    mod_dir.mkdir(parents=True)
    sim = Sim(mod_dir)
    sim.actions = [Actor(name, 'InputAction') for name in names]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    shachi = float(sys.argv[2]) if len(sys.argv) > 2 else 400.0
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 1000.0, 'Health_Max': 1000.0, 'Shachi': shachi, 'Shachi_Max': 400.0,
        'SpiritShadow': 0.0, 'UltimateChargeCount': 0.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (200, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 5000.0, 'Health_Max': 5000.0, 'Shield': 0.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    if len(sys.argv) < 4 or sys.argv[3] != 'nocombo':
        sim.install_combo(sim.actor)
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control()
    sim.step(1 / 60, 420)
    print('status:', {k: sim.read_status()[k] for k in (
        'action', 'attacks', 'combo_state', 'combo_step', 'combo_mode', 'combo_confirm',
        'combo_reject', 'combo_signal', 'heavy_ok')})
    print('diag:', {k: sim.read_diag().get(k) for k in (
        'combo_path', 'combo_mode', 'combo_confirm', 'combo_reject', 'combo_resync',
        'combo_resets', 'combo_last_reset', 'combo_tempo_pct', 'combo_wait_ms',
        'chain_tag', 'phase_tag', 'plan_delays_ms')})
    presses = [(name.replace('_Press', '')[3:], round(at - 100.0, 3))
               for name, value, at in sim.timed_injections
               if name.startswith('IA_Attack') and value and value > 0]
    print('presses:', presses)
    print('gaps:', [round(b[1] - a[1], 3) for a, b in zip(presses, presses[1:])])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
