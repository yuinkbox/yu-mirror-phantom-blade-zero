from types import SimpleNamespace
from unittest.mock import patch
import struct
import pytest
from combat import CombatSettings,CombatScheduler
from test_combo_intelligence import state
from combo_planner import choose_combo
from move_runtime import move_path,binding_unlocked
from weapon_switch import WeaponSwitch
from windows_io import InputSink
from test_input import Window


def equipped():
    s=state();s.shachi=90
    s.tactics['owned_tags'].append('CharacterState.Armed')
    s.tactics['weapon_inventory']=dict(index=0,config=101,slots=[dict(index=0,config=101,cooldown=15),dict(index=1,config=102,cooldown=15)])
    return s


def test_low_resource_switch_confirmed_once_without_oscillation():
    s=equipped();w=WeaponSwitch();w.observe(0,s)
    assert w.request(0,s,CombatSettings())==('tap','1')
    s.tactics['weapon_inventory'].update(index=1,config=102)
    w.observe(.3,s);assert w.confirmed==1 and w.pending is None
    w.observe(30,s);assert w.request(30,s,CombatSettings()) is None
    s.shachi=250;w.observe(31,s);s.shachi=90;w.observe(32,s)
    assert w.request(32,s,CombatSettings())==('tap','1')


def test_failed_switch_retries_only_after_weapon_cooldown():
    s=equipped();w=WeaponSwitch();w.observe(0,s);w.request(0,s,CombatSettings());w.observe(2,s)
    assert w.failed==1 and w.confirmed==0
    assert w.request(2,s,CombatSettings()) is None
    assert w.request(16,s,CombatSettings())==('tap','1')


def test_switch_when_heavy_cost_exceeds_remaining_shachi_above_30_percent():
    s=equipped();s.shachi=140
    for p,m in s.tactics['moves'].items():
        if p.endswith('B'):m['cost']=152
    w=WeaponSwitch();w.observe(0,s)
    assert w.request(0,s,CombatSettings())==('tap','1')


@pytest.mark.parametrize('condition',['single','air','busy','range','safe_resource','no_enemy','disabled','threat'])
def test_switch_requires_useful_safe_opportunity(condition):
    s=equipped();c=CombatSettings();blocked=False
    if condition=='single':s.tactics['weapon_inventory']['slots'].pop()
    if condition=='air':s.tactics['owned_tags'].remove('CharacterState.Space.Ground')
    if condition=='busy':s.tactics['phase']='CharacterState.Action.Phase.Act'
    if condition=='range':s.target_distance=10
    if condition=='safe_resource':s.shachi=200
    if condition=='no_enemy':s.tactics['enemies']=[]
    if condition=='disabled':c.switch_enabled=False
    if condition=='threat':blocked=True
    w=WeaponSwitch();w.observe(0,s)
    assert w.request(0,s,c,blocked) is None


def test_manual_weapon_change_respects_cooldown_and_low_resource_latch():
    s=equipped();w=WeaponSwitch();w.observe(0,s)
    s.tactics['weapon_inventory'].update(index=1,config=102);w.observe(1,s)
    assert w.request(2,s,CombatSettings()) is None and w.confirmed==0


def test_scheduler_switch_preempts_new_chain_but_never_defense():
    s=equipped();c=CombatScheduler(CombatSettings());c.arm(0,s);s.game_time+=.1
    assert c.tick(.1,True,s)==[('tap','1')]
    s.game_time+=.1;s.threats=[dict(id=4,hit_time=s.game_time+.05,dodge=False,attack_type='blue',source=123)]
    assert c.tick(.2,True,s)==[('down','鼠标右键')]


def test_new_weapon_uses_own_four_hit_chain_and_resource_costs():
    s=state();s.tactics['weapon']='Weapon_Hun_Long'
    s.tactics['moves']={p:m for p,m in s.tactics['moves'].items() if p in ('A','AA','AAA','AAAA','B','BB','BBB','AB','AAB','AAAB')}
    for p,m in s.tactics['moves'].items():
        if p.endswith('B'):m['cost']=152
    history=[]
    for _ in range(8):history.append(choose_combo(s,history).path)
    assert {'AAAA','AAAB'}<=set(history) and all(p in s.tactics['moves'] for p in history)


