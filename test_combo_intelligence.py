from dataclasses import replace
from types import SimpleNamespace
import pytest
from combo_planner import choose_combo
from combo_controller import ComboController
from combat import CombatSettings,CombatScheduler


def state():
    paths=['A','AA','AAA','AAAA','AAAAA','B','BB','BBB','AB','ABB','AAB','AABB','AAAB','AAABB']
    timing={'A':.3717,'AA':.624,'AAA':.724,'AB':1.3748,'AAB':1.4321,'AAAB':1.3051}
    moves={p:dict(path=p,name=p,instance=100+i,count=0,active=False,cost=82 if p.endswith('B') else 0,
                  available=True,combo_open=timing.get(p,.8),combo_close=timing.get(p,.8)+.3) for i,p in enumerate(paths)}
    return SimpleNamespace(valid=True,reason='ok',player=1,component=2,world=3,game_time=100.,
        hp=850.,hp_max=850.,shachi=400.,shachi_max=400.,medicine=4,target=10,target_distance=2.,
        deflect_component=5,threats=[],tactics=dict(weapon='Weapon_Hun_Tang',move_model_ready=True,
        moves=moves,move_events={p:m.copy() for p,m in moves.items()},phase='',active_moves=[],
        owned_tags=['CharacterState.Space.Ground'],skills={},
        enemies=[dict(actor=10,hp=800,hp_max=1000,distance=2,boss=False,shachi_ratio=.8)]))


def acknowledge(d,s,path,now,phase='Act'):
    s.game_time=100+now
    s.tactics['move_events'][path]['count']+=1
    s.tactics['phase']='CharacterState.Action.Phase.'+phase
    s.tactics['active_moves']=[path]
    d.observe(now,s)


def at(d,s,now):
    s.game_time=100+now
    return d.tick(now,s,CombatSettings())


def planned(path):
    s=state();d=ComboController();d.observe(0,s)
    d.plan=replace(choose_combo(s),path=path,label=path)
    return d,s


def test_five_video_combos_all_selected_in_safe_encounter():
    s=state();history=[]
    for _ in range(12):history.append(choose_combo(s,history).path)
    assert {'AAAAA','BBB','ABB','AABB','AAABB'}<=set(history)
    assert all(a!=b for a,b in zip(history,history[1:]))


def test_entire_chain_must_be_available_not_just_last_ability():
    s=state();s.tactics['moves']['AAAB']['available']=False
    assert choose_combo(s).path!='AAABB'
    del s.tactics['moves']['AB']
    for i in range(8):assert choose_combo(s,['AAAAA','AABB','BBB']*i).path!='ABB'


def test_upgrade_ability_without_upgrade_tag_never_selected():
    s=state();s.tactics['moves']['AAAAAA']=dict(s.tactics['moves']['A'],path='AAAAAA',available=False)
    history=[]
    for _ in range(20):history.append(choose_combo(s,history).path)
    assert 'AAAAAA' not in history


@pytest.mark.parametrize('resource',[90,150,220,300,400])
def test_total_combo_cost_leaves_defense_reserve(resource):
    s=state();s.shachi=resource
    p=choose_combo(s)
    assert p.cost==0 or s.shachi-p.cost>=p.reserve


def test_boss_waits_for_resource_break_before_long_combo():
    s=state();s.tactics['enemies'][0]['boss']=True
    assert len(choose_combo(s).path)<=2
    s.tactics['enemies'][0]['shachi_ratio']=.05
    assert len(choose_combo(s).path)>=3


def test_new_group_limits_heavy_commitment():
    s=state();e=s.tactics['enemies'][0]
    s.tactics['enemies'] += [dict(e,actor=11),dict(e,actor=12)]
    p=choose_combo(s)
    assert len(p.path)<=3 and p.path.count('B')<=1


def test_near_death_target_and_recent_hurt_override_variety():
    s=state();s.tactics['enemies'][0]['hp']=30
    assert len(choose_combo(s,['A','AA','AB']).path)<=2
    assert choose_combo(s,hurt=True).path=='A'


