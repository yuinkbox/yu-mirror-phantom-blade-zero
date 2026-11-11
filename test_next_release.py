"""Scenarios for isolation, native lock acquisition and resource-aware scheduling."""
from dataclasses import replace
from types import SimpleNamespace
import queue
import pytest
from combat import CombatSettings, CombatScheduler
from combo_planner import choose_combo
from combo_controller import ComboController
from target_control import TargetControl
from weapon_policy import policy_for, affordable_health, combat_art_ready, next_move_safe
from coexistence import select_peers, ObserverSink
from test_combo_intelligence import state, planned, at, acknowledge


def unlocked():
    s=state()
    s.target=0
    s.target_distance=None
    s.tactics['owned_tags'].append('CharacterState.Armed')
    return s


def test_peer_filter_excludes_only_own_bootloader_tree():
    rows=[dict(pid=1,parent=0,name='explorer.exe'),
          dict(pid=2,parent=1,name='PBZ战斗助手-开发版.exe'),
          dict(pid=3,parent=2,name='PBZ战斗助手-开发版.exe'),
          dict(pid=4,parent=1,name='PBZ战斗助手.exe'),
          dict(pid=5,parent=4,name='PBZ战斗助手.exe')]
    assert [p['pid'] for p in select_peers(rows,3)]==[4,5]


def test_observer_sink_has_no_injection_path():
    sink=ObserverSink()
    sink.pump(None);sink.release_all();sink.release_keys(('F',))
    with pytest.raises(RuntimeError):sink.dispatch(('tap','F'),None)


class FakeHotkeys:
    def __init__(self,*args):self.closed=False
    def close(self):self.closed=True


def worker(monkeypatch,tmp_path,observe=False):
    import runtime_app as app
    monkeypatch.setattr(app,'DATA',tmp_path)
    monkeypatch.setattr(app,'Hotkeys',FakeHotkeys)
    return app,app.Worker(CombatSettings(),queue.Queue(),observe=observe)


def test_settings_preserve_configured_attack_cadence(monkeypatch, tmp_path):
    import mod_app as app
    monkeypatch.setattr(app, 'DATA', tmp_path)
    (tmp_path / 'settings.json').write_text('{"attack_period":0.35}', 'utf-8')
    assert app.load_settings()['attack_period'] == 0.35
    (tmp_path / 'settings.json').write_text('{"attack_period":0.46}', 'utf-8')
    assert app.load_settings()['attack_period'] == 0.46
    (tmp_path / 'settings.json').write_text('{"attack_period":0.62}', 'utf-8')
    assert app.load_settings()['attack_period'] == 0.62


def test_existing_stable_version_never_registers_hotkeys_or_arms(monkeypatch,tmp_path):
    app,w=worker(monkeypatch,tmp_path)
    monkeypatch.setattr(app,'peer_processes',lambda:[dict(pid=42,name='PBZ战斗助手.exe')])
    def forbidden(*args):pytest.fail('must not register hotkeys while a peer exists')
    monkeypatch.setattr(app,'Hotkeys',forbidden)
    assert w.sync_control(0,None) is None
    w.toggle()
    assert not w.control_available and not w.session.enabled and not w.scheduler.armed


def test_peer_exit_allows_start_but_never_automatically_arms(monkeypatch,tmp_path):
    app,w=worker(monkeypatch,tmp_path)
    peers=[dict(pid=42)]
    monkeypatch.setattr(app,'peer_processes',lambda:peers)
    assert w.sync_control(0,None) is None
    peers.clear()
    keys=w.sync_control(1.1,None)
    assert isinstance(keys,FakeHotkeys) and w.control_available
    assert not w.session.enabled and not w.scheduler.armed


def test_new_peer_releases_new_versions_own_inputs_and_clears_start(monkeypatch,tmp_path):
    app,w=worker(monkeypatch,tmp_path)
    monkeypatch.setattr(app,'peer_processes',lambda:[dict(pid=42)])
    released=[]
    w.sink=SimpleNamespace(release_all=lambda:released.append(True))
    w.control_available=True;w.session.enabled=True;w.scheduler.armed=True;w.start_at=5
    keys=FakeHotkeys()
    assert w.sync_control(0,keys) is None
    assert keys.closed and released and not w.scheduler.armed and not w.session.enabled and w.start_at is None