def test_move_names_do_not_misclassify_air_as_ground_or_lose_long_new_finisher():
    assert move_path('Default__GA_Hun_Air_Tang_AA_C',['Ability.Action.Hun.Light.AA'],'Tang')==(None,None)
    assert move_path('Default__GA_Hun_Long_BBB_New_C',[],'Long')==('BBB',None)
    assert move_path('Default__GA_Hun_Chain_AAB_C',[],'Chain')==('AAB',None)


def test_upgrade_cannot_be_bypassed_by_unrelated_combo_entry():
    trigger='Ability.Action.Hun.Light.Long.AAAAA';upgrade='CharacterState.WeaponUpgrade.Long.BBB'
    rules=[dict(trigger=trigger,required=['Ability.Action.Hun.Light.AAAA',upgrade],ignored=[]),
           dict(trigger=trigger,required=['Ability.Action.Hun.Heavy.Long.BD'],ignored=[])]
    assert not binding_unlocked([trigger],rules,[],['Ability.Action.Hun.Light.AAAA'])
    assert binding_unlocked([trigger],rules,[upgrade],['Ability.Action.Hun.Light.AAAA'])


def test_early_blue_warning_releases_old_guard_and_reserves_input_window():
    s=state();c=CombatScheduler(CombatSettings());c.arm(0,s);c.guard_down=True;c.guard_until=102
    s.game_time=100.1;s.threats=[dict(id=4,hit_time=100.7,dodge=False,attack_type='blue')]
    assert c.tick(.1,True,s)==[('up','鼠标右键')]
    for at in (.2,.3,.4,.5):
        s.game_time=100+at;assert c.tick(at,True,s)==[]
    s.game_time=100.6;assert c.tick(.6,True,s)==[('down','鼠标右键')]


def test_native_perfect_count_requires_same_threat_and_native_tag():
    s=state();c=CombatScheduler(CombatSettings());c.arm(0,s);s.game_time=100.1
    s.threats=[dict(id=4,hit_time=100.2,dodge=False,attack_type='blue',source=123)]
    c.tick(.1,True,s);assert c.actions_count['native_perfect']==0
    s.diagnostics=dict(native_defense_result=dict(source=456,hit_time=100.2,result='Deflect.Result.Perfect',defender=s.player))
    s.game_time=100.2;c.tick(.2,True,s);assert c.actions_count['native_perfect']==0
    s.diagnostics['native_defense_result']['source']=123
    s.game_time=100.3;c.tick(.3,True,s);assert c.actions_count['native_perfect']==1
    s.game_time=100.4;c.tick(.4,True,s);assert c.actions_count['native_perfect']==1


def test_native_result_uses_reflected_struct_not_array_header():
    from runtime import RuntimeReader
    r=RuntimeReader.__new__(RuntimeReader)
    r.objects=SimpleNamespace(resolve=lambda b:123 if b==b'12345678' else 0)
    r.projectiles=SimpleNamespace(ref=SimpleNamespace(name=lambda b:'Deflect.Result.Perfect'))
    raw=bytearray(88);raw[:8]=b'12345678';struct.pack_into('<Q',raw,32,100);struct.pack_into('<f',raw,68,15.5)
    assert r.decode_defense_result(raw)==dict(source=123,defender=100,hit_time=15.5,result='Deflect.Result.Perfect')


def test_async_tap_allows_defense_sampling_and_releases_when_due():
    sink=InputSink();events=[];window=Window()
    with patch.object(sink,'_event',lambda k,d:events.append((k,d))),patch('windows_io.is_down',return_value=False),patch('windows_io.time.monotonic',return_value=10):
        sink.dispatch(('tap','F'),window,nonblocking=True)
        assert events==[('F',True)]
        sink.dispatch(('down','鼠标右键'),window)
        assert events[-1]==('鼠标右键',True)
        with patch('windows_io.time.monotonic',return_value=10.06):sink.pump(window)
        assert events[-1]==('F',False) and 'F' not in sink.held
        sink.pump(Window(False));assert events[-1]==('鼠标右键',False) and not sink.held


def test_async_tap_f10_releases_before_deadline():
    sink=InputSink();events=[];window=Window()
    with patch.object(sink,'_event',lambda k,d:events.append((k,d))),patch('windows_io.is_down',return_value=False):
        sink.dispatch(('tap','F'),window,nonblocking=True);sink.pump(window,lambda:True)
    assert events==[('F',True),('F',False)] and not sink.deadlines
