"""Regressions run the shipped Lua, including actual legacy defense arbitration.

These exercise scheduling and input lifetimes, not Unreal animation acceptance.
"""
import pytest
import tempfile
from pathlib import Path

from sim_mod_lua import (
    Actor, AttributeSet, AbilitySystemStub, BulletSource, DeflectComponent, FactionComponent, FName,
    Frame, MOD_SOURCE, Sim, TargetingComponent, WeaponComponent, AbilityInputStub, build,
)


@pytest.fixture
def tmp_path():
    # Lupa's Windows Lua C IO uses ANSI paths. Keep simulator IO in the ASCII
    # workspace, matching the actual installed game's path.
    root = Path(__file__).resolve().parent / 'evidence' / 'lua-pytest'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='case-', dir=root) as folder:
        yield Path(folder)


def make_fight(path, *, distance=150, **settings):
    sim = Sim(path)
    sim.actor = Actor('BP_Renewal_Hun_C_1', 'BP_Renewal_Hun_C')
    sim.actor.attributes = AttributeSet(sim.actor, {
        'Health': 1000., 'Health_Max': 1000., 'Shachi': 400., 'Shachi_Max': 400.,
        'UltimateChargeCount': 0., 'SpiritShadow': 0.,
    })
    boss = Actor('BP_Monster_JingYe_C_1', 'BP_Monster_JingYe_C', (distance, 0, 0))
    boss.attributes = AttributeSet(boss, {'Health': 5000., 'Health_Max': 5000.})
    boss.AbilitySystemComponent = AbilitySystemStub()
    sim.extra_actors = [boss]
    sim.faction_components = [FactionComponent(boss, 'Faction.Enemy')]
    sim.actions = [Actor(name, 'InputAction') for name in (
        'IA_AttackL_Press', 'IA_AttackH_Press', 'IA_MoveInput', 'IA_Deflect',
        'IA_Dodge', 'IA_DodgeHold', 'IA_UseBottomItem', 'IA_UseTopItem', 'IA_CombatArt',
        'IA_LockTarget',
    )]
    sim.deflect = DeflectComponent(sim.actor)
    sim.damage_on_attack = False
    build(sim, MOD_SOURCE.read_text(encoding='utf-8'))
    sim.write_control(attack_distance=3.5, execution_enabled=0, lock_enabled=0,
                      switch_enabled=0, **settings)
    return sim, boss


def presses(sim, prefix='IA_Attack'):
    return [(name, at) for name, value, at in sim.timed_injections
            if name.startswith(prefix) and value > 0]


def check_clean(sim):
    sim.step(count=35)
    assert sim.read_status()['error'] == ''


def test_repeated_defense_preserves_heavy_progress(tmp_path):
    sim, _ = make_fight(tmp_path)
    # Interrupt after each of the first two lights. The chain is a window
    # aligned AAAB now, so the finisher is the fourth press, not the third.
    for handle in (1, 2):
        before = len(presses(sim))
        for _ in range(120):
            sim.step()
            if len(presses(sim)) > before:
                break
        assert len(presses(sim)) == before + 1
        assert presses(sim)[-1][0] == 'IA_AttackL_Press'
        sim.deflect.ReceiveFrames.frames[handle] = Frame(sim.game_time + .01, is_dodge=True)
        sim.step()
        assert any(name.startswith('IA_Attack') and value == 0 and at == sim.clock_value
                   for name, value, at in sim.timed_injections)
        sim.deflect.ReceiveFrames.frames.clear()
    sim.step(count=200)
    sequence = [n for n, _ in presses(sim)[:4]]
    assert sequence == ['IA_AttackL_Press'] * 3 + ['IA_AttackH_Press'], presses(sim)
    check_clean(sim)


def test_refreshed_threat_after_dodge_releases_hold_and_resumes_attacks(tmp_path):
    sim, _ = make_fight(tmp_path, distance=150)
    sim.deflect.ReceiveFrames.frames[1] = Frame(sim.game_time + .01, is_dodge=True)
    sim.step()
    dodge_started = sim.clock_value

    # The enemy keeps refreshing a future hit time on the same frame handle.
    # This used to keep GuardUntil ahead of the clock, freezing both offense
    # and the injected IA_DodgeHold input after the initial dodge completed.
    for _ in range(36):
        sim.deflect.ReceiveFrames.frames[1] = Frame(sim.game_time + .30, is_dodge=True)
        sim.step()

    assert presses(sim), sim.read_status()
    hold_releases = [at for name, value, at in sim.timed_injections
                     if name == 'IA_DodgeHold' and value == 0 and at > dodge_started]
    assert hold_releases, sim.timed_injections
    assert sim.read_status()['combat_yield_reason'] == '', sim.read_status()
    check_clean(sim)


