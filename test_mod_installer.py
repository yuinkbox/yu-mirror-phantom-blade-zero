import json
from pathlib import Path

import pytest

from mod_installer import ModInstaller


def game_tree(tmp_path):
    game = tmp_path / 'platform' / 'steam'
    game.mkdir(parents=True)
    exe = game / 'PBZ.exe'
    exe.write_bytes(b'game')
    ue4ss = game / 'ue4ss'
    ue4ss.mkdir()
    (ue4ss / 'UE4SS.dll').write_bytes(b'loader')
    mods = ue4ss / 'Mods'
    mods.mkdir()
    original_mods = b'; preserved\r\nPBZAssist : 1 ; old\r\nPBZAutoCombat : 1\r\nOtherMod : 1\r\n'
    (mods / 'mods.txt').write_bytes(original_mods)
    for name in ('PBZAssist', 'PBZAutoCombat'):
        folder = mods / name
        folder.mkdir()
        (folder / 'enabled.txt').write_bytes(b'')
        (folder / 'keep.lua').write_text('-- legacy', encoding='utf-8')
    source = tmp_path / 'source' / 'PBZSmartCombat'
    (source / 'Scripts').mkdir(parents=True)
    (source / 'Scripts' / 'main.lua').write_text('-- test mod', encoding='utf-8')
    return exe, mods, source, original_mods


def test_install_disables_old_controllers_and_rollback_restores_exact_files(tmp_path):
    exe, mods, source, original = game_tree(tmp_path)
    installer = ModInstaller(exe, source)
    result = installer.install()
    new_mod = mods / 'PBZSmartCombat'
    assert (new_mod / 'enabled.txt').is_file()
    assert (new_mod / 'installation.json').is_file()
    assert b'PBZAssist : 0 ; old' in (mods / 'mods.txt').read_bytes()
    assert b'PBZAutoCombat : 0' in (mods / 'mods.txt').read_bytes()
    assert b'OtherMod : 1' in (mods / 'mods.txt').read_bytes()
    assert not (mods / 'PBZAssist' / 'enabled.txt').exists()
    assert (mods / 'PBZAssist' / 'keep.lua').is_file()
    assert installer.can_rollback()

    # Runtime hold-key transport must not make a managed install look edited.
    (new_mod / 'assist.ini').write_text('hold=0\n', encoding='utf-8')
    (new_mod / 'assist.tmp').write_text('hold=1\n', encoding='utf-8')
    assert installer.can_rollback()

    assert result['backup'].is_dir()
    installer.rollback()
    assert not new_mod.exists()
    assert (mods / 'mods.txt').read_bytes() == original
    for name in ('PBZAssist', 'PBZAutoCombat'):
        assert (mods / name / 'enabled.txt').is_file()
        assert (mods / name / 'keep.lua').is_file()
    manifest = json.loads((result['backup'] / 'migration.json').read_text('utf-8'))
    assert manifest['state'] == 'rolled_back'
    assert not installer.can_rollback()


def test_install_refuses_to_overwrite_existing_new_mod(tmp_path):
    exe, mods, source, _ = game_tree(tmp_path)
    existing = mods / 'PBZSmartCombat'
    existing.mkdir()
    (existing / 'user.txt').write_text('keep', encoding='utf-8')
    with pytest.raises(FileExistsError):
        ModInstaller(exe, source).install()
    assert (existing / 'user.txt').read_text('utf-8') == 'keep'


def test_rollback_refuses_to_overwrite_mods_txt_changed_after_install(tmp_path):
    exe, mods, source, _ = game_tree(tmp_path)
    installer = ModInstaller(exe, source)
    installer.install()
    with (mods / 'mods.txt').open('ab') as stream:
        stream.write(b'UserMod : 1\n')
    with pytest.raises(RuntimeError):
        installer.rollback()
    assert (mods / 'PBZSmartCombat' / 'Scripts' / 'main.lua').is_file()
    assert b'UserMod : 1' in (mods / 'mods.txt').read_bytes()


def test_rollback_preserves_user_edits_inside_installed_mod(tmp_path):
    exe, mods, source, _ = game_tree(tmp_path)
    installer = ModInstaller(exe, source)
    installer.install()
    script = mods / 'PBZSmartCombat' / 'Scripts' / 'main.lua'
    script.write_text('-- user edit', encoding='utf-8')
    with pytest.raises(RuntimeError):
        installer.rollback()
    assert script.read_text('utf-8') == '-- user edit'


def test_install_requires_existing_ue4ss_and_source(tmp_path):
    exe = tmp_path / 'PBZ.exe'
    exe.touch()
    with pytest.raises(FileNotFoundError, match='UE4SS'):
        ModInstaller(exe, tmp_path / 'source').install()


