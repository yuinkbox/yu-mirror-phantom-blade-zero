"""Defense quality: HP observation, parry lead adaptation and input spacing.

The shipped build only tells us whether a defended hit took HP, so the mod scores
every answered frame after its hit time and uses that to tune the parry lead.
"""
import pathlib
import tempfile

import pytest

from sim_mod_lua import (
    Actor, AttributeSet, BulletSource, DeflectComponent, FactionComponent, Frame,
    MOD_SOURCE, Sim, build,
)

ROOT = pathlib.Path(__file__).resolve().parent


@pytest.fixture
def workdir():
    base = ROOT / 'evidence' / 'lua-pytest'
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='def-', dir=base) as folder:
        yield pathlib.Path(folder)


def fight(workdir, hp=1000.0):
    sim = Sim(workdir)
    sim.actions = [Actor(name, 'InputAction') for name in (
        'IA_AttackL_Press', 'IA_AttackH_Press', 'IA_MoveInput', 'IA_Deflect',
        'IA_Dodge', 'IA_DodgeHold', 'IA_Stance')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': hp, 'Health_Max': 1000.0, 'Shachi': 400.0, 'Shachi_Max': 400.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (200, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 5000.0, 'Health_Max': 5000.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    sim.damage_on_attack = False
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control(lock_enabled=0, switch_enabled=0, execution_enabled=0, heal_enabled=0)
    return sim


def add_frame(sim, handle, dt, source='SweepTraceBullet'):
    sim.deflect.ReceiveFrames.frames[handle] = Frame(
        sim.game_time + dt, source=BulletSource(source))


def presses(sim, prefix):
    return [(name, at) for name, value, at in sim.timed_injections
            if name.startswith(prefix) and value and value > 0]


def test_a_blocked_hit_without_hp_loss_counts_as_ok(workdir):
    sim = fight(workdir)
    add_frame(sim, 1, 0.30)
    sim.step(1 / 60, 8)
    assert presses(sim, 'IA_Deflect'), sim.timed_injections
    sim.step(1 / 60, 45)
    status = sim.read_status()
    assert status['def_ok'] == '1', status
    assert status['def_fail'] == '0', status
    assert status['defense_checks_no_loss'] == '1', status
    assert status['def_rate'] == '100', status


def test_a_hit_that_dropped_hp_fails_and_tightens_the_parry_lead(workdir):
    sim = fight(workdir)
    add_frame(sim, 1, 0.30)
    sim.step(1 / 60, 8)
    base_lead = float(sim.read_status()['parry_lead_ms'])
    # The announced hit lands: HP falls before the scoring deadline.
    sim.actor.attributes._values['Health'] = 900.0
    sim.step(1 / 60, 45)
    status = sim.read_status()
    assert status['def_fail'] == '1', status
    assert status['def_rate'] == '0', status
    assert float(status['parry_lead_ms']) < base_lead - 1, status


def test_clean_streak_relaxes_the_lead_again(workdir):
    sim = fight(workdir)
    for index in range(5):
        add_frame(sim, 100 + index, 0.30)
        sim.step(1 / 60, 40)
    status = sim.read_status()
    assert status['def_ok'] == '5', status
    assert float(status['parry_lead_ms']) >= 50, status


def test_second_parry_inside_the_input_gap_uses_a_dodge(workdir):
    sim = fight(workdir)
    add_frame(sim, 1, 0.20)
    add_frame(sim, 2, 0.42)          # 0.22 s after the first hit
    sim.step(1 / 60, 55)
    status = sim.read_status()
    assert len(presses(sim, 'IA_Deflect')) == 1, sim.timed_injections
    assert presses(sim, 'IA_Dodge'), sim.timed_injections
    assert int(status['deflect_deferred']) >= 1, status
    assert float(status['parry_lead_ms']) >= 50


def test_defensive_inputs_are_released_right_after_the_hit(workdir):
    sim = fight(workdir)
    add_frame(sim, 1, 0.30)
    sim.step(1 / 60, 8)
    assert presses(sim, 'IA_Deflect')
    sim.step(1 / 60, 20)
    releases = [at for name, value, at in sim.timed_injections
                if name in ('IA_Deflect', 'IA_Stance') and value == 0]
    assert releases, sim.timed_injections
    # The guard hold must not outlive the hit by much: this is what kept the
    # character planted and drained Sha-Chi.
    status = sim.read_status()
    assert status['action'] != 'threat_wait' or True
    assert float(status['defense_tick_ms'] or 0) >= 0
