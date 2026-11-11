from pathlib import Path
import os

import pytest

from mod_bridge import ModBridge, encode_ini, parse_ini


def install(tmp_path, enabled=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    exe = tmp_path / "PBZ.exe"
    exe.touch()
    mod = tmp_path / "ue4ss" / "Mods" / "PBZSmartCombat"
    (mod / "Scripts").mkdir(parents=True)
    (mod / "Scripts" / "main.lua").write_text("-- test mod", encoding="utf-8")
    if enabled:
        (mod / "enabled.txt").touch()
    return exe, mod


def test_protocol_round_trip_comments_and_utf8_bom():
    values = {"schema": 2, "run": 0, "combo_preset": "balanced"}
    assert parse_ini(encode_ini(values)) == {"schema": "2", "run": "0", "combo_preset": "balanced"}
    assert parse_ini("\ufeff; comment\nrun=1\n# ignored\n") == {"run": "1"}


@pytest.mark.parametrize("value", ["bad\nrun=1", "bad=value=again", "bad\rvalue"])
def test_encode_rejects_line_injection(value):
    with pytest.raises(ValueError):
        encode_ini({"run": value})


def test_inspect_requires_enabled_loader_and_fresh_matching_heartbeat(tmp_path):
    exe, mod = install(tmp_path)
    bridge = ModBridge(exe)
    (mod / "status.ini").write_text(
        "schema=2\nloaded=1\nrunning=1\nheartbeat=1000\n", encoding="utf-8")
    state = bridge.inspect(now=1002)
    assert state["status"] == "running" and state["loaded"] and state["running"]
    assert bridge.inspect(now=1005)["status"] == "stale"

    (mod / "enabled.txt").unlink()
    assert bridge.inspect(now=1002)["status"] == "disabled"


def test_inspect_rejects_old_schema_and_stale_status(tmp_path):
    exe, mod = install(tmp_path)
    bridge = ModBridge(exe)
    (mod / "status.ini").write_text(
        "schema=1\nloaded=1\nrunning=1\nheartbeat=1000\n", encoding="utf-8")
    assert bridge.inspect(now=1001)["status"] == "stale"


def test_inspect_uses_newest_complete_status_slot(tmp_path):
    exe, mod = install(tmp_path)
    bridge = ModBridge(exe)
    (mod / "status-a.ini").write_text(
        "schema=2\nloaded=1\nrunning=0\nheartbeat=1000\nhp=500\n", encoding="utf-8")
    (mod / "status-b.ini").write_text("schema=1\nloaded=1\n")
    state = bridge.inspect(now=1001)
    assert state["loaded"] and state["hp"] == "500" and state["heartbeat"] == 1000


def test_newer_incompatible_or_partial_slot_does_not_hide_complete_slot(tmp_path):
    exe, mod = install(tmp_path)
    bridge = ModBridge(exe)
    (mod / 'status-a.ini').write_text('schema=2\nloaded=1\nrunning=1\nheartbeat=1000\nhp=500\n')
    bad = mod / 'status-b.ini'
    for text in ('schema=1\nloaded=1\nrunning=1\nheartbeat=1001\nhp=200\n',
                 'schema=2\nloaded=1\nheartbeat=1001\nhp=200\n'):
        bad.write_text(text)
        state = bridge.inspect(now=1001)
        assert state['loaded'] and state['running']
        assert state['hp'] == '500'


def test_status_slots_in_same_second_use_file_mtime_to_pick_newest(tmp_path):
    exe, mod = install(tmp_path)
    first, second = mod / 'status-a.ini', mod / 'status-b.ini'
    first.write_text('schema=2\nloaded=1\nrunning=0\nheartbeat=1000\nhp=500\n')
    second.write_text('schema=2\nloaded=1\nrunning=1\nheartbeat=1000\nhp=450\n')
    os.utime(first, ns=(1_000_000_000_000, 1_000_000_000_000))
    os.utime(second, ns=(1_000_500_000_000, 1_000_500_000_000))
    state = ModBridge(exe).inspect(now=1001)
    assert state['running'] and state['hp'] == '450'


def test_bridge_exports_community_defense_and_healing_observations(tmp_path):
    exe, mod = install(tmp_path)
    values = dict(schema=2, loaded=1, running=1, heartbeat=1000,
                  defense_engine='Muspelion/PBZ-AutoParryDodge v1.1.0', defense_ready=1,
                  defense_error='', defense_parry_ready=1, defense_dodge_ready=1,
                  defense_checks_no_loss=4, defense_checks_damage=2, defense_checks_unknown=3,
                  defense_api_fail=1, defense_projectiles=8, defense_frames_late=7,
                  skill_state='ready', skill_charges=1, heal_ok=3, heal_fail=1,
                  heal_last='heal', attack_unverified=9)
    (mod / 'status-a.ini').write_text(encode_ini(values), encoding='utf-8')
    state = ModBridge(exe).inspect(now=1001)
    for key in values.keys() - {'schema', 'loaded', 'running', 'heartbeat'}:
        assert state[key] == str(values[key])


def test_write_control_is_atomic_versioned_and_maps_settings(tmp_path):
    exe, mod = install(tmp_path)
    bridge = ModBridge(exe)
    request_id = bridge.write_control(True, {
        "heal_enabled": False, "attack_enabled": True, "guard_enabled": False,
        "dodge_enabled": True, "threshold": .6,
        "attack_distance": 4.25, "combo_preset": "pressure",
        "combo_speed": 1.12, "shachi_reserve": 0.45,
    }, now=100)
    values = parse_ini((mod / "control.ini").read_text(encoding="utf-8"))
    assert len(request_id) == 32 and values["request_id"] == request_id
    assert values["schema"] == "2" and values["run"] == "1"
    assert values["lease_until"] == "104"
    assert values["heal_enabled"] == "0" and values["attack_enabled"] == "1"
    assert values["guard_enabled"] == "0" and values["dodge_enabled"] == "1"
    assert values["heal_threshold"] == "0.6" and values["attack_distance"] == "4.25"
    assert values["combo_preset"] == "pressure"
    assert values["combo_speed"] == "1.12" and values["shachi_reserve"] == "0.45"
    assert not list(mod.glob("control-*.tmp"))
    bridge.write_control(False, now=101)
    stopped = parse_ini((mod / "control.ini").read_text(encoding="utf-8"))
    assert stopped["run"] == "0" and stopped["lease_until"] == "105"
    # A desktop build that predates the combo tuning keys keeps working: the mod
    # falls back to its own defaults instead of reading an empty value.
    assert stopped["combo_speed"] == "1" and stopped["shachi_reserve"] == "0.3"


def test_write_control_requires_installed_and_loader_enabled(tmp_path):
    exe = tmp_path / "PBZ.exe"
    exe.touch()
    with pytest.raises(FileNotFoundError):
        ModBridge(exe).write_control(True, now=100)
    exe, _ = install(tmp_path / "second", enabled=False)
    with pytest.raises(RuntimeError):
        ModBridge(exe).write_control(True, now=100)


def test_mods_txt_can_enable_mod_without_touching_it(tmp_path):
    exe, mod = install(tmp_path, enabled=False)
    mods = mod.parent / "mods.txt"
    mods.write_text("; user config\nPBZSmartCombat : 1\n", encoding="utf-8")
    bridge = ModBridge(exe)
    bridge.write_control(False, now=200)
    assert bridge.inspect(now=200)["enabled"]


@pytest.mark.parametrize("legacy", ["PBZAssist", "PBZAutoCombat", "PBZAutoGuard"])
def test_legacy_combat_mod_blocks_new_controller(tmp_path, legacy):
    exe, mod = install(tmp_path)
    legacy_dir = mod.parent / legacy
    legacy_dir.mkdir()
    (legacy_dir / "enabled.txt").touch()
    bridge = ModBridge(exe)
    assert bridge.inspect(now=200)["status"] == "conflict"
    with pytest.raises(RuntimeError, match="legacy combat mod conflict"):
        bridge.write_control(True, now=200)
    bridge.write_control(False, now=201)
    stopped = parse_ini((mod / "control.ini").read_text(encoding="utf-8"))
    assert stopped["run"] == "0"


def test_transient_stale_status_does_not_cancel_desktop_run_request(monkeypatch, tmp_path):
    import queue
    import mod_app

    class Bridge:
        def __init__(self, _path):
            self.writes = []
            self.info = {"status": "stale", "installed": True, "enabled": True,
                         "loaded": False, "message": "Mod 心跳过期"}

        def inspect(self):
            return self.info.copy()

        def write_control(self, run, settings=None):
            self.writes.append((run, settings))

    monkeypatch.setattr(mod_app, "ModBridge", Bridge)
    worker = mod_app.ModWorker(queue.Queue(), str(tmp_path / "PBZ.exe"), {})
    worker.run_requested = True
    worker.last_mod_check = -1
    worker.last_pulse = -1
    worker.pulse(1)
    assert worker.run_requested
    assert worker.bridge.writes[-1][0] is True
    assert '保持' in worker.message

    worker.bridge.info = {"status": "pending", "installed": True, "enabled": True,
                          "loaded": False, "message": "等待游戏 Mod"}
    worker.last_mod_check = -1
    worker.last_pulse = 0
    worker.pulse(2)
    assert worker.run_requested
    assert worker.bridge.writes[-1][0] is True


def test_desktop_stops_and_clears_control_on_explicit_conflict(monkeypatch, tmp_path):
    import queue
    import mod_app

    class Bridge:
        def __init__(self, _path):
            self.writes = []

        def inspect(self):
            return {"status": "conflict", "installed": True, "enabled": True,
                    "loaded": True, "message": "旧战斗 Mod 已启用"}

        def write_control(self, run, settings=None):
            self.writes.append(run)

    monkeypatch.setattr(mod_app, "ModBridge", Bridge)
    worker = mod_app.ModWorker(queue.Queue(), str(tmp_path / "PBZ.exe"), {})
    worker.run_requested = True
    worker.last_mod_check = -1
    worker.pulse(1)
    assert not worker.run_requested
    assert worker.bridge.writes == [False]


def test_desktop_clears_control_when_conflict_races_with_lease_renewal(monkeypatch, tmp_path):
    import queue
    import mod_app

    class Bridge:
        def __init__(self, _path):
            self.writes = []

        def inspect(self):
            return {"status": "running", "installed": True, "enabled": True,
                    "loaded": True, "running": True, "message": "connected"}

        def write_control(self, run, settings=None):
            self.writes.append(run)
            if run:
                raise RuntimeError("legacy combat mod conflict: PBZAutoGuard")

    monkeypatch.setattr(mod_app, "ModBridge", Bridge)
    worker = mod_app.ModWorker(queue.Queue(), str(tmp_path / "PBZ.exe"), {})
    worker.run_requested = True
    worker.last_mod_check = -1
    worker.pulse(1)
    assert not worker.run_requested
    assert worker.bridge.writes == [True, False]


def test_desktop_ignores_old_unreliable_perfect_counters():
    import mod_app
    assert mod_app._defense_feedback({'def_ok': '52', 'def_perfect': '52', 'def_fail': '0'}) == '结果未验证'
    result = mod_app._defense_feedback({'defense_checks_no_loss': '4',
                                      'defense_checks_damage': '2', 'defense_checks_unknown': '3',
                                      'defense_api_fail': '1', 'parry_lead_ms': '95'})
    assert result == '防住 4 / 被打 2（67%） · 未判定 3 · 提前量 95ms · 调用失败 1'
    assert '完美' not in result
    assert mod_app._defense_feedback({'defense_checks_no_loss': '0',
                                      'defense_checks_damage': '0',
                                      'defense_checks_unknown': '0'}) == '还没有拦下过攻击'
    assert mod_app._skill_feedback({'skill_state': 'resource_unknown'}) == '资源未识别'
    assert mod_app._skill_feedback({'skill_state': 'resource_empty', 'skill_charges': '0'}) == '资源不足 · 0'


def test_desktop_status_shows_loaded_version_and_real_observations(monkeypatch, tmp_path):
    import mod_app
    monkeypatch.setattr(mod_app, 'DATA', tmp_path / 'settings')
    monkeypatch.setattr(mod_app, 'load_path', lambda: str(tmp_path / 'PBZ.exe'))
    app = mod_app.App(smoke=True)
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args))
    try:
        info = {'message': 'Mod 已连接', 'status': 'running', 'installed': True,
                'enabled': True, 'loaded': True, 'running': True, 'mod_version': '0.16.2',
                'hp': '700', 'hp_max': '1000', 'target_distance': '1.5', 'enemy_count': '2',
                'attack_ok': '4', 'attack_no_damage': '2', 'attack_unverified': '8',
                'heal_ok': '2', 'heal_fail': '1', 'heal_last': 'heal',
                'defense_engine': 'Muspelion/PBZ-AutoParryDodge v1.1.0', 'defense_ready': '1',
                'defense_parry_ready': '1', 'defense_dodge_ready': '1',
                'defense_checks_no_loss': '4', 'defense_checks_damage': '2',
                'defense_checks_unknown': '3', 'defense_api_fail': '1',
                'skill_state': 'ready', 'skill_charges': '1', 'action': 'heal_wait_chain'}
        event = {'game_path': str(tmp_path / 'PBZ.exe'), 'mod': info,
                 'message': '辅助运行中', 'control_available': True, 'run_requested': True}
        app.apply_status(event)
        app.update()
        assert app.mode.get() == '版本不一致'
        assert '0.16.2' in app.status.get() and mod_app.VERSION in app.status.get()
        assert app.stop_button.enabled  # mismatch does not revoke the user's control
        assert '格挡、闪避就绪' in app.engine_status.get() and 'Muspelion' in app.engine_status.get()
        assert app.action.get() == '收招后喝药'
        assert app.healing.get() == '回血 2 · 无效果 1\nR 快捷药槽 · T 尚未触发'
        assert app.hit_rate.get() == '掉血 4 · 无变化 2\n血量未知 8'
        assert app.defense_rate.get() == '防住 4 / 被打 2（67%） · 未判定 3 · 调用失败 1'
        assert app.skill.get() == '可用 · 1'
        info.update(mod_version=mod_app.VERSION, defense_ready='0', defense_parry_ready='1',
                    defense_dodge_ready='0', defense_error='缺少闪避组件')
        app.apply_status(event)
        assert app.mode.get() != '版本不一致'
        assert '格挡就绪 · 闪避未就绪' in app.engine_status.get()
        assert '缺少闪避组件' in app.engine_status.get()
        assert not errors
        assert not app.worker.is_alive()
    finally:
        app.close()


