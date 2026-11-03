from datetime import datetime, timedelta
from community_mods import inspect_autoguard


def fixture(tmp_path):
    exe=tmp_path/'platform'/'steam'/'PBZ.exe'
    ue=exe.parent/'ue4ss';mod=ue/'Mods'/'PBZAutoGuard'
    (mod/'Scripts').mkdir(parents=True)
    (mod/'Scripts'/'main.lua').write_text('-- dummy test fixture, never executed','utf-8')
    return exe,ue,mod


def test_missing_plugin_preserves_external_defense(tmp_path):
    assert not inspect_autoguard(tmp_path/'PBZ.exe')['enabled']


def test_installed_but_disabled_plugin_preserves_external_defense(tmp_path):
    exe,ue,mod=fixture(tmp_path)
    assert inspect_autoguard(exe)['status']=='disabled'


def test_enabled_plugin_needs_current_session_load_record(tmp_path):
    exe,ue,mod=fixture(tmp_path);(mod/'enabled.txt').touch()
    status=inspect_autoguard(exe,datetime.now().timestamp())
    assert status['enabled'] and status['status']=='pending'


def test_modlist_enable_is_detected_without_enabled_marker(tmp_path):
    exe,ue,mod=fixture(tmp_path)
    (ue/'Mods'/'mods.txt').write_text('; PBZAutoGuard : 1\nPBZAutoGuard : 1 ; enabled\n','utf-8')
    assert inspect_autoguard(exe)['enabled']


def test_old_session_log_does_not_claim_active_plugin(tmp_path):
    exe,ue,mod=fixture(tmp_path);(mod/'enabled.txt').touch()
    start=datetime.now();old=start-timedelta(hours=1)
    (ue/'UE4SS.log').write_text(f"[{old.isoformat(sep=' ')}] Mod 'PBZAutoGuard' has enabled.txt, starting mod.\n",'utf-8')
    info=inspect_autoguard(exe,start.timestamp())
    assert not info['loader_started'] and info['status']=='pending'


def test_runtime_plugin_keeps_ownership_after_marker_is_removed(tmp_path):
    exe,ue,mod=fixture(tmp_path);start=datetime.now()-timedelta(seconds=1);now=datetime.now()
    (ue/'UE4SS.log').write_text(f"[{now.isoformat(sep=' ')}] Mod 'PBZAutoGuard' has enabled.txt, starting mod.\n",'utf-8')
    info=inspect_autoguard(exe,start.timestamp())
    assert info['enabled'] and info['status']=='started'


def test_lua_error_blocks_the_assistant_instead_of_claiming_success(tmp_path):
    exe,ue,mod=fixture(tmp_path);start=datetime.now()-timedelta(seconds=1);stamp=datetime.now().isoformat(sep=' ')
    (ue/'UE4SS.log').write_text(f"[{stamp}] Mod 'PBZAutoGuard' has enabled.txt, starting mod.\n[{stamp}] [Lua] ERROR PBZAutoGuard/main.lua:1: failure\n",'utf-8')
    info=inspect_autoguard(exe,start.timestamp())
    assert info['enabled'] and info['status']=='error'