def test_process_enumeration_failure_keeps_control_disabled(monkeypatch,tmp_path):
    app,w=worker(monkeypatch,tmp_path)
    def fail():raise OSError('snapshot failed')
    monkeypatch.setattr(app,'peer_processes',fail)
    assert w.sync_control(0,None) is None and not w.control_available


def test_observer_mode_never_claims_hotkeys_even_without_peer(monkeypatch,tmp_path):
    app,w=worker(monkeypatch,tmp_path,True)
    def forbidden(*args):pytest.fail('observer attempted to claim controls')
    monkeypatch.setattr(app,'Hotkeys',forbidden)
    for now in (0,2,8):assert w.sync_control(now,None) is None
    assert isinstance(w.sink,ObserverSink) and not w.control_available


def test_hotkey_conflict_stays_read_only(monkeypatch,tmp_path):
    app,w=worker(monkeypatch,tmp_path)
    monkeypatch.setattr(app,'peer_processes',lambda:[])
    def unavailable(*args):raise OSError('busy')
    monkeypatch.setattr(app,'Hotkeys',unavailable)
    assert w.sync_control(0,None) is None and not w.control_available


def test_lock_requires_native_confirmation_then_preserves_target():
    s=unlocked();t=TargetControl();t.observe(0,s)
    assert t.request(0,s,CombatSettings())==('tap','鼠标中键')
    assert t.request(.1,s,CombatSettings()) is None
    s.target=10;t.observe(.2,s)
    assert t.confirmed==1 and t.pending is None
    assert t.request(20,s,CombatSettings()) is None


def test_lock_failure_has_cooldown_and_bounded_retries():
    s=unlocked();t=TargetControl();t.observe(0,s)
    assert t.request(0,s,CombatSettings())
    t.observe(1,s)
    assert t.failed==1 and t.request(2,s,CombatSettings()) is None
    assert t.request(4,s,CombatSettings())
    t.observe(5,s)
    assert t.failed==2 and t.request(10,s,CombatSettings()) is None
    assert t.request(11,s,CombatSettings())


def test_dead_target_snapshot_delay_does_not_disable_transfer():
    s=unlocked();t=TargetControl();s.target=10;t.observe(0,s)
    s.target=0;t.observe(.1,s)
    assert t.request(.1,s,CombatSettings()) is None
    s.tactics['enemies']=[dict(s.tactics['enemies'][0],actor=11)]
    t.observe(.8,s)
    assert t.request(.8,s,CombatSettings())==('tap','鼠标中键')


def test_live_target_unlock_is_respected_until_encounter_ends():
    s=unlocked();t=TargetControl();s.target=10;t.observe(0,s)
    s.target=0;t.manual(.1);t.observe(.1,s);t.observe(.8,s)
    assert t.request(9,s,CombatSettings()) is None
    enemies=s.tactics['enemies'];s.tactics['enemies']=[]
    t.request(10,s,CombatSettings());t.request(14,s,CombatSettings())
    s.tactics['enemies']=enemies
    assert t.request(15,s,CombatSettings())


def test_natural_target_loss_retries_lock_after_backoff():
    s=state();t=TargetControl();t.observe(0,s)
    s.target=0;t.observe(.1,s);t.observe(.8,s)
    assert not t.manual_unlock
    assert t.request(1,s,CombatSettings())==('tap','鼠标中键')


def test_physical_unlock_suppresses_relock_even_if_enemy_snapshot_is_empty():
    s=unlocked();t=TargetControl();t.observe(0,s);t.manual(.1)
    assert t.request(10,s,CombatSettings()) is None


@pytest.mark.parametrize('condition',['air','menu','far','other_floor','no_enemy','disabled','threat'])
def test_acquisition_requires_safe_nearby_ground_target(condition):
    s=unlocked();t=TargetControl();cfg=CombatSettings();blocked=False
    if condition=='air':s.tactics['owned_tags'].remove('CharacterState.Space.Ground')
    if condition=='menu':s.input_blocked=True
    if condition=='busy':s.tactics['phase']='CharacterState.Action.Phase.Act'
    if condition=='far':s.tactics['enemies'][0]['distance']=25
    if condition=='other_floor':s.tactics['enemies'][0]['height_delta']=450
    if condition=='no_enemy':s.tactics['enemies']=[]
    if condition=='disabled':cfg.autolock_enabled=False
    if condition=='threat':blocked=True
    t.observe(0,s)
    assert t.request(0,s,cfg,blocked) is None