def test_rider_is_stable_and_existing_unreadable_lock_not_toggled(tmp_path):
    sim, boss = make_fight(tmp_path, distance=240)
    horse = Actor('horse', 'BP_Monster_Horse_JingYe_C', (220, 0, 0))
    horse.attributes = AttributeSet(horse, {'Health': 5000., 'Health_Max': 5000.})
    horse._lua = sim.lua
    sim.extra_actors.append(horse)
    sim.faction_components.append(FactionComponent(horse, 'Faction.Enemy'))
    class UnreadableLock(TargetingComponent):
        def HasLockTarget(self):
            return True
        def GetLockTarget(self):
            return None
    sim.targeting = UnreadableLock(sim.actor)
    sim.write_control(lock_enabled=1, skills_enabled=0, attack_distance=3.5)
    sim._walk = lambda dt: None  # mount collision prevents further approach
    for n in range(5):
        horse._position = (220 + 35 * (n % 2), 0, 0)
        sim.step(count=60)
        assert sim.read_status()['target'] == boss._name
    assert not presses(sim, 'IA_LockTarget')
    assert len(presses(sim)) >= 4
    assert any(n == 'IA_AttackH_Press' for n, _ in presses(sim))
    check_clean(sim)


@pytest.mark.parametrize('distance', [280, 450])
def test_player_requested_combo_is_independent_of_actor_center_distance(tmp_path, distance):
    sim, _ = make_fight(tmp_path, distance=distance)
    sim._walk = lambda dt: None
    sim.step(count=240)
    assert presses(sim)
    assert not any(abs(x) + abs(y) > 0 for x, y in sim.move_inputs)
    check_clean(sim)


def test_distant_jingye_is_reported_without_auto_pursuit(tmp_path):
    sim, boss = make_fight(tmp_path, distance=6500)
    sim._walk = lambda _dt: None
    sim.step(count=150)
    status = sim.read_status()
    assert status['target'] == boss._name, status
    assert status['action'].startswith('combo_'), status
    assert int(status['enemy_count']) == 1, status
    assert not any(abs(x) + abs(y) > 0 for x, y in sim.move_inputs)
    assert presses(sim)
    check_clean(sim)


def test_regular_enemy_keeps_the_existing_30m_limit(tmp_path):
    sim, enemy = make_fight(tmp_path, distance=6500)
    enemy._class = 'BP_Monster_Wolf_C'
    sim._walk = lambda _dt: None
    sim.step(count=150)
    status = sim.read_status()
    assert status['target'] == '', status
    assert status['action'].startswith('combo_'), status
    assert 'BP_Monster_Wolf_C:distance_65.0m' in sim.read_diag()['enemy_rejected']
    assert presses(sim)  # no target is needed to follow a physical hold gesture
    check_clean(sim)


def test_heal_effect_observation_does_not_freeze_combat(tmp_path):
    sim, _ = make_fight(tmp_path)
    sim.extra_actors[0]._class = 'BP_Monster_Wolf_C'
    sim.actor.attributes._values['Health'] = 300
    sim.damage_on_attack = False
    for _ in range(90):
        sim.step()
        if presses(sim, 'IA_UseBottomItem'):
            break
    start = presses(sim, 'IA_UseBottomItem')[0][1]
    sim.step(count=100)
    attacks_after_recovery = [at for _, at in presses(sim) if at >= start + .8]
    assert attacks_after_recovery, sim.read_status()
    assert sim.read_status()['heal_state'] == 'waiting_effect'
    check_clean(sim)


def test_readable_native_lock_wins_over_nearer_enemy(tmp_path):
    sim, boss = make_fight(tmp_path, distance=200)
    other = Actor('nearby', 'BP_Monster_Wolf_C', (100, 0, 0))
    other.attributes = AttributeSet(other, {'Health': 5000., 'Health_Max': 5000.})
    other._lua = sim.lua
    sim.extra_actors.append(other)
    sim.faction_components.append(FactionComponent(other, 'Faction.Enemy'))
    sim.targeting = TargetingComponent(sim.actor, boss)
    sim.step(count=90)
    assert sim.read_status()['target'] == boss._name
    boss.attributes._values['Health'] = 0
    sim.step(count=90)
    assert sim.read_status()['target'] == other._name


def test_break_punish_once_per_window_and_rearms(tmp_path):
    sim, boss = make_fight(tmp_path)
    boss.AbilitySystemComponent.tags.add('CharacterState.Stand.Break')
    sim.step(count=80)
    assert presses(sim)[0][0] == 'IA_AttackH_Press'
    assert sim.read_status()['boss_punishes'] == '1'
    boss.AbilitySystemComponent.tags.clear()
    sim.step(count=20)
    boss.AbilitySystemComponent.tags.add('CharacterState.Stand.Break')
    sim.step(count=80)
    assert sim.read_status()['boss_punishes'] == '2'
    check_clean(sim)


