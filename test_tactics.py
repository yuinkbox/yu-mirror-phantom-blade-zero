from types import SimpleNamespace
import pytest
from tactics import choose_plan
from tactics_runtime import boss_from_tags, TacticalReader
from combat import CombatSettings, CombatScheduler


def state(**changes):
    target=dict(actor=50,hp=800,hp_max=1000,distance=2,boss=False,shachi_ratio=.8)
    data=dict(valid=True,reason='ok',player=10,component=20,world=30,game_time=100.,
              hp=850.,hp_max=850.,shachi=400.,shachi_max=400.,deflect_component=40,
              medicine=4,target=50,target_distance=2.,threats=[],
              tactics=dict(enemies=[target],abilities=['Default__GA_Hun_Tang_AABB_C','Default__GA_Hun_Tang_ABB_C'],
                           skills={'combat_art':dict(cost=1,charge=3,unlocked=True,cooldown_known=True,active=False)},phase=''))
    data.update(changes)
    return SimpleNamespace(**data)


def test_only_explicit_game_tags_classify_boss():
    assert boss_from_tags(['CharacterType.Difficulty.NormalBoss'])
    assert boss_from_tags(['CharacterType.AI.Boss'])
    assert not boss_from_tags(['CharacterType.Difficulty.Elite','SomeBossName'])


def test_low_health_and_low_resource_override_spending():
    s=state(hp=200)
    assert choose_plan(s).mode=='保命' and not choose_plan(s).skill
    s=state(shachi=40)
    assert choose_plan(s).sequence==('light','light') and not choose_plan(s).skill


def test_target_near_death_conserves_full_charge():
    s=state();s.tactics['enemies'][0]['hp']=100
    assert choose_plan(s).mode=='收尾' and not choose_plan(s).skill


def test_group_counts_only_close_observed_hostiles():
    s=state();target=s.tactics['enemies'][0]
    s.tactics['enemies'] += [dict(target,actor=51,distance=6),dict(target,actor=52,distance=20)]
    assert choose_plan(s).mode=='双敌'
    s.tactics['enemies'][2]['distance']=6
    assert choose_plan(s).mode=='群战' and choose_plan(s).skill
    assert len(choose_plan(s).sequence)<=3


def test_boss_holds_charge_until_opening():
    s=state();s.tactics['enemies'][0]['boss']=True
    assert choose_plan(s).mode=='Boss 试探' and not choose_plan(s).skill
    assert choose_plan(s,after_defense=True).skill
    s.tactics['enemies'][0]['shachi_ratio']=.05
    assert choose_plan(s).skill


@pytest.mark.parametrize('key,value',[('charge',0),('charge',None),('cost',0),('cost',11),
                                    ('cooldown_known',False),('active',True),('unlocked',False)])
def test_unknown_unavailable_or_insufficient_skill_resources_block_cast(key,value):
    s=state();s.tactics['skills']['combat_art'][key]=value
    assert not choose_plan(s).skill


def test_busy_action_prevents_new_skill():
    s=state();s.tactics['phase']='Action.Phase.Main'
    assert not choose_plan(s).skill


def test_combo_varies_but_uses_only_granted_branches():
    s=state()
    assert choose_plan(s,0).sequence!=choose_plan(s,1).sequence
    assert choose_plan(s,2).sequence==choose_plan(s,0).sequence


def scheduler(s):
    c=CombatScheduler(CombatSettings());c.arm(0,s);c.next_attack=0
    return c


def tick(c,s,t):
    s.game_time=100+t
    return c.tick(t,True,s)


def test_scheduler_prioritizes_defense_over_charged_skill():
    s=state();c=scheduler(s)
    s.threats=[dict(id=1,hit_time=100.15,dodge=False)]
    assert tick(c,s,.02)==[('down','鼠标右键')]
    assert c.actions_count['skill']==0


def test_skill_count_requires_activation_or_charge_consumption():
    s=state();c=scheduler(s)
    assert tick(c,s,.02)==[('tap','Q')]
    assert c.actions_count['skill']==1 and c.actions_count['skill_confirmed']==0
    s.tactics['skills']['combat_art']['charge']=2
    tick(c,s,.04)
    assert c.actions_count['skill_confirmed']==1


