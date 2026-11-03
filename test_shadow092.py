"""Resource-source regression and interruptible shield-breaking scenarios."""
import copy
import math
import struct
from types import SimpleNamespace
from dataclasses import replace
import pytest
from combat_resources import ultimate_resource,attribute
from combat import CombatSettings,CombatScheduler
from shadow_controller import ShadowController,shielded
from shadow_runtime import ShadowReader
from test_combo_intelligence import state
from test_fixes091 import skill,scheduler
from windows_io import InputSink


def resources(count=1.,maximum=3.,progress=0.,limit=100.):
    offsets={'UltimateChargeCount':0x8b8,'UltimateChargeCount_Max':0x8c8,
             'UltimateCharge':0x898,'UltimateCharge_Max':0x8a8}
    values=dict(zip(offsets,(count,maximum,progress,limit)))
    raw={2000+offsets[k]+12:struct.pack('<f',v) for k,v in values.items()}
    memory=SimpleNamespace(u64=lambda a:{1000+0x758:2000,2000+0x20:1000}.get(a,0),
        read=lambda a,n:raw.get(a,bytes(n)))
    t=SimpleNamespace(m=memory,r=SimpleNamespace(objects=SimpleNamespace(valid_object=lambda _:True)),
        ref=SimpleNamespace(field=lambda _,k:(offsets[k],16,0)))
    return t,raw


def axe_state():
    s=state();s.execution_ready=False
    s.tactics['enemies'][0].update(shield_present=True,shield=100,shield_max=100)
    s.tactics['shadow']=dict(weapon='Weapon_BrutalAxe',config=987,instance=789,supported=True,
        ammo=4.,ammo_max=4.,cost=1.,active=False,activation=11,charge_stage=0,
        cooldown_known=True,required_tags=[],blocked_tags=[],charge_times=[1.701,2.179],min_duration=1.432)
    return s


def advance(c,s,now):
    s.game_time=100+now
    return c.tick(now,True,s)


def test_real_charge_works_when_legacy_backpack_is_zero():
    t,_=resources();s=skill(state());s.tactics['skills']['combat_art'].update(ultimate_resource(t,1000))
    assert scheduler(s).tick(.01,True,s)==[('tap','Q')]
    assert s.tactics['skills']['combat_art']['charge_source']=='player.UltimateChargeCount'


def test_progress_is_not_a_spendable_charge():
    t,_=resources(0,3,99.9);v=ultimate_resource(t,1000)
    assert v['charge']==0 and v['charge_progress']<1
    s=skill(state());s.tactics['skills']['combat_art'].update(v)
    assert ('tap','Q') not in scheduler(s).tick(.01,True,s)


@pytest.mark.parametrize('count,maximum',[(float('nan'),3),(float('inf'),3),(-1,3),(4,3),(.5,3),(1,101)])
def test_invalid_resource_is_unknown_not_zero_or_unlimited(count,maximum):
    t,_=resources(count,maximum);assert ultimate_resource(t,1000)['charge'] is None


def test_wrong_attribute_owner_or_partial_read_is_not_spendable():
    t,raw=resources();raw[2000+0x8b8+12]=b'\x00'
    assert ultimate_resource(t,1000)['charge'] is None
    t.m.u64=lambda _:2000
    assert attribute(t,1000,'UltimateChargeCount') is None


def test_charge_confirmation_requires_same_ability_after_input():
    s=skill(state());c=scheduler(s);assert c.tick(.01,True,s)==[('tap','Q')]
    s.tactics['skills']['combat_art'].update(activation=3,charge=0)
    advance(c,s,.1)
    assert c.actions_count['skill_confirmed']==1


def test_shield_break_preempts_q_and_normal_combo():
    s=skill(axe_state());c=scheduler(s)
    assert c.tick(.01,True,s)==[('down','Ctrl')]
    assert c.actions_count['skill']==c.actions_count['attack']==0
    assert c.shadow.pending['stage']==2


def test_native_charge_stage_releases_axe_and_shield_break_resumes_q():
    s=skill(axe_state());c=scheduler(s);c.tick(.01,True,s)
    arm=s.tactics['shadow'];arm.update(active=True,activation=12,charge_stage=0,ammo=3)
    for now in (.2,.5,.8,1.1,1.4,1.7):assert advance(c,s,now)==[]
    arm['charge_stage']=1;assert advance(c,s,1.8)==[]
    arm['charge_stage']=2;assert advance(c,s,2.1)==[('up','Ctrl')]
    arm['active']=False;s.tactics['enemies'][0].update(shield_present=False,shield=0)
    assert advance(c,s,2.2)==[('tap','Q')]
    assert c.actions_count['shadow_confirmed']==1 and c.actions_count['shield_broken']==1


def test_surrounded_uses_shorter_single_stage():
    s=axe_state();s.tactics['enemies'].append(dict(s.tactics['enemies'][0],actor=11))
    c=scheduler(s);assert c.tick(.01,True,s)==[('down','Ctrl')]
    assert c.shadow.pending['stage']==1


def test_q_is_reserved_during_shield_break_retry_and_allowed_if_shadow_is_disabled():
    s=skill(axe_state());c=scheduler(s);c.shadow.next_try=10
    assert ('tap','Q') not in c.tick(.01,True,s)
    c=scheduler(s);c.config.shadow_enabled=False
    assert c.tick(.01,True,s)==[('tap','Q')]