def test_upgrade_snapshots_modified_install_and_rollback_restores_it(tmp_path):
    exe, mods, source, original = game_tree(tmp_path)
    installer = ModInstaller(exe, source)
    result = installer.install()
    mod = mods / 'PBZSmartCombat'
    (mod / 'status-a.ini').write_text('heartbeat=1\n', encoding='utf-8')
    (mod / 'diag.txt').write_text('version=0.10.0\n', encoding='utf-8')
    (mod / 'Scripts' / 'main.lua').write_text('-- locally modified old mod', encoding='utf-8')
    (source / 'Scripts' / 'main.lua').write_text('-- upgraded mod', encoding='utf-8')
    assert installer.can_upgrade()
    upgraded = installer.upgrade()
    assert upgraded['changed']
    assert (mod / 'Scripts' / 'main.lua').read_text('utf-8') == '-- upgraded mod'
    assert (mod / 'installation.json').is_file()
    assert installer.can_rollback()

    (mod / 'Scripts' / 'main.lua').write_text('-- edited new mod', encoding='utf-8')
    assert not installer.can_rollback()
    with pytest.raises(RuntimeError, match='新 Mod 文件'):
        installer.rollback()
    assert (mod / 'Scripts' / 'main.lua').read_text('utf-8') == '-- edited new mod'
    (mod / 'Scripts' / 'main.lua').write_text('-- upgraded mod', encoding='utf-8')

    installer.rollback()
    assert (mod / 'Scripts' / 'main.lua').read_text('utf-8') == '-- locally modified old mod'
    assert (mod / 'status-a.ini').read_text('utf-8') == 'heartbeat=1\n'
    assert (mod / 'diag.txt').read_text('utf-8') == 'version=0.10.0\n'
    assert (mods / 'mods.txt').read_bytes() != original
    assert installer.can_upgrade()

    (source / 'Scripts' / 'main.lua').write_text('-- next upgrade', encoding='utf-8')
    assert installer.upgrade()['changed']
    installer.rollback()
    assert (mod / 'Scripts' / 'main.lua').read_text('utf-8') == '-- locally modified old mod'


def test_upgrade_with_identical_source_reports_no_change(tmp_path):
    exe, mods, source, _ = game_tree(tmp_path)
    installer = ModInstaller(exe, source)
    installer.install()
    assert installer.upgrade()['changed'] is False


def test_upgrade_allows_hash_drift_only_for_owned_install_and_snapshots_it(tmp_path):
    exe, mods, source, _ = game_tree(tmp_path)
    installer = ModInstaller(exe, source)
    installer.install()
    (mods / 'PBZSmartCombat' / 'Scripts' / 'main.lua').write_text('-- user edit', encoding='utf-8')
    (source / 'Scripts' / 'main.lua').write_text('-- next', encoding='utf-8')
    assert installer.can_upgrade()
    assert installer.upgrade()['changed']
    installer.rollback()
    assert (mods / 'PBZSmartCombat' / 'Scripts' / 'main.lua').read_text('utf-8') == '-- user edit'


def test_upgrade_rejects_marker_that_does_not_match_managed_backup(tmp_path):
    exe, mods, source, _ = game_tree(tmp_path)
    installer = ModInstaller(exe, source)
    installer.install()
    marker = mods / 'PBZSmartCombat' / 'installation.json'
    marker.write_text(json.dumps({'managed_by': 'PBZSmartCombat', 'backup': 'other'}), 'utf-8')
    assert not installer.can_upgrade()
    with pytest.raises(RuntimeError, match='不匹配'):
        installer.upgrade()


def test_upgrade_replace_failure_restores_original_directory(tmp_path, monkeypatch):
    exe, mods, source, _ = game_tree(tmp_path)
    installer = ModInstaller(exe, source)
    installer.install()
    original = mods / 'PBZSmartCombat' / 'Scripts' / 'main.lua'
    original.write_text('-- modified old version', encoding='utf-8')
    (source / 'Scripts' / 'main.lua').write_text('-- next', encoding='utf-8')
    replace = Path.replace

    def fail_staging_replace(path, target):
        if path.name.startswith('.PBZSmartCombat-upgrading-'):
            raise OSError('simulated replace failure')
        return replace(path, target)

    monkeypatch.setattr(Path, 'replace', fail_staging_replace)
    with pytest.raises(OSError, match='simulated'):
        installer.upgrade()
    assert original.read_text('utf-8') == '-- modified old version'
    assert installer.can_upgrade()