@pytest.mark.parametrize('setting', ['empty_resource', 'light_only', 'short_flinch', 'disabled'])
def test_no_invalid_opening_heavy(tmp_path, setting):
    config = {'combo_preset': 'light'} if setting == 'light_only' else {}
    if setting == 'disabled':
        config['attack_enabled'] = 0
    sim, boss = make_fight(tmp_path, **config)
    if setting == 'empty_resource':
        sim.actor.attributes._values['Shachi'] = 0
    tag = 'CharacterState.Hitreact.Phase' if setting == 'short_flinch' else 'CharacterState.Stand.Break'
    boss.AbilitySystemComponent.tags.add(tag)
    if setting == 'short_flinch':
        sim.step(count=5)
        boss.AbilitySystemComponent.tags.clear()
    sim.step(count=90)
    assert sim.read_status()['boss_punishes'] == '0'
    if setting == 'disabled':
        assert not presses(sim)
    elif setting != 'short_flinch':
        assert presses(sim) and all(n == 'IA_AttackL_Press' for n, _ in presses(sim))
    check_clean(sim)


def test_opening_still_yields_to_incoming_attack(tmp_path):
    sim, boss = make_fight(tmp_path)
    boss.AbilitySystemComponent.tags.add('CharacterState.Stand.Break')
    sim.deflect.ReceiveFrames.frames[1] = Frame(sim.game_time + .03, is_dodge=True)
    sim.step(count=6)
    assert not presses(sim)
    assert presses(sim, 'IA_Dodge')
    sim.deflect.ReceiveFrames.frames.clear()
    sim.step(count=80)
    assert presses(sim)[0][0] == 'IA_AttackH_Press'


def test_flinch_debounce_applies_between_tag_poll_ticks(tmp_path):
    sim, boss = make_fight(tmp_path, attack_period=.6)
    sim.step(count=20)
    boss.AbilitySystemComponent.tags.add('CharacterState.Hitreact.Phase')
    sim.step(count=10)  # next attack becomes due during this short hit reaction
    boss.AbilitySystemComponent.tags.clear()
    sim.step(count=60)
    assert sim.read_status()['boss_punishes'] == '0'


@pytest.mark.parametrize('item,hp', [('IA_UseBottomItem', 200), ('IA_UseTopItem', 550)])
def test_medicine_pulse_survives_arbitration_and_recovery(tmp_path, item, hp):
    sim, _ = make_fight(tmp_path)
    sim.actor.attributes._values['Health'] = hp
    for _ in range(90):
        sim.step()
        if presses(sim, item):
            break
    start = presses(sim, item)[0][1]
    # Emulate the engine consuming the input on a later frame, not in Inject().
    # The quick slot holds longer than the support slot: a single-frame item
    # press is eaten by the drink animation.
    sim.step(count=16)
    releases = [at for name, value, at in sim.timed_injections if name == item and value == 0]
    minimum_hold = .11 if item == 'IA_UseTopItem' else .20
    assert releases and releases[0] - start >= minimum_hold
    sim.actor.attributes._values['Health'] = 800
    sim.step(count=15)
    assert not [at for _, at in presses(sim) if start <= at < start + .60]
    sim.step(count=120)
    assert presses(sim)
    check_clean(sim)


def test_skill_has_priority_and_recovery_before_opening_heavy(tmp_path):
    sim, boss = make_fight(tmp_path)
    sim.actor.attributes._values['UltimateChargeCount'] = 1
    boss.AbilitySystemComponent.tags.add('CharacterState.Stand.Break')
    sim.step(count=100)
    skill_at = presses(sim, 'IA_CombatArt')[0][1]
    attack, attack_at = presses(sim)[0]
    assert attack == 'IA_AttackH_Press' and attack_at - skill_at >= .65
    assert sim.read_status()['boss_punishes'] == '1'


def test_missing_heavy_continues_light_instead_of_stalling(tmp_path):
    sim, _ = make_fight(tmp_path)
    sim.actions = [a for a in sim.actions if a._name != 'IA_AttackH_Press']
    sim.step(count=540)
    # Without a heavy mapping the plan falls back to the free light chain and the
    # input windows keep their spacing instead of stalling after the first hit.
    assert len(presses(sim)) >= 12, presses(sim)
    assert all(n == 'IA_AttackL_Press' for n, _ in presses(sim))
    gaps = [b[1] - a[1] for a, b in zip(presses(sim), presses(sim)[1:])]
    assert max(gaps) <= 1.1, gaps
    check_clean(sim)


