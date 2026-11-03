"""Regression against observed shield encounter, upgrade refresh and stance buff."""
import copy,json,struct
from pathlib import Path
from types import SimpleNamespace
import pytest
from combat import CombatSettings
from move_runtime import MoveReader,binding_unlocked
from combo_planner import choose_combo
from test_fixes091 import scheduler,skill
from test_shadow092 import axe_state,advance
from test_combo_intelligence import state
from enhancement_controller import EnhancementController
from shadow_controller import ShadowController


@pytest.mark.parametrize('hp',[375.1123,455.7,460.1123])
def test_recorded_shield_encounter_below_55_percent_uses_quick_axe(hp):
    s=axe_state();s.hp=hp;c=scheduler(s)
    assert c.tick(.01,True,s)==[('tap','Ctrl')]
    assert not c.shadow.holding and c.actions_count['attack']==0


def combo_axe(s):
    s.tactics['phase']='CharacterState.Action.Phase.Combo'
    s.tactics['active_moves']=['Default__GA_Hun_Tang_A_C']
    s.tactics['shadow']['combo_bindings']=[dict(required=['CharacterState.Space.Ground'],
        ignored=['CharacterState.AbilityState.BrutalAxe.CanCombo','Ability.Action.Hun.ShachiAbility.BrutalAxe.Main'])]
    return s


def test_native_shadow_combo_binding_preempts_next_normal_attack():
    s=combo_axe(axe_state());s.hp=455.7;c=scheduler(s)
    c.director.observe(0,s);c.director.plan=choose_combo(s);c.director.step=1
    assert c.tick(.01,True,s)==[('tap','Ctrl')]
    assert c.director.plan is None


@pytest.mark.parametrize('condition',['not_combo','hitreact','shadow_active','missing_binding'])
def test_shadow_followup_requires_the_real_binding_and_valid_phase(condition):
    s=combo_axe(axe_state());c=ShadowController()
    if condition=='not_combo':s.tactics['phase']='CharacterState.Action.Phase.Act'
    if condition=='hitreact':s.tactics['owned_tags'].append('CharacterState.Hitreact.Phase.Combo')
    if condition=='shadow_active':s.tactics['owned_tags'].append('Ability.Action.Hun.ShachiAbility.BrutalAxe.Main')
    if condition=='missing_binding':s.tactics['shadow']['combo_bindings']=[]
    assert c.request(0,s,CombatSettings()) is None


def test_quick_axe_still_confirms_and_does_not_repeat_while_active():
    s=axe_state();s.hp=450;c=scheduler(s);c.tick(.01,True,s)
    s.tactics['shadow'].update(activation=12,active=True,ammo=3)
    for now in (.2,.5,.8,1.1):assert advance(c,s,now)==[]
    assert c.actions_count['shadow_confirmed']==1
    s.tactics['shadow']['active']=False;s.tactics['enemies'][0]['shield_present']=False
    assert advance(c,s,1.4)
    assert c.shadow.pending is None


def test_axe_uses_shorter_charge_if_full_charge_would_meet_threat():
    s=axe_state();s.threats=[dict(id=4,hit_time=102.6,dodge=False)]
    c=ShadowController();assert c.request(0,s,CombatSettings())==('down','Ctrl')
    assert c.pending['stage']==1


def test_healing_threshold_still_wins_over_axe():
    s=axe_state();s.hp=300;c=scheduler(s)
    assert ('tap','Ctrl') not in c.tick(.01,True,s)
    assert ('tap','R') in advance(c,s,.2)


def enhanced_state():
    s=state();s.tactics['enhancement']=dict(name='黑伤强化',unlocked=True,cooldown_known=True,
        instance=500,activation=0,active=False,buff_active=False,combo_open=2.7,required_tags=[],blocked_tags=[])
    return s


def test_unlocked_stance_buff_waits_for_native_guard_tag_then_heavy_and_releases():
    s=enhanced_state();c=scheduler(s)
    assert c.tick(.01,True,s)==[('down','鼠标右键')]
    assert advance(c,s,.1)==[]
    s.tactics['owned_tags'].append('CharacterState.AbilityState.Stance.HoldingStanceKey')
    assert advance(c,s,.2)==[('tap','F')]
    s.tactics['enhancement'].update(active=True,activation=1)
    assert advance(c,s,.3)==[('up','鼠标右键')]
    assert c.actions_count['enhancement_confirmed']==1
    assert c.actions_count['attack']==0


@pytest.mark.parametrize('reason',['locked','buff_active','unknown_effects','low_hp','shield','incoming','wrong_phase','multi','off'])
def test_buff_is_not_blindly_recast_or_used_in_unsafe_opening(reason):
    s=enhanced_state();e=s.tactics['enhancement'];cfg=CombatSettings()
    if reason=='locked':e['unlocked']=False
    if reason=='buff_active':e['buff_active']=True
    if reason=='unknown_effects':e['buff_active']=None
    if reason=='low_hp':s.hp=400
    if reason=='shield':s.tactics['enemies'][0]['shield_present']=True
    if reason=='incoming':s.threats=[dict(id=2,hit_time=101,dodge=True)]
    if reason=='wrong_phase':s.tactics['phase']='CharacterState.Action.Phase.Act'
    if reason=='multi':s.tactics['enemies'].append(dict(s.tactics['enemies'][0],actor=11))
    if reason=='off':cfg.skills_enabled=False
    assert EnhancementController().request(0,s,cfg) is None


def test_failed_stance_buff_releases_and_backs_off():
    s=enhanced_state();c=scheduler(s);c.tick(.01,True,s)
    advance(c,s,.2);advance(c,s,.5)
    assert ('up','鼠标右键') in advance(c,s,.7)
    assert c.enhancement.pending is None and c.enhancement.next_try>8


