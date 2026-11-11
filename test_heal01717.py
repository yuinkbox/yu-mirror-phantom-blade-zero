"""Quick-slot medicine: binding, press length, diagnostics and threat starvation."""
import pathlib
import tempfile

import pytest

from sim_mod_lua import (
    Actor, AttributeSet, BulletSource, DeflectComponent, FactionComponent, Frame,
    MOD_SOURCE, Sim, build,
)

ROOT = pathlib.Path(__file__).resolve().parent
ITEMS = ('IA_UseBottomItem', 'IA_UseTopItem', 'IA_YaoHuLu')


@pytest.fixture
def workdir():
    base = ROOT / 'evidence' / 'lua-pytest'
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='heal-', dir=base) as folder:
        yield pathlib.Path(folder)


def fight(workdir, *, hp=200.0, actions=ITEMS):
    sim = Sim(workdir)
    sim.actions = [Actor(name, 'InputAction') for name in (
        'IA_AttackL_Press', 'IA_AttackH_Press', 'IA_MoveInput', 'IA_Deflect',
        'IA_Dodge', 'IA_DodgeHold') + tuple(actions)]
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
    sim.write_control(lock_enabled=0, switch_enabled=0, execution_enabled=0)
    return sim


def item_presses(sim):
    return [at for name, value, at in sim.timed_injections
            if name in ITEMS and value and value > 0]


def test_quick_slot_is_pressed_for_long_enough_to_be_consumed(workdir):
    sim = fight(workdir)
    sim.step(1 / 60, 90)
    starts = [at for name, value, at in sim.timed_injections
              if name == 'IA_UseBottomItem' and value and value > 0]
    assert starts, sim.timed_injections
    releases = [at for name, value, at in sim.timed_injections
                if name == 'IA_UseBottomItem' and value == 0]
    assert releases and releases[0] - starts[0] >= 0.18, sim.timed_injections


def test_quick_slot_wins_over_the_support_slot_when_both_exist(workdir):
    sim = fight(workdir)
    sim.step(1 / 60, 90)
    status = sim.read_status()
    assert status['heal_action'] == 'IA_UseBottomItem', status
    first = [name for name, value, at in sim.timed_injections if value and value > 0
             and name in ITEMS][:1]
    assert first == ['IA_UseBottomItem'], sim.timed_injections


def test_missing_item_action_is_reported_with_the_tried_keys(workdir):
    sim = fight(workdir, actions=())
    sim.step(1 / 60, 120)
    status = sim.read_status()
    assert 'heal:missing' in status['heal_candidates'], status
    assert 'action_missing' in status['heal_action'], status
    assert status['heal_action'].count('action_missing') == 2, status


def test_item_like_actions_are_listed_for_diagnosis(workdir):
    sim = fight(workdir, actions=('IA_UseMedicine', 'IA_UseTopItem'))
    sim.step(1 / 60, 90)
    status = sim.read_status()
    # The real binding is unknown on a new build: the status names the candidates.
    assert 'IA_UseMedicine' in status['item_actions'], status
    assert 'IA_UseTopItem' in status['item_actions'], status
    assert status['heal_action'] == 'IA_UseMedicine', status
    assert 'heal:IA_UseMedicine' in status['heal_candidates'], status


def test_medicine_is_taken_when_threats_never_stop(workdir):
    sim = fight(workdir)
    # A boss that keeps announcing an attack just outside the parry lead keeps
    # the mod in "wait for a safe window" forever; low HP must win instead.
    for _ in range(420):
        sim.deflect.ReceiveFrames.frames[1] = Frame(
            sim.game_time + 0.30, source=BulletSource('SweepTraceBullet'))
        sim.step()
    status, diag = sim.read_status(), sim.read_diag()
    assert int(status['heal_blocked_ms']) >= 0, status
    assert item_presses(sim), sim.timed_injections
    assert int(diag['heal_brave']) >= 1, diag


def test_medicine_effect_is_confirmed_and_reported(workdir):
    sim = fight(workdir)
    sim.heal_on_inject = {'IA_UseBottomItem': 250.0}
    sim.step(1 / 60, 40)
    status = sim.read_status()
    assert status['heal_ok'] == '1', status
    assert status['heal_last'] == 'heal', status
    assert status['heal_action'] == 'IA_UseBottomItem', status
    # Once HP is above the threshold the state moves on to "healthy".
    sim.step(1 / 60, 80)
    assert sim.read_status()['heal_state'] in ('hp_increased', 'healthy'), sim.read_status()


def test_hold_lengthens_after_a_press_that_did_not_heal(workdir):
    sim = fight(workdir)                      # no heal_on_inject: nothing restores HP
    # Quick slot at ~0.4 s, its 2 s effect window, then the gourd fallback does
    # the same, so the first observed failure lands around 4.5 s.
    sim.step(1 / 60, 330)
    status = sim.read_status()
    assert status['heal_fail'] == '1', status
    assert int(status['heal_hold_ms']) > 200, status
