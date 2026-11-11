"""Regression cases for the 0.17.15 window aligned combo engine.

These run the shipped Lua against a game model that only chains an attack input
inside that move's combo_open..combo_close window, which is what the shipped
build does.  The old fixed cadence pressed outside the window on most steps.
"""
import pathlib
import sys
import tempfile

import pytest

from sim_mod_lua import (
    Actor, AbilitySystemStub, AttributeSet, DeflectComponent, FactionComponent, Frame,
    MOD_SOURCE, Sim, WindowedFight, build,
)

ROOT = pathlib.Path(__file__).resolve().parent


@pytest.fixture
def workdir():
    base = ROOT / 'evidence' / 'lua-pytest'
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='combo-', dir=base) as folder:
        yield pathlib.Path(folder)


def fight(workdir, *, shachi=400.0, game_kwargs=None, health=1000.0, **settings):
    sim = Sim(workdir)
    sim.actions = [Actor(name, 'InputAction') for name in (
        'IA_AttackL_Press', 'IA_AttackH_Press', 'IA_MoveInput', 'IA_Deflect',
        'IA_Dodge', 'IA_DodgeHold', 'IA_UseBottomItem', 'IA_UseTopItem')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': health, 'Health_Max': 1000.0, 'Shachi': shachi, 'Shachi_Max': 400.0,
        'SpiritShadow': 0.0, 'UltimateChargeCount': 0.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    monster = Actor('BP_Monster_Wolf_C_1', 'BP_Monster_Wolf_C', (200, 0, 0))
    monster.attributes = AttributeSet(monster, {'Health': 5000.0, 'Health_Max': 5000.0})
    sim.extra_actors = [monster]
    sim.faction_components = [FactionComponent(monster, 'Faction.Enemy')]
    sim.damage_on_attack = False
    sim.combo_game = WindowedFight(sim, **(game_kwargs or {}))
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control(lock_enabled=0, switch_enabled=0, execution_enabled=0, **settings)
    return sim


def attack_presses(sim, after=0.0):
    return [(name, at) for name, value, at in sim.timed_injections
            if name.startswith('IA_Attack') and value and value > 0 and at >= after]


def test_windowed_game_chains_the_full_aaab_plan(workdir):
    sim = fight(workdir)
    sim.step(1 / 60, 600)
    game = sim.combo_game
    assert game.accepted >= 4, game.presses
    assert game.completed >= 2, (game.completed, game.presses)
    # Near every input is inside its window now; the old cadence wasted most of them.
    assert game.rejected <= 1, [(key, round(at - 100, 2), path) for key, at, path in game.presses]
    assert [key for key, _, _ in game.presses][:4] == ['A', 'A', 'A', 'B'], game.presses
    status = sim.read_status()
    assert status['combo_mode'] == 'tag', status
    assert status['combo_signal'] == 'tag', status
    assert int(status['combo_confirm']) >= 6, status


def test_heavy_finisher_keeps_a_shachi_reserve(workdir):
    sim = fight(workdir, shachi=150.0)  # just under cost 82 + reserve 120
    sim.step(1 / 60, 420)
    game = sim.combo_game
    assert game.presses, game.presses
    assert all(key == 'A' for key, _, _ in game.presses), game.presses
    assert sim.read_status()['heavy_ok'] == '0', sim.read_status()


def test_broken_opening_may_spend_the_reserve(workdir):
    sim = fight(workdir, shachi=150.0)
    boss = sim.extra_actors[0]
    boss._class = 'BP_Boss_JingYe_C'
    boss.AbilitySystemComponent = AbilitySystemStub(tags=['CharacterState.Stand.Break'])
    sim.step(1 / 60, 120)
    assert any(name == 'IA_AttackH_Press' for name, _ in attack_presses(sim)), sim.timed_injections
    assert sim.read_status()['boss_punishes'] == '1', sim.read_status()


def test_phase_only_build_still_chains(workdir):
    sim = fight(workdir, game_kwargs={'publish_tags': False, 'publish_phase': True})
    sim.step(1 / 60, 600)
    game = sim.combo_game
    diag = sim.read_diag()
    assert diag['combo_mode'] == 'phase', diag
    assert game.completed >= 2, (game.completed, game.presses)
    assert game.rejected <= 3, [(key, round(at - 100, 2), path) for key, at, path in game.presses]


def test_defense_pauses_the_chain_instead_of_restarting_it(workdir):
    sim = fight(workdir)
    game = sim.combo_game
    for _ in range(360):
        sim.step()
        if len(game.path) >= 2:
            break
    assert len(game.path) >= 2, game.path
    expected = 'AAAB'[len(game.path)]
    before = len(attack_presses(sim))
    sim.deflect.ReceiveFrames.frames[1] = Frame(sim.game_time + .01, is_dodge=True)
    sim.step()
    assert len(attack_presses(sim)) == before, sim.timed_injections
    sim.deflect.ReceiveFrames.frames.clear()
    sim.step(count=100)
    resumed = attack_presses(sim)[before:]
    assert resumed, sim.timed_injections
    name = resumed[0][0]
    assert name == ('IA_AttackH_Press' if expected == 'B' else 'IA_AttackL_Press'), (expected, resumed)
    # The resumed press is timed from the press that started the running move,
    # not from the end of the dodge, so it still lands inside the window.
    assert game.rejected == 0, game.presses


def test_early_press_is_retried_inside_the_same_window(workdir):
    sim = fight(workdir, combo_speed=0.85)
    sim.step(1 / 60, 600)
    game = sim.combo_game
    diag = sim.read_diag()
    assert game.completed >= 2, (game.completed, game.presses)
    # Retries keep the input count bounded instead of turning into mashing.
    assert len(game.presses) <= game.accepted * 2 + 2, (len(game.presses), game.accepted)
    assert int(diag['combo_delay_ms']) > 0, diag


def test_heal_waits_while_a_move_tag_is_active(workdir):
    sim = fight(workdir, health=300.0)
    sim.deflect.ReceiveFrames.frames.clear()
    sim.step(1 / 60, 1)
    game = sim.combo_game
    # Hold the character mid-move: medicine must not be pressed into it.
    game.path, game.move_start = 'A', sim.clock_value
    game._set_tag(sim.clock_value)
    before = [at for name, value, at in sim.timed_injections
              if name in ('IA_UseBottomItem', 'IA_UseTopItem') and value]
    sim.step(1 / 60, 40)
    after = [at for name, value, at in sim.timed_injections
             if name in ('IA_UseBottomItem', 'IA_UseTopItem') and value]
    assert len(after) == len(before), sim.timed_injections
    assert sim.read_status()['chain_tag'] == 'Ability.Action.Hun.Light.A', sim.read_status()
    # Once the move is over, the medicine is allowed to go out.
    sim.step(1 / 60, 120)
    assert len([at for name, value, at in sim.timed_injections
                if name in ('IA_UseBottomItem', 'IA_UseTopItem') and value]) > len(before), \
        sim.timed_injections


def test_status_and_diag_expose_the_window_engine(workdir):
    sim = fight(workdir)
    sim.step(1 / 60, 240)
    status, diag = sim.read_status(), sim.read_diag()
    for key in ('combo_mode', 'combo_signal', 'combo_confirm', 'combo_reject',
                'combo_wait_ms', 'combo_tempo', 'chain_tag', 'phase_tag', 'heavy_ok',
                'combo_state', 'combo_step', 'combo_plan'):
        assert key in status, (key, status)
    for key in ('combo_path', 'combo_step', 'combo_wait_ms', 'combo_confirm',
                'combo_reject', 'combo_resync', 'combo_resets', 'combo_tempo_pct',
                'combo_delay_ms', 'combo_lead_ms', 'combo_speed', 'shachi_reserve',
                'chain_tag', 'phase_tag', 'tag_readable'):
        assert key in diag, (key, diag)
    assert status['combo_plan'] in ('balanced', 'pressure', 'swift'), status
    assert status['combo_step'].endswith('/4'), status