def test_buff_interruption_releases_guard_before_dodge_and_never_sends_heavy():
    s=enhanced_state();c=scheduler(s);c.tick(.01,True,s)
    s.threats=[dict(id=2,hit_time=100.25,dodge=True)]
    assert advance(c,s,.2)==[('up','鼠标右键'),('tap','Shift')]


def test_pause_releases_guard_for_enhancement():
    s=enhanced_state();c=scheduler(s);c.tick(.01,True,s)
    assert c.pause()==[('up','鼠标右键')]


def test_buff_interrupted_after_start_is_not_treated_as_40_seconds_of_success():
    s=enhanced_state();c=EnhancementController();cfg=CombatSettings()
    c.request(0,s,cfg);s.tactics['owned_tags'].append('CharacterState.AbilityState.Stance.HoldingStanceKey')
    c.observe(.1,s,cfg);s.tactics['enhancement'].update(activation=1,active=True)
    c.observe(.2,s,cfg)
    assert c.confirmed==1 and c.pending is not None
    s.hp-=10;c.observe(.4,s,cfg)
    assert c.pending is None and c.next_try<10


def test_buff_success_waits_for_effect_then_does_not_recast():
    s=enhanced_state();c=EnhancementController();cfg=CombatSettings()
    c.request(0,s,cfg);s.tactics['owned_tags'].append('CharacterState.AbilityState.Stance.HoldingStanceKey')
    c.observe(.1,s,cfg);s.tactics['enhancement'].update(activation=1,active=True)
    c.observe(.2,s,cfg);s.tactics['enhancement']['buff_active']=True;c.observe(2.5,s,cfg)
    assert c.pending is None and c.next_try>=42.5


@pytest.mark.parametrize('changed',['level','tags','weapon','player','world'])
def test_equipment_change_invalidates_both_binding_and_timing_caches(changed):
    r=MoveReader.__new__(MoveReader);r.weapon_cache={};r.timing_cache={};r.model_generation={};r.equipment_identity=None
    r.refresh_equipment(1,2,3,5,['CharacterState.WeaponUpgrade.Tang.CanBuff'])
    r.weapon_cache['old']=1;r.timing_cache['old']=1;r.model_generation['old']=0
    args=[1,2,3,5,['CharacterState.WeaponUpgrade.Tang.CanBuff']]
    if changed=='level':args[3]=15
    if changed=='tags':args[4].append('CharacterState.WeaponUpgrade.Tang.Combo_01')
    if changed=='weapon':args[2]=4
    if changed=='player':args[0]=4
    if changed=='world':args[1]=4
    r.refresh_equipment(*args)
    assert not r.weapon_cache and not r.timing_cache


def test_ordinary_combat_tags_do_not_invalidate_equipment_caches_each_frame():
    r=MoveReader.__new__(MoveReader);r.weapon_cache={};r.timing_cache={};r.model_generation={};r.equipment_identity=None
    r.refresh_equipment(1,2,3,5,['CharacterState.WeaponUpgrade.Tang.CanBuff'])
    r.weapon_cache['ok']=1
    r.refresh_equipment(1,2,3,5,['Ability.Action.Hun.Light.A.Normal','CharacterState.WeaponUpgrade.Tang.CanBuff'])
    assert r.weapon_cache=={'ok':1}


def test_same_config_reloads_in_place_bindings_after_refresh_interval(monkeypatch):
    config=100;granted=[200]
    t=SimpleNamespace(r=SimpleNamespace(objects=SimpleNamespace(valid_object=lambda _:True),sparse_map=lambda *a:[]),
        m=SimpleNamespace(read=lambda *a:bytes(8)),
        ref=SimpleNamespace(name=lambda _:'Weapon_Hun_Tang',field=lambda _,k:({'Equip Abilities':8,'Owning Abilities':24,'Weapon Combos':40}[k],16,0)))
    t.array=lambda addr,*a:[struct.pack('<Q',x) for x in granted] if addr==config+8 else []
    clock=[0.];monkeypatch.setattr('move_runtime.time.monotonic',lambda:clock[0])
    reader=MoveReader(t)
    assert reader.weapon_model(config)['classes']=={200}
    granted.append(201);clock[0]=1
    assert reader.weapon_model(config)['classes']=={200}
    clock[0]=2.1
    assert reader.weapon_model(config)['classes']=={200,201}


def test_real_upgrade_rules_unlock_finisher_without_restart_and_disable_on_removal():
    p=json.loads((Path(__file__).parent/'evidence/shield093/live-before.json').read_text('utf-8'))
    s=state();s.tactics=copy.deepcopy(p['state']['tactics']);s.target=10
    s.tactics['enemies']=[dict(actor=10,hp=900,hp_max=1000,distance=2,boss=False,shachi_ratio=.8)]
    rules=p['weapon_model']['rules'];moves=s.tactics['moves']
    new='CharacterState.WeaponUpgrade.Tang.Combo_01';tags=s.tactics['owned_tags']
    assert not moves['AAAAAA']['available']
    tags.append(new)
    moves['AAAAAA']['available']=binding_unlocked(moves['AAAAAA']['ability_tags'],rules,tags,moves['AAAAA']['ability_tags'])
    history=[]
    for _ in range(12):history.append(choose_combo(s,history).path)
    assert 'AAAAAA' in history
    tags.remove(new)
    assert not binding_unlocked(moves['AAAAAA']['ability_tags'],rules,tags,moves['AAAAA']['ability_tags'])