def test_lock_can_acquire_while_character_is_in_an_attack_phase():
    s=unlocked();s.tactics['phase']='CharacterState.Action.Phase.Act'
    t=TargetControl();t.observe(0,s)
    assert t.request(0,s,CombatSettings())==('tap','鼠标中键')


def test_scheduler_acquires_before_attack_and_defense_preempts_lock():
    s=unlocked();c=CombatScheduler(CombatSettings());c.arm(0,s);c.next_attack=0
    s.game_time+=.1
    assert c.tick(.1,True,s)==[('tap','鼠标中键')]
    s.game_time+=.1;s.threats=[dict(id=1,hit_time=s.game_time+.05,dodge=True)]
    assert c.tick(.2,True,s)==[('tap','Shift')]
    assert c.actions_count['attack']==0


def test_close_native_target_gets_light_attack_if_move_model_is_unavailable():
    s=state();s.tactics['move_model_ready']=False;s.tactics['moves']={};s.tactics['move_events']={}
    c=CombatScheduler(CombatSettings());c.arm(0,s);c.next_attack=0;c.last_attack_input=-2
    assert c.tick(.01,True,s)==[('tap','鼠标左键')]
    assert c.status=='已锁定近身目标：恢复基础轻击'


def test_close_native_target_gets_light_attack_if_tactical_sample_is_missing():
    s=state();s.tactics={}
    c=CombatScheduler(CombatSettings());c.arm(0,s);c.next_attack=0;c.last_attack_input=-2
    assert c.tick(.01,True,s)==[('tap','鼠标左键')]
    assert c.status=='近身目标战况读取中：恢复基础轻击'


def test_close_target_recovers_after_unconfirmed_combo_input_stalls():
    s=state();c=CombatScheduler(CombatSettings());c.arm(0,s);c.next_attack=0
    first=c.tick(.01,True,s)
    assert first==[('tap','鼠标左键')]
    s.tactics['move_model_ready']=False
    recovered=[]
    for now in (.25,.5,.75,1.,1.25,1.5,1.75,2.):
        s.game_time+=.25
        recovered=c.tick(now,True,s)
        if recovered:break
    assert recovered==[('tap','鼠标左键')]
    assert c.status=='已锁定近身目标：恢复基础轻击'


def test_worker_discovers_running_install_when_saved_path_is_stale(monkeypatch,tmp_path):
    import runtime_app as app
    import queue
    from types import SimpleNamespace
    monkeypatch.setattr(app,'DATA',tmp_path)
    game=tmp_path/'steam'/'PBZ'/'platform'/'steam'/'PBZ.exe'
    game.parent.mkdir(parents=True);game.write_bytes(b'build')
    window=SimpleNamespace(path=str(game),pid=lambda:42)
    class FakeRuntime:
        def __init__(self,pid,path):self.memory=SimpleNamespace(pid=pid)
    monkeypatch.setattr(app,'find_game',lambda _:None)
    monkeypatch.setattr(app,'find_game_by_name',lambda _:window)
    monkeypatch.setattr(app,'verify_build',lambda _:True)
    monkeypatch.setattr(app,'RuntimeReader',FakeRuntime)
    worker=app.Worker(app.CombatSettings(),queue.Queue(),path=str(tmp_path/'old'/'PBZ.exe'))
    worker.connect(1)
    assert worker.path==str(game.resolve())
    assert worker.window is window and worker.runtime.memory.pid==42
    assert (tmp_path/'game-path.txt').read_text('utf-8')==worker.path


def test_worker_reports_running_game_with_unsupported_build(monkeypatch,tmp_path):
    import runtime_app as app
    import queue
    from types import SimpleNamespace
    monkeypatch.setattr(app,'DATA',tmp_path)
    game=tmp_path/'PBZ.exe';game.write_bytes(b'other build')
    monkeypatch.setattr(app,'find_game',lambda _:None)
    monkeypatch.setattr(app,'find_game_by_name',lambda _:SimpleNamespace(path=str(game),pid=lambda:42))
    monkeypatch.setattr(app,'verify_build',lambda _:False)
    worker=app.Worker(app.CombatSettings(),queue.Queue(),path=str(tmp_path/'old'/'PBZ.exe'))
    worker.connect(1)
    assert '版本尚未适配' in worker.status and worker.runtime is None