@pytest.mark.parametrize('change',['no_shield','broken_hp','empty','unknown_ammo','unsupported','cooldown',
    'low_hp','busy','far','air','blocked','unconfirmed_instance','unknown_timing','incoming','setting_off'])
def test_axe_does_not_spend_or_charge_in_invalid_situations(change):
    s=axe_state();a=s.tactics['shadow'];c=scheduler(s)
    if change=='no_shield':s.tactics['enemies'][0]['shield_present']=False
    if change=='broken_hp':s.tactics['enemies'][0]['shield']=0
    if change=='empty':a['ammo']=0
    if change=='unknown_ammo':a['ammo']=None
    if change=='unsupported':a['supported']=False
    if change=='cooldown':a['cooldown_known']=False
    if change=='low_hp':s.hp=200
    if change=='busy':s.tactics['phase']='CharacterState.Action.Phase.Act'
    if change=='far':s.target_distance=5
    if change=='air':s.tactics['owned_tags']=[]
    if change=='blocked':a['blocked_tags']=['CharacterState.Space.Ground']
    if change=='unconfirmed_instance':a['activation']=None
    if change=='unknown_timing':a['charge_times']=[]
    if change=='incoming':s.threats=[dict(id=5,hit_time=101.4,dodge=True)]
    if change=='setting_off':c.config.shadow_enabled=False
    assert ('down','Ctrl') not in c.tick(.01,True,s)


@pytest.mark.parametrize('interrupt',['target','weapon','hurt','unknown','disabled','execution','far','broken'])
def test_charging_releases_on_changed_battle_state(interrupt):
    s=axe_state();c=scheduler(s);c.tick(.01,True,s)
    if interrupt=='target':s.target=11
    if interrupt=='weapon':s.tactics['shadow']['config']=543
    if interrupt=='hurt':s.hp-=5
    if interrupt=='unknown':s.tactics={}
    if interrupt=='disabled':c.config.shadow_enabled=False
    if interrupt=='execution':s.execution_ready=True;s.execution_target=10
    if interrupt=='far':s.target_distance=7
    if interrupt=='broken':s.tactics['enemies'][0].update(shield_present=False,shield=0)
    assert ('up','Ctrl') in advance(c,s,.2)
    assert not c.shadow.holding


@pytest.mark.parametrize('threat',[
    dict(id=1,hit_time=100.25,dodge=True,attack_type='red'),
    dict(id=1,hit_time=100.25,dodge=False,attack_type='normal'),
    dict(id=1,hit_time=100.25,dodge=True,attack_type='projectile',projectile=True)])
def test_urgent_defense_releases_ctrl_before_sending_defense(threat):
    s=axe_state();c=scheduler(s);c.tick(.01,True,s);s.threats=[threat]
    actions=advance(c,s,.2)
    assert actions[0]==('up','Ctrl') and len(actions)==2
    assert actions[1][1] in ('Shift','鼠标右键')


def test_missing_activation_has_finite_release_and_retry_backoff():
    s=axe_state();c=scheduler(s);c.tick(.01,True,s)
    for now in (.2,.5,.8):advance(c,s,now)
    assert advance(c,s,1.1)==[('up','Ctrl')]
    assert c.shadow.next_try>=9.1
    for now in (1.4,1.7,2.0):assert ('down','Ctrl') not in advance(c,s,now)
    assert c.actions_count['shadow_confirmed']==0


def test_charge_timeout_never_holds_forever():
    s=axe_state();c=scheduler(s);c.tick(.01,True,s)
    s.tactics['shadow'].update(active=True,activation=12)
    result=[]
    for n in range(1,16):result+=advance(c,s,n*.2)
    assert result.count(('up','Ctrl'))==1 and not c.shadow.holding


def test_f10_pause_and_focus_loss_release_held_charge():
    for mode in ('pause','focus','stale','invalid'):
        s=axe_state();c=scheduler(s);c.tick(.01,True,s)
        if mode=='pause':actions=c.pause()
        elif mode=='focus':actions=c.tick(.2,False,s)
        elif mode=='stale':actions=c.tick(1,True,s)
        else:s.valid=False;actions=c.tick(.2,True,s)
        assert ('up','Ctrl') in actions and not c.shadow.holding and c.shadow.pending is None


def test_input_sink_owns_and_releases_ctrl_without_affecting_manual_keys(monkeypatch):
    sink=InputSink();events=[]
    monkeypatch.setattr(sink,'_event',lambda k,d:events.append((k,d)))
    monkeypatch.setattr('windows_io.is_down',lambda _:False)
    window=SimpleNamespace(foreground=lambda:True)
    sink.dispatch(('down','Ctrl'),window,nonblocking=True)
    sink.pump(None)
    assert events==[('Ctrl',True),('Ctrl',False)] and not sink.held


def test_shadow_key_conflict_is_rejected():
    with pytest.raises(ValueError):CombatSettings(shadow_key='Q').validate()


def test_shield_detection_reads_live_tag_not_class_name():
    # A shield bearer retains the same class after its shield has broken.
    r=ShadowReader.__new__(ShadowReader)
    t,_=resources();r.t=t
    t.move_reader=SimpleNamespace(owned_tags=lambda _:['CharacterState.AbilityState.Shield.HasShield.Handle'])
    t.ref.field=lambda _,k:None
    assert r.shield(1000)['shield_present'] is True
    t.move_reader.owned_tags=lambda _:['CharacterType.MaleThug.SwordShield']
    assert r.shield(1000)['shield_present'] is False
