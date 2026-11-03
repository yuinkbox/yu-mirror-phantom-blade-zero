"""Regression cases for bow enemies, defense-to-offense deadlocks, and Q admission."""
from dataclasses import replace
from types import SimpleNamespace
import copy
import json
from pathlib import Path
import struct
import pytest
from action_windows import can_start_offense
from combat import CombatScheduler,CombatSettings
from combo_controller import ComboController
from weapon_policy import combat_art_decision
from target_control import TargetControl
from tactics_runtime import TacticalReader
from test_combo_intelligence import state


def guard_recovery(s):
    s.tactics.update(phase='CharacterState.Action.Phase.Combo',active_moves=[])
    s.tactics['owned_tags']+=['Ability.Action.Stance','CharacterState.AbilityState.Stance.InDeflectTimeline']
    return s


def scheduler(s):
    c=CombatScheduler(CombatSettings());c.arm(0,s);c.next_attack=0
    return c


def skill(s,charge=1):
    s.tactics['skills']['combat_art']=dict(cost=1,charge=charge,unlocked=True,cooldown_known=True,
        active=False,instance=123,activation=2,required_tags=[],blocked_tags=[])
    return s


def test_guard_combo_recovery_allows_new_attack_instead_of_waiting_forever():
    s=guard_recovery(state());c=scheduler(s)
    assert c.tick(.01,True,s)[0][1] in ('F','鼠标左键')
    assert c.director.pending


@pytest.mark.parametrize('condition',['held_guard','active_light','hit_reaction','unknown','act','normal_combo'])
def test_recovery_fix_does_not_bypass_active_or_unknown_action(condition):
    s=guard_recovery(state())
    if condition=='held_guard':s.tactics['owned_tags'].append('CharacterState.AbilityState.Stance.HoldingStanceKey')
    if condition=='active_light':s.tactics['active_moves']=['GA_Hun_Tang_A_C']
    if condition=='hit_reaction':s.tactics['owned_tags'].append('CharacterState.Hitreact.Phase.Combo')
    if condition=='unknown':s.tactics['phase']='unknown'
    if condition=='act':s.tactics['phase']='CharacterState.Action.Phase.Act'
    if condition=='normal_combo':s.tactics['owned_tags'].remove('Ability.Action.Stance')
    assert not can_start_offense(s.tactics)


def test_unarmed_ground_player_can_acquire_nearby_bow_enemy():
    s=state();s.target=0;s.target_distance=None
    assert 'CharacterState.Armed' not in s.tactics['owned_tags']
    s.tactics['enemies'][0].update(class_name='BP_Monster_ArrowMan_Bow_C',distance=1.5)
    t=TargetControl();t.observe(0,s)
    assert t.request(0,s,CombatSettings())==('tap','鼠标中键')


def test_nearby_bow_enemy_can_be_attacked_after_native_lock_confirmation():
    s=state();s.target=0;s.target_distance=None
    s.tactics['enemies'][0].update(class_name='BP_Monster_ArrowMan_Bow_C',distance=1.5)
    c=scheduler(s)
    assert c.tick(.01,True,s)==[('tap','鼠标中键')]
    s.target=10;s.target_distance=1.5;s.game_time+=.35
    actions=c.tick(.36,True,s)
    assert actions and actions[0][1] in ('F','鼠标左键')
    assert c.actions_count['lock_confirmed']==1


def test_short_attack_continues_during_distant_arrow_then_dodges_on_time():
    s=state();c=scheduler(s)
    s.threats=[dict(id='arrow:1',hit_time=100.85,dodge=True,attack_type='projectile',projectile=True)]
    assert c.tick(.01,True,s)==[('tap','鼠标左键')]
    for now in (.2,.4,.6):
        s.game_time=100+now
        assert not any(a[1]=='Shift' for a in c.tick(now,True,s))
    s.game_time=100.7
    assert c.tick(.7,True,s)==[('tap','Shift')]
    assert c.director.plan is None


def test_close_arrow_still_preempts_attack():
    s=state();c=scheduler(s)
    s.threats=[dict(id='arrow:1',hit_time=100.12,dodge=True,attack_type='projectile',projectile=True)]
    assert c.tick(.01,True,s)==[('tap','Shift')]
    assert c.actions_count['attack']==0


def test_already_handled_arrow_does_not_keep_blocking_all_offense():
    s=state();c=scheduler(s)
    s.threats=[dict(id='arrow:1',hit_time=100.12,dodge=True,attack_type='projectile',projectile=True)]
    c.handled['arrow:1']=100.12
    assert c.tick(.01,True,s) and c.actions_count['attack']==1


