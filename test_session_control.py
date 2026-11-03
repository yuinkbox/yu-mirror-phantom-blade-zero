from types import SimpleNamespace
from session_control import SessionControl


def state(t=0,**kw):
    v=dict(valid=True,hp=850,player=1,component=2,world=3,game_time=100+t,reason='数据失效',input_blocked=False)
    v.update(kw)
    return SimpleNamespace(**v)


def running():
    s=SessionControl();s.start(0,state())
    assert not s.update(.01,True,state(.01))
    assert s.update(.4,True,state(.4))
    return s


def test_focus_loss_is_temporary_and_returns_automatically():
    s=running()
    assert not s.update(.5,False,state(.5)) and s.enabled
    assert not s.update(1,True,state(1))
    assert s.update(1.4,True,state(1.4))


def test_unpaused_menu_uses_native_input_block_to_prevent_resuming():
    s=running()
    for t in (.5,1,2,10):
        assert not s.update(t,True,state(t,input_blocked=True))
        assert s.enabled
    assert not s.update(11,True,state(11))
    assert s.update(11.4,True,state(11.4))


def test_frozen_game_resumes_only_after_time_advances_stably():
    s=running()
    assert not s.update(.8,True,state(.4))
    assert not s.update(5,True,state(.4))
    assert not s.update(5.1,True,state(.5))
    assert s.update(5.5,True,state(.9))


def test_manual_stop_while_suspended_prevents_automatic_resume():
    s=running();s.update(.5,False,state(.5));s.stop('F10 已停止')
    assert not s.update(2,True,state(2))
    assert not s.enabled and s.reason=='F10 已停止'


def test_menu_key_suspends_before_game_updates_cursor_flag():
    s=running()
    assert not s.update(.5,True,state(.5),True)
    assert not s.update(.6,True,state(.6))
    assert not s.update(.9,True,state(.9))
    assert s.update(1.3,True,state(1.3))


def test_foreground_flapping_restarts_resume_delay():
    s=running();s.update(.5,False,state(.5));s.update(.6,True,state(.6))
    assert not s.update(.8,False,state(.8))
    assert not s.update(1,True,state(1))
    assert not s.update(1.2,True,state(1.2))
    assert s.update(1.4,True,state(1.4))


def test_dead_or_replaced_character_does_not_auto_arm():
    for kw in ({'hp':0},{'player':9},{'valid':False},{'world':9}):
        s=running()
        assert not s.update(.5,True,state(.5,**kw))
        assert not s.enabled


def test_start_in_menu_waits_without_losing_user_intent():
    s=SessionControl();s.start(0,state(input_blocked=True))
    assert not s.update(.5,True,state(.5,input_blocked=True))
    assert s.enabled
    assert not s.update(1,True,state(1))
    assert s.update(1.4,True,state(1.4))


def test_worker_releases_keys_and_retains_intent_across_focus_and_menu(monkeypatch,tmp_path):
    import queue
    import runtime_app
    from runtime import RuntimeState
    monkeypatch.setattr(runtime_app,'DATA',tmp_path)
    monkeypatch.setattr(runtime_app,'is_down',lambda _:False)
    w=runtime_app.Worker(runtime_app.CombatSettings(),queue.Queue())
    focus=[True];released=[]
    w.control_available=True  # This test exercises an exclusively acquired input session.
    w.window=SimpleNamespace(foreground=lambda:focus[0])
    w.sink=SimpleNamespace(release_all=lambda:released.append(True))
    w.state=RuntimeState(valid=True,hp=850,hp_max=850,player=1,component=2,world=3,game_time=100,
                         input_blocked=False,deflect_component=4)
    w.session.start(0,w.state)
    w.update_session(.01);w.state.game_time=100.4;w.update_session(.4)
    assert w.scheduler.armed
    focus[0]=False;w.state.game_time=100.5;w.update_session(.5)
    assert not w.scheduler.armed and w.session.enabled and released
    focus[0]=True;w.state.input_blocked=True;w.state.game_time=100.6;w.update_session(.6)
    assert not w.scheduler.armed and w.session.enabled
    w.state.input_blocked=False;w.state.game_time=101;w.update_session(1)
    w.state.game_time=101.4;w.update_session(1.4)
    assert w.scheduler.armed
    w.pause('F10 已停止');w.state.game_time=103;w.update_session(3)
    assert not w.scheduler.armed and not w.session.enabled


def test_minimized_window_keeps_existing_game_connection(monkeypatch,tmp_path):
    import queue
    import runtime_app
    monkeypatch.setattr(runtime_app,'DATA',tmp_path)
    monkeypatch.setattr(runtime_app,'find_game',lambda _:None)
    monkeypatch.setattr(runtime_app,'find_game_by_name',lambda _:None)
    w=runtime_app.Worker(runtime_app.CombatSettings(),queue.Queue())
    window=SimpleNamespace(valid=lambda:True,pid=lambda:42)
    runtime=SimpleNamespace(memory=SimpleNamespace(pid=42))
    w.window=window;w.runtime=runtime;w.session.enabled=True
    w.connect(1)
    assert w.window is window and w.runtime is runtime and w.session.enabled


def test_death_while_suspended_clears_auto_resume_status(monkeypatch,tmp_path):
    import queue
    import runtime_app
    from runtime import RuntimeState
    monkeypatch.setattr(runtime_app,'DATA',tmp_path)
    monkeypatch.setattr(runtime_app,'is_down',lambda _:False)
    w=runtime_app.Worker(runtime_app.CombatSettings(),queue.Queue())
    w.window=SimpleNamespace(foreground=lambda:False)
    w.control_available=True
    w.state=RuntimeState(valid=True,hp=850,hp_max=850,player=1,component=2,world=3,game_time=100)
    w.session.start(0,w.state)
    w.update_session(.1)
    assert w.session.enabled and '自动恢复' in w.status
    w.state.hp=0
    w.update_session(.2)
    assert not w.session.enabled and '角色死亡' in w.status