def test_long_windup_does_not_start_before_known_incoming_attack():
    s=state();s.threats=[dict(id=1,hit_time=s.game_time+1.0,dodge=False)]
    assert next_move_safe(dict(combo_open=.3),s)
    assert not next_move_safe(dict(combo_open=1.4),s)
    s.tactics['moves']={p:m for p,m in s.tactics['moves'].items() if p=='B'}
    s.tactics['moves']['B']['combo_open']=1.4
    assert choose_combo(s) is None


def test_threat_between_combo_steps_prevents_long_heavy_followup():
    d,s=planned('ABB');at(d,s,0);acknowledge(d,s,'A',.02,'Combo')
    s.threats=[dict(id=2,hit_time=101.2,dodge=False)]
    assert at(d,s,.2) is None and d.pending is None


def test_explicit_health_cost_preserves_floor_for_whole_chain():
    s=state();s.hp=600;p=policy_for(s.tactics)
    assert affordable_health([dict(path='B',health_cost=100)],s,p)
    assert not affordable_health([dict(path='B',health_cost=100),dict(path='BB',health_cost=150)],s,p)


@pytest.mark.parametrize('cost',[None,float('nan'),float('inf'),-1])
def test_unreadable_health_cost_is_never_spent_even_at_full_hp(cost):
    s=state()
    assert not affordable_health([dict(path='B',health_cost=cost)],s,policy_for(s.tactics))


def test_unknown_family_keeps_ordinary_attacks_and_suppresses_unverified_skill():
    s=state();s.tactics['weapon']='Weapon_Hun_Unknown';s.hp=500
    assert 'B' not in choose_combo(s).path
    s.tactics['skills']['combat_art']=dict(cost=1,charge=3,unlocked=True,cooldown_known=True,active=False)
    s.hp=850
    assert not combat_art_ready(s)
    assert choose_combo(s) is not None


def test_known_cost_is_not_ignored_when_another_step_is_unknown():
    s=state();s.tactics['weapon']='Weapon_Hun_Unknown'
    assert not affordable_health([dict(path='A',health_cost=500),dict(path='AB')],s,policy_for(s.tactics))


def test_skill_blocked_tags_and_health_cost_take_precedence_over_charge():
    s=state();art=dict(cost=1,charge=3,unlocked=True,cooldown_known=True,active=False)
    s.tactics['skills']['combat_art']=art
    assert combat_art_ready(s)
    art['health_cost']=500
    assert not combat_art_ready(s)
    art['health_cost']=0;art['blocked_tags']=['CharacterState.Space.Ground']
    assert not combat_art_ready(s)


def test_unknown_numeric_cost_type_never_gets_decoded_as_free_health():
    from move_runtime import MoveReader
    m=MoveReader.__new__(MoveReader)
    m.m=SimpleNamespace(u64=lambda _:99)
    m.ref=SimpleNamespace(fields=lambda _:{'Health Cost':(100,4,1)})
    m.number=lambda *_:pytest.fail('unverified health field must not be decoded')
    assert m.health_budget(1,850)['health_cost'] is None


def test_defense_release_does_not_release_manual_or_untracked_keys(monkeypatch):
    from windows_io import InputSink
    sink=InputSink();events=[]
    monkeypatch.setattr(sink,'_event',lambda key,down:events.append((key,down)))
    sink.held={'F','鼠标右键'};sink.deadlines={'F':1}
    sink.release_keys(('F','Q','鼠标左键'))
    assert events==[('F',False)] and sink.held=={'鼠标右键'} and not sink.deadlines


def test_new_settings_do_not_read_stable_data_folder():
    from pathlib import Path
    import runtime_app as app
    assert app.DATA.name=='data-next' and app.SETTINGS.parent==app.DATA
    assert app.ROOT == Path(app.__file__).resolve().parent
    assert app.DATA.parent == app.ROOT