def test_single_charge_q_is_used_on_ordinary_enemy_with_half_health():
    s=skill(state());s.hp=465
    c=scheduler(s)
    assert c.tick(.01,True,s)==[('tap','Q')]


def test_q_uses_ultimate_charge_without_unrelated_shachi_floor():
    s=skill(state());s.shachi=30
    c=scheduler(s)
    assert c.tick(.01,True,s)==[('tap','Q')]


def test_q_allowed_in_cancel_window_even_with_unfinished_old_plan():
    s=skill(state());s.tactics['phase']='CharacterState.Action.Phase.Cancel'
    c=scheduler(s)
    from combo_planner import choose_combo
    c.director.observe(0,s);c.director.plan=choose_combo(s)
    assert c.tick(.01,True,s)==[('tap','Q')]
    assert c.director.plan is None


def test_q_allowed_after_released_guard_combo_window():
    s=skill(guard_recovery(state()));c=scheduler(s)
    assert c.tick(.01,True,s)==[('tap','Q')]


def test_q_does_not_depend_on_a_selectable_ab_combo():
    s=skill(state());s.tactics['moves']={};s.tactics['move_events']={};s.tactics['move_model_ready']=False
    c=scheduler(s)
    assert c.tick(.01,True,s)==[('tap','Q')]


def test_q_no_longer_stalls_at_exactly_two_nearby_enemies():
    s=skill(state());s.tactics['enemies'].append(dict(s.tactics['enemies'][0],actor=11))
    assert scheduler(s).tick(.01,True,s)==[('tap','Q')]


def test_boss_q_uses_post_defense_opening():
    s=skill(guard_recovery(state()));s.tactics['enemies'][0]['boss']=True
    c=scheduler(s);c.last_defense=-.5
    assert c.tick(.01,True,s)==[('tap','Q')]


@pytest.mark.parametrize('condition',['zero_charge','unknown_cost','hurt','low_health','busy','forbidden','air','new_threat','boss_no_opening'])
def test_q_respects_real_resource_and_safety_constraints(condition):
    s=skill(state());c=scheduler(s)
    if condition=='zero_charge':s.tactics['skills']['combat_art']['charge']=0
    if condition=='unknown_cost':s.tactics['skills']['combat_art']['cost']=None
    if condition=='hurt':c.director.hurt_until=2
    if condition=='low_health':s.hp=200
    if condition=='busy':s.tactics['phase']='CharacterState.Action.Phase.Act'
    if condition=='forbidden':s.tactics['skills']['combat_art']['blocked_tags']=['CharacterState.Space.Ground']
    if condition=='air':s.tactics['owned_tags']=[]
    if condition=='new_threat':s.threats=[dict(id=1,hit_time=100.1,dodge=True)]
    if condition=='boss_no_opening':s.tactics['enemies'][0]['boss']=True
    assert ('tap','Q') not in c.tick(.01,True,s)


def test_q_diagnostic_explains_zero_charge():
    s=skill(state(),charge=0)
    assert combat_art_decision(s)==(False,'充能不足 0/1')


def test_game_bow_enemy_is_not_filtered_by_equipped_enemy_weapon():
    # Native offsets and attribute layout match the read-only ArrowMan sample.
    actor,attr,faction=1000,2000,3000
    raw=bytearray(20);struct.pack_into('<f',raw,0,1029.6);struct.pack_into('<f',raw,16,1029.6)
    m=SimpleNamespace(u64=lambda p:{faction+0xa8:actor,actor+0x758:attr,attr+0x20:actor}.get(p,0),
        read=lambda p,n:bytes(raw) if p==attr+0x64 else b'enemy' if p==faction+0x134 else b'')
    ref=SimpleNamespace(pointer=lambda a,n:faction if n=='Faction Component' else 0,name=lambda _: 'Faction.Enemy')
    t=TacticalReader.__new__(TacticalReader);t.m=m;t.ref=ref
    t.r=SimpleNamespace(objects=SimpleNamespace(valid_object=lambda _:True),position=lambda a:(150,0,0))
    t.actor_world=lambda _:42
    e=t.enemy(actor,(0,0,0),42)
    assert e and e['distance']==1.5 and e['hp']>1000


def test_recorded_guard_recovery_state_is_not_a_permanent_attack_deadlock():
    path=Path(__file__).parent/'evidence/fixes091/live-detailed.json'
    if not path.exists():pytest.skip('optional local read-only recording')
    s=SimpleNamespace(**json.loads(path.read_text('utf-8'))['state'])
    assert s.tactics['phase'].endswith('.Combo') and not s.tactics['active_moves']
    assert not (not s.tactics['phase'] or s.tactics['phase'].endswith('.Cancel'))
    actions=scheduler(s).tick(.01,True,s)
    assert actions and actions[0][1] in ('F','鼠标左键')
