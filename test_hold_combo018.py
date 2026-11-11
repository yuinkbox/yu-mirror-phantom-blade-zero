"""Hold-to-combo behavior through the actual Lua and desktop file protocol."""
import queue
from types import SimpleNamespace

import pytest

from hold_combo import COMBO_KEYS, HoldGesture, combo_key
from mod_bridge import ModBridge, parse_ini
from sim_mod_lua import Actor, Frame, WindowedFight
from test_mod_bridge import install
from test_mod_combat01710 import make_fight, presses, tmp_path


@pytest.mark.parametrize('mode', ['released', 'missing', 'wrong_session', 'old_packet', 'background'])
def test_no_key_never_starts_any_offense_but_mod_runs(tmp_path, mode):
    sim, boss = make_fight(tmp_path)
    sim.actor.attributes._values.update(UltimateChargeCount=2, SpiritShadow=300)
    boss.attributes._values['Shield'] = 100
    sim.assist_hold = False if mode == 'released' else None
    if mode == 'wrong_session':
        sim.write_assist(hold=1, session='previous-desktop')
    elif mode == 'old_packet':
        sim.write_assist(hold=1, stamp=sim.now_epoch() - 10)
    elif mode == 'background':
        sim.write_assist(hold=1, focused=0)
    sim.step(count=180)
    assert not [x for x in sim.timed_injections if x[1] > 0], sim.read_status()
    assert sim.read_status()['running'] == '1'
    assert sim.read_status()['error'] == ''


def test_release_stops_followups_even_with_a_running_move_tag(tmp_path):
    sim, _ = make_fight(tmp_path)
    sim.combo_game = WindowedFight(sim, cost=0)
    sim.step(count=70)
    assert presses(sim)
    assert sim.combo_game.path
    sim.assist_hold = False
    sim.step(count=3)  # transport polling: at most two simulation frames
    boundary = sim.clock_value
    before = len(presses(sim))
    sim.step(count=180)
    assert len(presses(sim)) == before
    assert sim.read_status()['combo_gate'] == 'waiting_key'
    sim.assist_hold = True
    sim.step(count=100)
    assert any(at > boundary for _, at in presses(sim))
    assert sim.read_status()['error'] == ''


@pytest.mark.parametrize('failure', ['stale', 'removed', 'malformed', 'rollback', 'future'])
def test_transport_failure_releases_active_attack(tmp_path, failure):
    sim, _ = make_fight(tmp_path)
    sim.step(count=35)
    assert presses(sim)
    sim.assist_hold = None
    path = sim.mod_dir / 'assist.ini'
    if failure == 'removed':
        path.unlink()
    elif failure == 'malformed':
        path.write_text('hold=1\n', encoding='utf-8')
    elif failure == 'rollback':
        sim.write_assist(hold=1, seq=1)
    elif failure == 'future':
        sim.write_assist(hold=1, stamp=sim.now_epoch() + 100)
    sim.step(count=18)  # local key lease is only 250 ms
    before = len(presses(sim))
    sim.step(count=180)
    assert len(presses(sim)) == before
    assert sim.read_status()['combo_gate'] in ('input_expired', 'input_missing')
    assert sim.read_status()['error'] == ''


@pytest.mark.parametrize('target_kind', ['none', 'distant', 'mounted'])
def test_hold_attacks_without_target_dependency_and_never_moves_or_locks(tmp_path, target_kind):
    sim, boss = make_fight(tmp_path, distance=1200 if target_kind == 'distant' else 280)
    if target_kind == 'none':
        sim.extra_actors, sim.faction_components = [], []
    elif target_kind == 'mounted':
        boss._class = 'BP_Monster_Horse_JingYe_C'
    sim.step(count=300)
    assert len(presses(sim)) >= 4
    assert any(name == 'IA_AttackH_Press' for name, _ in presses(sim))
    assert not any(abs(x) + abs(y) > 0 for x, y in sim.move_inputs)
    assert not presses(sim, 'IA_LockTarget')
    assert sim.read_status()['error'] == ''


@pytest.mark.parametrize('still_held', [True, False])
def test_defense_preempts_and_only_resumes_with_key_down(tmp_path, still_held):
    sim, _ = make_fight(tmp_path)
    sim.step(count=40)
    assert presses(sim)
    sim.deflect.ReceiveFrames.frames[1] = Frame(sim.game_time + .01, is_dodge=True)
    sim.step(count=3)
    assert presses(sim, 'IA_Dodge')
    before = len(presses(sim))
    sim.assist_hold = still_held
    sim.deflect.ReceiveFrames.frames.clear()
    sim.step(count=200)
    assert (len(presses(sim)) > before) == still_held
    assert sim.read_status()['error'] == ''


@pytest.mark.parametrize('item,hp', [('IA_UseBottomItem', 200), ('IA_UseTopItem', 550)])
def test_medicine_runs_without_holding_combo_key(tmp_path, item, hp):
    sim, _ = make_fight(tmp_path)
    sim.assist_hold = False
    sim.actor.attributes._values['Health'] = hp
    sim.step(count=120)
    assert presses(sim, item), sim.read_status()
    assert not presses(sim)
    assert sim.read_status()['error'] == ''


def test_defense_works_even_without_key_state_file(tmp_path):
    sim, _ = make_fight(tmp_path)
    sim.assist_hold = None
    sim.deflect.ReceiveFrames.frames[1] = Frame(sim.game_time + .01, is_dodge=True)
    sim.step(count=5)
    assert presses(sim, 'IA_Dodge') and not presses(sim)