def test_write_control_carries_defense_and_combo_choices(tmp_path):
    exe, mod = install(tmp_path)
    bridge = ModBridge(exe)
    bridge.write_control(True, {"blue_response": "dodge", "red_response": "guard",
                                "combo_enabled": False, "combo_preset": "swift"}, now=100)
    values = parse_ini((mod / "control.ini").read_text(encoding="utf-8"))
    assert values["blue_response"] == "dodge" and values["red_response"] == "guard"
    assert values["combo_enabled"] == "0"
    assert values["combo_preset"] == "swift"
    assert "lock_enabled" not in values  # movement / lock are owned by the player
    assert values['combat_mode'] == 'hold'
    assert values["attack_distance"] == "3.5"
    with pytest.raises(ValueError):
        bridge.write_control(True, {"blue_response": "parry"}, now=101)


def test_stop_hotkey_avoids_the_ue4ss_console_key():
    # UE4SS ConsoleEnabler binds F10; the assistant must not steal it.
    source = (Path(__file__).parent / "mod_app.py").read_text(encoding="utf-8")
    assert "1010" not in source
    assert "(0x78, 'stop')" in source


def test_new_lua_mod_runs_combat_on_the_game_thread():
    script = Path(__file__).parent / "mod-dev-v0.10" / "mod" / "PBZSmartCombat" / "Scripts" / "main.lua"
    source = script.read_text(encoding="utf-8")
    assert "LoopInGameThreadAfterFrames" in source
    assert "InjectInputVectorForAction" in source
    assert "IA_AttackL_Press" in source
    assert "IA_Stance" in source and "IA_Dodge" in source
    assert "IA_CombatArt" in source
    assert "IA_YaoHuLu" in source
    assert "IA_ChangeWeapon" in source
    assert "IA_ShadowArm" in source
    assert "ShieldOf" in source and "shadow_uses" in source


def test_new_desktop_entrypoint_does_not_import_old_combat_runtime():
    source = (Path(__file__).parent / "mod_app.py").read_text(encoding="utf-8")
    assert "RuntimeReader" not in source
    assert "CombatScheduler" not in source
    assert "InputSink" not in source
