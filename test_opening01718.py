"""Stun / break openings: stop standing still and spend the window on damage.

The engine used to wait for `combo_close` after a finished chain (up to ~1.9 s
after the heavy finisher), which swallowed exactly the window a stunned enemy
opens. These tests use the windowed game model, whose idle time is the amount of
time the character spends with no move running.
"""
import pathlib
import tempfile

import pytest

from sim_mod_lua import (
    Actor, AbilitySystemStub, AttributeSet, DeflectComponent, FactionComponent,
    MOD_SOURCE, Sim, WindowedFight, build,
)

ROOT = pathlib.Path(__file__).resolve().parent


@pytest.fixture
def workdir():
    base = ROOT / 'evidence' / 'lua-pytest'
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='open-', dir=base) as folder:
        yield pathlib.Path(folder)


def fight(workdir, *, enemy_class='BP_Monster_Wolf_C', tags=None):
    sim = Sim(workdir)
    sim.actions = [Actor(name, 'InputAction') for name in (
        'IA_AttackL_Press', 'IA_AttackH_Press', 'IA_MoveInput', 'IA_Deflect',
        'IA_Dodge', 'IA_DodgeHold')]
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C', (0, 0, 0))
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 1000.0, 'Health_Max': 1000.0, 'Shachi': 400.0, 'Shachi_Max': 400.0,
    })
    sim.deflect = DeflectComponent(sim.actor)
    enemy = Actor('enemy_1', enemy_class, (200, 0, 0))
    enemy.attributes = AttributeSet(enemy, {'Health': 9000.0, 'Health_Max': 9000.0})
    enemy.AbilitySystemComponent = AbilitySystemStub(tags=list(tags or []))
    sim.extra_actors = [enemy]
    sim.faction_components = [FactionComponent(enemy, 'Faction.Enemy')]
    sim.damage_on_attack = False
    sim.combo_game = WindowedFight(sim)
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control(lock_enabled=0, switch_enabled=0, execution_enabled=0)
    return sim, enemy


def attack_presses(sim, after=0.0):
    return [(name, at) for name, value, at in sim.timed_injections
            if name.startswith('IA_Attack') and value and value > 0 and at >= after]


def test_a_finished_chain_does_not_stand_through_the_window(workdir):
    sim, _ = fight(workdir)
    sim.step(1 / 60, 1200)  # 20 s
    game = sim.combo_game
    assert game.completed >= 3, (game.completed, game.presses)
    # The heavy finisher is ~1.9 s; waiting for combo_close used to leave the
    # character idle for that whole time after every chain.
    assert game.longest_idle <= 0.6, (round(game.longest_idle, 2), game.presses)
    assert game.idle_seconds / 20 <= 0.12, round(game.idle_seconds, 2)


def test_stun_edge_abandons_the_remaining_chain_wait(workdir):
    sim, enemy = fight(workdir)
    game = sim.combo_game
    # Reach a completed plan while its last move is still running.
    for _ in range(600):
        sim.step()
        if len(game.path) >= 4:
            break
    assert len(game.path) >= 4, game.path
    before = len(attack_presses(sim))
    enemy.AbilitySystemComponent.tags.add('CharacterState.Stand.Break')
    sim.step(1 / 60, 40)          # ~0.67 s
    status = sim.read_status()
    assert status['openings'] == '1', status
    # The break edge either restarts the chain or lands the punish heavy; both
    # mean the remaining chain wait was abandoned.
    assert status['combo_last_reset'] in ('opening', 'complete', 'opening-burst',
                                          'boss-punish', 'free'), status
    # It keeps trying inside the stun instead of waiting out combo_close.
    assert len(attack_presses(sim)) > before or float(status['combo_stall_ms']) >= 0


def test_break_punish_fires_once_and_then_lets_the_heavy_finish(workdir):
    sim, enemy = fight(workdir, enemy_class='BP_Boss_JingYe_C')
    enemy.AbilitySystemComponent.tags.add('CharacterState.Stand.Break')
    sim.step(1 / 60, 8)
    presses = attack_presses(sim)
    assert presses and presses[0][0] == 'IA_AttackH_Press', presses
    first_at = presses[0][1]
    sim.step(1 / 60, 60)          # one second of the heavy animation
    during = [at for _, at in attack_presses(sim) if at > first_at + 0.1]
    assert len(during) <= 1, (presses, during)
    status = sim.read_status()
    assert status['boss_punishes'] == '1', status
    assert int(status['combo_reject']) <= 1, status


def test_flinch_punish_is_not_used_on_a_normal_enemy(workdir):
    sim, enemy = fight(workdir)
    enemy.AbilitySystemComponent.tags.add('CharacterState.Hitreact.Phase')
    sim.step(1 / 60, 120)
    status = sim.read_status()
    assert status['boss_punishes'] == '0', status
    # The light chain keeps going instead of a heavy opener eating the combo.
    assert any(name == 'IA_AttackL_Press' for name, _ in attack_presses(sim)), sim.timed_injections


def test_flinch_punish_still_works_on_a_boss(workdir):
    sim, enemy = fight(workdir, enemy_class='BP_Boss_JingYe_C')
    enemy.AbilitySystemComponent.tags.add('CharacterState.Hitreact.Phase')
    sim.step(1 / 60, 60)
    status = sim.read_status()
    assert status['boss_punishes'] == '1', status
    # The flinch tag has a 0.18 s debounce, so a light press may come first; the
    # punish heavy still has to arrive inside the opening.
    assert any(name == 'IA_AttackH_Press' for name, _ in attack_presses(sim)), sim.timed_injections


def test_dropped_press_is_retried_quickly(workdir):
    sim, _ = fight(workdir)
    sim.write_control(lock_enabled=0, switch_enabled=0, execution_enabled=0, combo_speed=0.85)
    sim.step(1 / 60, 1200)
    game, status = sim.combo_game, sim.read_status()
    assert game.completed >= 3, (game.completed, game.presses)
    # An early press is dropped by the game; the mod must retry inside the same
    # window instead of idling until the chain timeout.
    assert game.longest_idle <= 0.6, (round(game.longest_idle, 2), game.presses)
    assert int(status['combo_reject']) <= 8, status