def test_long_heavy_waits_for_its_own_combo_window():
    d,s=planned('ABB')
    assert at(d,s,0)==('tap','鼠标左键')
    acknowledge(d,s,'A',.02)
    assert at(d,s,.2) is None
    assert at(d,s,.32)==('tap','F')
    acknowledge(d,s,'AB',.4)
    assert at(d,s,1.1) is None
    assert at(d,s,1.72)==('tap','F')
    assert d.confirmed==2


def test_actual_combo_phase_overrides_predicted_wait():
    d,s=planned('ABB');at(d,s,0);acknowledge(d,s,'A',.02,phase='Combo')
    assert at(d,s,.04) is None
    assert at(d,s,.14)==('tap','F')


def test_repeated_actual_first_attack_does_not_count_as_second_step():
    d,s=planned('AABB');at(d,s,0);acknowledge(d,s,'A',.02,phase='Combo');at(d,s,.14)
    acknowledge(d,s,'A',.18)
    assert d.repairs==1 and d.plan is None and d.confirmed==1
    assert at(d,s,.2) is None


def test_no_second_input_until_previous_move_is_observed():
    d,s=planned('ABB');at(d,s,0)
    assert at(d,s,.4) is None and at(d,s,1.0) is None
    s.game_time=101.6;d.observe(1.6,s)
    assert d.repairs==1 and d.pending is None
    assert at(d,s,1.7) is None


def test_finished_combo_waits_for_recovery_before_restarting():
    d,s=planned('A');at(d,s,0);acknowledge(d,s,'A',.02,phase='Combo')
    assert d.completed==1 and at(d,s,.05) is None
    s.tactics['phase']='CharacterState.Action.Phase.Cancel'
    assert at(d,s,.5) is not None


def test_resource_loss_cancels_expensive_followup():
    d,s=planned('ABB');at(d,s,0);acknowledge(d,s,'A',.02,phase='Combo')
    s.shachi=150
    assert at(d,s,.05) is None and d.plan is None


def test_phase_end_resets_chain_instead_of_wrong_heavy():
    d,s=planned('ABB');at(d,s,0);acknowledge(d,s,'A',.02,phase='Cancel')
    assert at(d,s,.05) is None and d.plan is None


def test_expired_window_never_sends_old_followup():
    d,s=planned('ABB');at(d,s,0);acknowledge(d,s,'A',.02)
    assert at(d,s,1.1) is None and d.plan is None


def test_player_target_or_weapon_change_invalidates_pending_move():
    for field in ('target','player','weapon'):
        d,s=planned('ABB');at(d,s,0)
        if field=='weapon':s.tactics[field]='Weapon_Hun_Double'
        else:setattr(s,field,999)
        acknowledge(d,s,'A',.02)
        assert d.confirmed==0 and d.pending is None


def test_old_instance_counter_cannot_confirm_new_instance():
    d,s=planned('ABB');at(d,s,0)
    s.tactics['move_events']['A']['instance']=999
    acknowledge(d,s,'A',.02)
    assert d.confirmed==0 and d.pending


def test_airborne_player_does_not_use_ground_combo():
    d,s=planned('ABB');s.tactics['owned_tags']=['CharacterState.Space.Air']
    assert at(d,s,0) is None


def test_new_defense_threat_interrupts_modern_combo():
    s=state();c=CombatScheduler(CombatSettings());c.arm(0,s);c.next_attack=0
    assert c.tick(.01,True,s)==[('tap','鼠标左键')]
    s.game_time=100.04;s.threats=[dict(id=1,hit_time=100.15,dodge=True)]
    assert c.tick(.04,True,s)==[('tap','Shift')]
    assert c.director.pending is None and c.director.plan is None


def test_skill_spends_only_when_readiness_and_scene_allow():
    s=state();art=dict(cost=1,charge=2,unlocked=True,cooldown_known=True,active=False,instance=200,activation=0)
    s.tactics['skills']['combat_art']=art
    c=CombatScheduler(CombatSettings());c.arm(0,s);c.next_attack=0
    assert c.tick(.01,True,s)==[('tap','Q')]
    art['activation']=1;s.game_time=100.04
    c.tick(.04,True,s)
    assert c.actions_count['skill_confirmed']==1
    for key,value in [('charge',0),('cooldown_known',False),('active',True)]:
        s=state();s.tactics['skills']['combat_art']=dict(art,**{key:value})
        assert not choose_combo(s).skill
