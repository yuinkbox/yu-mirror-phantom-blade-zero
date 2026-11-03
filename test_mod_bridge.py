from pathlib import Path

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


def test_write_control_is_atomic_versioned_and_maps_settings(tmp_path):
    exe, mod = install(tmp_path)
    bridge = ModBridge(exe)
    request_id = bridge.write_control(True, {
        "heal_enabled": False, "attack_enabled": True, "guard_enabled": False,
        "dodge_enabled": True, "threshold": .6,
        "attack_distance": 4.25, "combo_preset": "pressure",
    }, now=100)
    values = parse_ini((mod / "control.ini").read_text(encoding="utf-8"))
    assert len(request_id) == 32 and values["request_id"] == request_id
    assert values["schema"] == "2" and values["run"] == "1"
    assert values["lease_until"] == "104"
    assert values["heal_enabled"] == "0" and values["attack_enabled"] == "1"
    assert values["guard_enabled"] == "0" and values["dodge_enabled"] == "1"
    assert values["heal_threshold"] == "0.6" and values["attack_distance"] == "4.25"
    assert values["combo_preset"] == "pressure"
    assert not list(mod.glob("control-*.tmp"))
    bridge.write_control(False, now=101)
    stopped = parse_ini((mod / "control.ini").read_text(encoding="utf-8"))
    assert stopped["run"] == "0" and stopped["lease_until"] == "105"


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


def test_write_control_carries_defense_and_combo_choices(tmp_path):
    exe, mod = install(tmp_path)
    bridge = ModBridge(exe)
    bridge.write_control(True, {"blue_response": "dodge", "red_response": "guard",
                                "combo_enabled": False, "combo_preset": "swift"}, now=100)
    values = parse_ini((mod / "control.ini").read_text(encoding="utf-8"))
    assert values["blue_response"] == "dodge" and values["red_response"] == "guard"
    assert values["combo_enabled"] == "0"
    assert values["combo_preset"] == "swift"
    assert values["lock_enabled"] == "1"
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