def test_unconfirmed_skill_backs_off_without_spam_or_stopping_defense():
    s=state();c=scheduler(s);tick(c,s,.02)
    for i in range(1,18):tick(c,s,.02+i*.1)
    assert c.pending_skill is None and c.next_skill>9
    assert c.armed and c.actions_count['skill']==1


def test_stale_or_unrecognized_target_stops_offensive_inputs():
    s=state(tactics={});c=scheduler(s)
    assert tick(c,s,.02)==[]
    s=state();s.tactics['enemies'][0]['actor']=999;c=scheduler(s)
    assert tick(c,s,.02)==[]


def test_healing_preempts_combat_art():
    s=state(hp=250);c=scheduler(s)
    tick(c,s,.02)
    assert tick(c,s,.2)==[('tap','R')]
    assert c.actions_count['skill']==0


def test_tactical_runtime_excludes_friendly_before_reading_health():
    class Mem:
        def u64(self,p):return 123 if p==900+0xa8 else 0
        def read(self,p,n):return b'friendly!'
    class Ref:
        def pointer(self,*_):return 900
        def name(self,_):return 'Faction.Player'
    r=SimpleNamespace(objects=SimpleNamespace(valid_object=lambda _:True))
    t=TacticalReader.__new__(TacticalReader);t.r=r;t.m=Mem();t.ref=Ref()
    assert t.enemy(123,(0,0,0)) is None


def test_attack_chain_waits_for_observed_move_then_resumes():
    s=state();s.tactics['skills']={};s.tactics['move_counts']={'A':3};s.tactics['weapon']='Weapon_Hun_Tang'
    c=scheduler(s)
    assert tick(c,s,.02)==[('tap','鼠标左键')]
    assert c.combo_index==1
    tick(c,s,.3)
    assert tick(c,s,.52)==[]
    s.tactics['move_counts']['A']=4
    assert tick(c,s,.54)==[('tap','鼠标左键')]
    assert c.actions_count['attack_confirmed']==1


def test_missing_attack_activation_restarts_chain_without_skipping_to_heavy():
    s=state();s.tactics['skills']={};s.tactics['move_counts']={'A':3}
    c=scheduler(s);tick(c,s,.02)
    for t in (.3,.6,.9,1.1):tick(c,s,t)
    assert c.combo_index==0
    assert tick(c,s,1.36)==[('tap','鼠标左键')]
    assert c.actions_count['heavy']==0


def test_defense_interrupts_waiting_attack_immediately():
    s=state();s.tactics['skills']={};s.tactics['move_counts']={'A':3}
    c=scheduler(s);tick(c,s,.02)
    s.threats=[dict(id=6,hit_time=100.2,dodge=True)]
    assert tick(c,s,.04)==[('tap','Shift')]
    assert c.pending_attack is None and c.combo_index==0


def test_skill_instance_change_cannot_confirm_old_attempt():
    s=state();s.tactics['skills']['combat_art'].update(instance=80,activation=4)
    c=scheduler(s);tick(c,s,.02)
    s.tactics['skills']['combat_art'].update(instance=81,activation=5,charge=2)
    tick(c,s,.04)
    assert c.actions_count['skill_confirmed']==0


def test_short_skill_confirmed_by_persistent_activation_counter():
    s=state();art=s.tactics['skills']['combat_art'];art.update(instance=80,activation=4)
    c=scheduler(s);tick(c,s,.02)
    art['activation']=5
    tick(c,s,.04)
    assert c.actions_count['skill_confirmed']==1


def test_unknown_or_other_weapon_does_not_use_retained_tang_branches():
    s=state();s.tactics['weapon']='Weapon_Hun_Unknown'
    assert choose_plan(s).sequence==('light','light','heavy')


def test_old_world_actor_is_excluded():
    t=TacticalReader.__new__(TacticalReader)
    t.r=SimpleNamespace(objects=SimpleNamespace(valid_object=lambda _:True))
    t.actor_world=lambda _:11
    assert t.enemy(123,(0,0,0),12) is None


def test_partial_tactical_array_is_rejected():
    import struct
    h=struct.pack('<Qii',1000,2,2)
    t=TacticalReader.__new__(TacticalReader)
    t.m=SimpleNamespace(read=lambda addr,size:h if addr==500 else b'12345678')
    assert t.array(500,8)==[]


def test_skill_key_cannot_collide_with_defense():
    with pytest.raises(ValueError):CombatSettings(skill_key='Shift').validate()