def test_hold_uses_move_windows_instead_of_fast_key_spam(tmp_path):
    sim, _ = make_fight(tmp_path)
    sim.combo_game = WindowedFight(sim, cost=0)
    sim.step(count=1800)
    assert sim.combo_game.completed >= 3
    assert sim.combo_game.rejected / max(1, len(sim.combo_game.presses)) < .15
    assert sim.read_status()['combo_mode'] == 'tag'


def test_gesture_requires_release_after_focus_or_key_change():
    gesture = HoldGesture()
    def sample(down, focused=True, key='mouse4', enabled=True):
        return gesture.sample(enabled=enabled, focused=focused, key=key, down=down)
    assert not sample(True)
    assert not sample(False)
    assert sample(True)
    assert not sample(True, focused=False)
    assert not sample(True)
    assert not sample(False)
    assert sample(True)
    assert not sample(True, key='f6')
    assert not sample(False, key='f6')
    assert sample(True, key='f6')
    assert not sample(True, key='f6', enabled=False)
    assert not sample(True, key='f6')


def test_bridge_has_session_and_monotonic_key_sequence(tmp_path):
    exe, mod = install(tmp_path)
    bridge = ModBridge(exe)
    bridge.write_control(True, now=100)
    control = parse_ini((mod / 'control.ini').read_text('utf-8'))
    assert control['combat_mode'] == 'hold'
    bridge.write_assist(True, now=100)
    first = parse_ini((mod / 'assist.ini').read_text('utf-8'))
    bridge.write_assist(True, now=100, focused=False)
    second = parse_ini((mod / 'assist.ini').read_text('utf-8'))
    assert second['hold'] == '0'
    assert int(second['seq']) > int(first['seq'])
    assert first['session'] == control['combo_session']
    assert ModBridge(exe).assist_session != bridge.assist_session


def test_worker_publishes_selected_key_only_for_game_foreground(monkeypatch, tmp_path):
    import mod_app
    exe, mod = install(tmp_path)
    monkeypatch.setattr(mod_app.UE4SSPackage, 'discover', lambda: None)
    worker = mod_app.ModWorker(queue.Queue(), str(exe), dict(mod_app.DEFAULT_SETTINGS, combo_key='f6'))
    focused, down, keys = [True], [False], []
    worker.window = SimpleNamespace(foreground=lambda: focused[0])
    def read_key(vk):
        keys.append(vk)
        return down[0]
    monkeypatch.setattr(mod_app, 'is_down', read_key)
    worker.run_requested = True
    worker.assist_tick(100)
    down[0] = True
    worker.assist_tick(100.02)
    assert parse_ini((mod / 'assist.ini').read_text('utf-8'))['hold'] == '1'
    assert set(keys) == {COMBO_KEYS['f6'][1]}
    focused[0] = False
    worker.assist_tick(100.04)
    assert parse_ini((mod / 'assist.ini').read_text('utf-8'))['hold'] == '0'
    assert worker.run_requested  # returning to game never requires another F8
    focused[0] = True
    worker.assist_tick(100.06)
    assert parse_ini((mod / 'assist.ini').read_text('utf-8'))['hold'] == '0'


def test_old_full_auto_mod_cannot_be_started_by_new_panel(monkeypatch, tmp_path):
    import mod_app
    exe, _ = install(tmp_path)
    monkeypatch.setattr(mod_app.UE4SSPackage, 'discover', lambda: None)
    worker = mod_app.ModWorker(queue.Queue(), str(exe))
    worker.control_available = True
    worker.bridge.inspect = lambda: dict(status='connected', loaded=True, mod_version='0.17.20')
    with pytest.raises(RuntimeError, match=mod_app.VERSION):
        worker.request(True)
    assert not worker.run_requested


def test_idle_panel_does_not_touch_another_instances_key_file(monkeypatch, tmp_path):
    import mod_app
    exe, mod = install(tmp_path)
    monkeypatch.setattr(mod_app.UE4SSPackage, 'discover', lambda: None)
    (mod / 'assist.ini').write_text('another instance', encoding='utf-8')
    worker = mod_app.ModWorker(queue.Queue(), str(exe))
    worker.assist_tick(100)
    worker.release_combo()
    assert (mod / 'assist.ini').read_text('utf-8') == 'another instance'


@pytest.mark.parametrize('override', [{'combat_mode': 'auto'}, {'combo_session': ''},
                                     {'attack_enabled': 0}, {'combo_enabled': 0}])
def test_invalid_mode_or_disabled_combo_never_emits_offense(tmp_path, override):
    sim, _ = make_fight(tmp_path)
    sim.write_control(**override)
    sim.step(count=180)
    assert not presses(sim)


def test_panel_stop_publishes_key_up_and_stop_request(monkeypatch, tmp_path):
    import mod_app
    exe, mod = install(tmp_path)
    monkeypatch.setattr(mod_app.UE4SSPackage, 'discover', lambda: None)
    worker = mod_app.ModWorker(queue.Queue(), str(exe))
    worker.run_requested = True
    worker.bridge.write_assist(True)
    worker.request(False)
    assert not worker.run_requested
    assert parse_ini((mod / 'assist.ini').read_text('utf-8'))['hold'] == '0'
    assert parse_ini((mod / 'control.ini').read_text('utf-8'))['run'] == '0'


@pytest.mark.parametrize('bad_key', ['mouse1', 'R', 'f8', 'f9', None, [], 42])
def test_reserved_or_invalid_key_migrates_to_side_button(bad_key):
    assert combo_key(bad_key) == 'mouse4'