def test_valid_weapon_components_are_not_rescanned_every_two_seconds(tmp_path):
    sim, _ = make_fight(tmp_path)
    sim.weapon_component = WeaponComponent(sim.actor)
    sim.weapon_input = AbilityInputStub(sim.actor, sim)
    sim.write_control(switch_enabled=1, lock_enabled=0, execution_enabled=0)
    sim.step(count=620)
    assert sim.find_calls['CharacterWeaponsComponent'] == 1
    assert sim.find_calls['AbilityInputComponent'] == 1
    check_clean(sim)


def test_support_slot_reports_observed_heal_and_releases_promptly(tmp_path):
    sim, _ = make_fight(tmp_path)
    sim.actor.attributes._values['Health'] = 550
    for _ in range(90):
        sim.step()
        if presses(sim, 'IA_UseTopItem'):
            break
    start = presses(sim, 'IA_UseTopItem')[0][1]
    sim.step(count=10)
    releases = [at for name, value, at in sim.timed_injections
                if name == 'IA_UseTopItem' and value == 0]
    assert releases and .11 <= releases[0] - start < .15, releases
    sim.actor.attributes._values['Health'] = 700
    sim.step(count=60)
    status = sim.read_status()
    assert status['support_state'] == 'hp_increased', status
    assert status['support_confirmed'] == '1', status


def test_support_slot_rechecks_after_effect_even_if_hp_reaches_95_percent(tmp_path):
    sim, _ = make_fight(tmp_path)
    sim.actor.attributes._values['Health'] = 550
    sim.heal_on_inject = {'IA_UseTopItem': 400}
    sim.step(count=1400)
    used = [at for name, value, at in sim.timed_injections
            if name == 'IA_UseTopItem' and value > 0]
    assert len(used) >= 2, used
    assert 15 <= used[1] - used[0] < 18, used


def test_support_slot_waits_until_announced_melee_threat_resolves(tmp_path):
    sim, _ = make_fight(tmp_path)
    sim.actor.attributes._values['Health'] = 550
    sim.deflect.ReceiveFrames.frames[1] = Frame(
        sim.game_time + .25, source=BulletSource('PBZ_SweepTraceBullet', attack_type=777))
    sim.step(count=12)
    assert not presses(sim, 'IA_UseTopItem')
    assert presses(sim, 'IA_Deflect')
    sim.deflect.ReceiveFrames.frames.clear()
    sim.step(count=45)
    assert presses(sim, 'IA_UseTopItem')
    check_clean(sim)


def test_defense_does_not_cancel_an_item_pulse_already_in_progress(tmp_path):
    sim, _ = make_fight(tmp_path)
    sim.actor.attributes._values['Health'] = 550
    for _ in range(90):
        sim.step()
        if presses(sim, 'IA_UseTopItem'):
            break
    start = presses(sim, 'IA_UseTopItem')[0][1]
    sim.deflect.ReceiveFrames.frames[1] = Frame(
        sim.game_time + .05, source=BulletSource('PBZ_SweepTraceBullet', attack_type=777))
    sim.step(count=12)
    releases = [at for name, value, at in sim.timed_injections
                if name == 'IA_UseTopItem' and value == 0]
    assert releases and releases[0] - start >= .11, releases
    assert presses(sim, 'IA_Deflect')
    assert sim.read_diag()['medicine_interrupted'] == '0'
    check_clean(sim)


def test_deflect_component_that_appears_after_entry_is_acquired_quickly(tmp_path):
    sim, _ = make_fight(tmp_path)
    sim.deflect = None
    sim.step(count=3)
    sim.deflect = DeflectComponent(sim.actor)
    sim.deflect.ReceiveFrames.frames[1] = Frame(
        sim.game_time + .30, source=BulletSource('PBZ_SweepTraceBullet', attack_type=777))
    sim.step(count=40)
    assert sim.read_status()['defense_ready'] == '1'
    assert presses(sim, 'IA_Deflect')
    assert not presses(sim, 'IA_Dodge')
    check_clean(sim)


def test_enemy_montage_diagnostics_run_while_defense_is_busy(tmp_path):
    class Montage:
        def IsValid(self):
            return True

        def GetFName(self):
            return FName('Montage_Attack_01')

    class AnimInstance:
        def IsValid(self):
            return True

        def GetCurrentActiveMontage(self):
            return montage

        def Montage_GetPosition(self, _montage):
            return .3

    class Mesh:
        def IsValid(self):
            return True

        def GetAnimInstance(self):
            return AnimInstance()

    sim, boss = make_fight(tmp_path)
    montage = Montage()
    boss.Mesh = Mesh()
    sim.deflect.ReceiveFrames.frames[1] = Frame(
        sim.game_time + .25, source=BulletSource('PBZ_SweepTraceBullet'))
    sim.step(count=140)
    assert 'Montage_Attack_01' in sim.read_diag().get('enemy_montages', '')
